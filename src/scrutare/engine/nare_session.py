"""External nare runtime inspection and one admitted session lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import stat
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict, TypeVar
from urllib.parse import urlsplit

from scrutare import __version__
from scrutare.config import ModelRail, ModelSettings
from scrutare.engine.assessment_inputs import AssessmentInput
from scrutare.engine.budgets import BudgetLease, ReviewBudgetLedger
from scrutare.engine.debate_inputs import DebateInput
from scrutare.engine.persona_inputs import PersonaReanchorInput, PersonaReviewInput
from scrutare.engine.reanchor import reanchor_schema
from scrutare.engine.review_inputs import ReviewInputError, validate_prepared_inputs
from scrutare.engine.session_artifacts import SessionArtifactError, write_owned_json
from scrutare.engine.session_models import (
    NareCapability,
    NareRuntime,
    ReanchorOutcome,
    SessionOutcome,
    SessionStatus,
    TokenUsage,
)
from scrutare.engine.session_output import (
    CitationCorrectionOffered,
    DecodedReanchorSession,
    DecodedSession,
    ProtocolCorrectionOffered,
    SessionProtocolError,
    constrained_turns,
    decode_citation_correction,
    decode_cost_event,
    decode_reanchor_session,
    decode_session,
    findings_schema,
)
from scrutare.findings.citation_correction import (
    citation_correction_enabled,
    citation_correction_prompt,
    citation_correction_schema,
)
from scrutare.findings.evidence import CitationValidationError, evidence_version
from scrutare.findings.models import FindingError
from scrutare.findings.models import artifact_data as asdict


class SessionRuntimeError(ValueError):
    """Safe external runtime inspection diagnostics, without process output."""


def _executable(runtime: NareRuntime) -> Path:
    if not isinstance(runtime, NareRuntime):
        raise SessionRuntimeError("runtime: expected validated operational limits")
    found = shutil.which(str(runtime.executable))
    if found is None:
        raise SessionRuntimeError("executable: cannot locate an executable nare runtime")
    return Path(found).absolute()


def _version(value: str) -> None:
    if re.fullmatch(r"[0-9]{4}\.[0-9]{1,2}\.[0-9]+", value) is None:
        raise SessionRuntimeError("version: unrecognized nare release")
    if tuple(map(int, value.split("."))) < (2026, 10, 0):
        raise SessionRuntimeError("version: nare release must be at least 2026.10.0")


def _environment(home: Path, temporary: Path, provider: str | None = None) -> dict[str, str]:
    # An allowlist also removes PYTHONPATH, proxy settings, GitHub auth and every NARE_* control.
    environment = {name: os.environ[name] for name in
                   ("PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR")
                   if name in os.environ}
    credential = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider or "")
    if credential is not None and credential in os.environ:
        environment[credential] = os.environ[credential]
    environment.update(HOME=str(home), TMPDIR=str(temporary), TMP=str(temporary),
                       TEMP=str(temporary))
    return environment


async def _terminate(process: asyncio.subprocess.Process) -> None:
    # Signal the whole owned group, including descendants holding either capture pipe open.
    for action in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, action)
        except ProcessLookupError:
            pass
        if action == signal.SIGTERM:
            await asyncio.sleep(0.2)
    await process.wait()


_T = TypeVar("_T")


async def _finish(task: asyncio.Future[_T]) -> _T:
    """Complete bounded cleanup even if a caller cancels again during its grace period."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            continue


def _contract_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise SessionRuntimeError("contract: duplicate metadata field")
        document[key] = value
    return document


async def _start_process(
    argv: list[str], environment: dict[str, str], cwd: Path,
) -> tuple[asyncio.subprocess.Process, bool]:
    """Own startup through cancellation, including a raced no-child spawn error."""
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=environment, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    ))
    try:
        return await asyncio.shield(spawning), False
    except asyncio.CancelledError:
        try:
            return await _finish(spawning), True
        except OSError:
            raise asyncio.CancelledError from None


async def _cleanup_capture(
    process: asyncio.subprocess.Process, capture: asyncio.Future[_T],
) -> _T:
    """Reap the owned group and finish either capture strategy before settlement."""
    await _finish(asyncio.create_task(_terminate(process)))
    return await _finish(capture)


async def _wait_capture(
    process: asyncio.subprocess.Process, capture: asyncio.Future[_T], timeout: float, *,
    cancelled: bool,
) -> _T:
    """Preserve capture while applying the common liveness and cancellation policy."""
    if cancelled:
        await _cleanup_capture(process, capture)
        raise asyncio.CancelledError
    try:
        return await asyncio.wait_for(asyncio.shield(capture), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await _cleanup_capture(process, capture)
        raise


async def _probe(executable: Path, args: tuple[str, ...], root: Path,
                 runtime: NareRuntime) -> bytes:
    process, cancelled = await _start_process(
        [str(executable), *args], _environment(root, root), root,
    )
    capture = asyncio.create_task(process.communicate())
    stdout, _ = await _wait_capture(process, capture, runtime.timeout_seconds, cancelled=cancelled)
    if process.returncode != 0:
        raise SessionRuntimeError("inspection: nare capability command failed")
    return stdout


# nare 2026.10.7 moved to contract 2, which only changes when `ask` blocks. Sessions here
# offer only `read`, so both contracts carry the same evidence; each from its own releases.
_CONTRACT_2_FROM = (2026, 10, 7)
SUPPORTED_CONTRACTS = (1, 2)


def _speaks(version: str, contract: object) -> bool:
    if type(contract) is not int or contract not in SUPPORTED_CONTRACTS:
        return False
    return (tuple(map(int, version.split("."))) >= _CONTRACT_2_FROM) == (contract == 2)


def _structured_output(contract: dict[str, object]) -> tuple[str, ...]:
    """The json_schema modes nare advertises for its OpenAI rail, or none.

    An unfamiliar entry advertises nothing rather than failing inspection: waves that never
    ask for the constraint must not depend on it, and require_structured_output refuses one
    that does.
    """
    entry = contract.get("structured_output")
    if (not isinstance(entry, dict) or not isinstance(entry.get("modes"), list)
            or not isinstance(entry.get("providers"), list)
            or entry.get("flag") != "--structured-output" or "openai" not in entry["providers"]):
        return ()
    return tuple(mode for mode in entry["modes"] if mode == "json_schema")


async def inspect_nare_runtime(runtime: NareRuntime) -> NareCapability:
    """Inspect external version and contract without constructing a provider or session."""
    executable = _executable(runtime)
    try:
        with tempfile.TemporaryDirectory(prefix="scrutare-inspect-") as directory:
            root = Path(directory)
            version = (await _probe(executable, ("--version",), root, runtime)).decode().strip()
            _version(version)
            contract = json.loads(await _probe(executable, ("contract",), root, runtime),
                                  object_pairs_hook=_contract_pairs)
            if (not isinstance(contract, dict) or not _speaks(version, contract.get("contract"))
                    or contract.get("nare") != version):
                raise SessionRuntimeError(
                    "contract: expected contract 1 (before 2026.10.7) or 2 and matching release")
            return NareCapability(version, contract["contract"], _structured_output(contract))
    except SessionRuntimeError:
        raise
    except (OSError, ValueError, asyncio.TimeoutError, RecursionError):
        raise SessionRuntimeError(
            "inspection: cannot verify executable version and contract"
        ) from None


def require_structured_output(models: ModelSettings, capability: NareCapability) -> None:
    """Refuse a wave before any session when a rail asks for a constraint nare cannot send."""
    for rail in (models.default, *(rail for _, rail in models.overrides)):
        mode = rail.structured_output
        if mode is not None and mode not in getattr(capability, "structured_output", ()):
            raise SessionRuntimeError(
                f"capability: nare {capability.version} cannot send structured output {mode}; "
                "use a nare release that advertises it (2026.10.12 or newer)")


def _rail(rail: ModelRail) -> None:
    if (not isinstance(rail, ModelRail) or rail.provider not in ("anthropic", "openai")
            or not isinstance(rail.model, str) or not rail.model.strip()
            or "\x00" in rail.model):
        raise SessionRuntimeError("rail: expected an explicit provider and nonempty model")
    if rail.structured_output is not None and (
            rail.structured_output != "json_schema" or rail.provider != "openai"):
        raise SessionRuntimeError("rail.structured_output: expected json_schema on openai")
    if rail.max_output_tokens is not None and (
            type(rail.max_output_tokens) is not int or rail.max_output_tokens <= 0):
        raise SessionRuntimeError("rail.max_output_tokens: expected a positive integer or null")
    if rail.base_url is not None:
        if not isinstance(rail.base_url, str):
            raise SessionRuntimeError("rail.base_url: expected an HTTP(S) URL or null")
        try:
            valid = (isinstance(rail.base_url, str) and not any(
                char.isspace() or char == "\x00" for char in rail.base_url
            ))
            url = urlsplit(rail.base_url)
            valid = valid and url.scheme in ("http", "https") and bool(url.hostname)
            url.port
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise SessionRuntimeError("rail.base_url: expected an HTTP(S) URL or null")


@contextmanager
def _attempt(path: Path, prepared_root: Path, persona: str) -> Iterator[int]:
    """Pin a validated private attempt through no-follow ancestor descriptors."""
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or path.parent.parent.parent != prepared_root.parent
            or path.parent.parent.name != "sessions" or path.parent.name != persona
            or re.fullmatch(r"attempt-(?!0000)[0-9]{4}", path.name) is None):
        raise SessionArtifactError("artifact_directory: expected the owned private attempt")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(path.parts[1:]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            if index >= len(path.parts) - 4:
                info = os.fstat(directory)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise SessionArtifactError("artifact_directory: expected owner-only ancestors")
        yield directory
    finally:
        os.close(directory)


def _new_capture(directory: int, name: str) -> BinaryIO:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    return os.fdopen(descriptor, "wb", buffering=0)


def _session_document(directory: int, name: str = "session.json") -> bytes | None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise SessionArtifactError("session: expected private regular saved evidence")
        return stream.read()


@dataclass
class _Evidence:
    usage: TokenUsage
    stdout: bytes = b""
    corrupt: bool = False
    started: bool = False

    def event(self, line: bytes, ledger: ReviewBudgetLedger, lease: BudgetLease) -> None:
        if not line.strip():
            return
        try:
            usage = decode_cost_event(line)
            if usage is not None:
                cumulative = TokenUsage(*(getattr(self.usage, field) + getattr(usage, field)
                                          for field in
                                          ("input", "output", "cache_read", "cache_write")))
                ledger.observe(lease, cumulative)
                self.usage = cumulative
        except (SessionProtocolError, ValueError):
            self.corrupt = True
            ledger.mark_uncertain(lease)


async def _stdout(stream: asyncio.StreamReader, destination: BinaryIO, evidence: _Evidence,
                  ledger: ReviewBudgetLedger, lease: BudgetLease) -> None:
    chunks: list[bytes] = []
    pending = b""
    try:
        while chunk := await stream.read(65536):
            destination.write(chunk)
            chunks.append(chunk)
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                evidence.event(line, ledger, lease)
        if pending:
            evidence.event(pending, ledger, lease)
    except OSError:
        ledger.mark_uncertain(lease)
        raise
    finally:
        evidence.stdout = b"".join(chunks)


async def _stderr(stream: asyncio.StreamReader, destination: BinaryIO,
                  ledger: ReviewBudgetLedger, lease: BudgetLease) -> None:
    try:
        while chunk := await stream.read(65536):
            destination.write(chunk)
    except OSError:
        ledger.mark_uncertain(lease)
        raise


async def _execute(argv: list[str], environment: dict[str, str], cwd: Path,
                   runtime: NareRuntime, stdout: BinaryIO, stderr: BinaryIO,
                   evidence: _Evidence, ledger: ReviewBudgetLedger,
                   lease: BudgetLease) -> int:
    process, cancelled = await _start_process(argv, environment, cwd)
    evidence.started = True
    assert process.stdout is not None and process.stderr is not None
    drainage = asyncio.gather(_stdout(process.stdout, stdout, evidence, ledger, lease),
                              _stderr(process.stderr, stderr, ledger, lease), process.wait(),
                              return_exceptions=True)
    try:
        results = await _wait_capture(
            process, drainage, runtime.timeout_seconds, cancelled=cancelled,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
    except OSError:
        await _cleanup_capture(process, drainage)
        raise
    assert process.returncode is not None
    return process.returncode


class _OutcomeMetadata(TypedDict):
    persona: str
    status: SessionStatus
    reason: str
    output_available: bool
    usage: TokenUsage
    accounting_complete: bool
    allocated_tokens: int
    overshoot_tokens: int
    session_id: str | None
    artifact_directory: Path
    invocation_limit: int
    nare_status: str | None
    stop_reason: str | None
    exit_code: int | None


def _outcome(lease: BudgetLease, path: Path, evidence: _Evidence, *, status: SessionStatus,
             reason: str, complete: bool, decoded: DecodedSession | DecodedReanchorSession | None,
             exit_code: int | None, purpose: Literal["review", "reanchor"],
             ) -> SessionOutcome | ReanchorOutcome:
    metadata: _OutcomeMetadata = {
        "persona": lease.persona, "status": status, "reason": reason,
        "output_available": decoded is not None and decoded.output_available,
        "usage": evidence.usage, "accounting_complete": complete,
        "allocated_tokens": lease.allocated_tokens,
        "overshoot_tokens": max(0, evidence.usage.total - lease.limit_tokens),
        "session_id": None if decoded is None else decoded.session_id,
        "artifact_directory": path, "invocation_limit": lease.limit_tokens,
        "nare_status": None if decoded is None else decoded.status,
        "stop_reason": None if decoded is None else decoded.stop_reason, "exit_code": exit_code,
    }
    if purpose == "reanchor":
        assert decoded is None or isinstance(decoded, DecodedReanchorSession)
        return ReanchorOutcome(
            corrections=() if decoded is None else decoded.corrections, **metadata,
        )
    assert decoded is None or isinstance(decoded, DecodedSession)
    return SessionOutcome(findings=() if decoded is None else decoded.findings, **metadata)


def _offers_citation_correction(descriptor: object) -> bool:
    """Only an opted-in discovery session: never an assessor, discussion, chair or reanchor."""
    return (type(descriptor) is PersonaReviewInput
            and citation_correction_enabled(descriptor.inputs.root))


def _rail_argv(rail: ModelRail, capability: NareCapability) -> list[str]:
    argv = ["--provider", rail.provider, "--model=" + rail.model]
    if (rail.provider == "openai"
            and tuple(map(int, capability.version.split("."))) >= (2026, 10, 4)):
        argv.append("--stream")
    if rail.base_url is not None:
        argv.extend(("--base-url", rail.base_url))
    return argv


def _rail_options(rail: ModelRail) -> list[str]:
    argv: list[str] = []
    if rail.structured_output is not None:
        argv.extend(("--structured-output", rail.structured_output))
    if rail.max_output_tokens is not None:
        argv.extend(("--max-tokens", str(rail.max_output_tokens)))
    return argv


async def _correct_citations(
    offered: CitationCorrectionOffered | ProtocolCorrectionOffered,
    descriptor: PersonaReviewInput, rail: ModelRail,
    lease: BudgetLease, *, ledger: ReviewBudgetLedger, directory: int, artifact_directory: Path,
    executable: Path, runtime: NareRuntime, capability: NareCapability, evidence: _Evidence,
    deadline: float, record: dict[str, object],
) -> DecodedSession:
    """Resume the refused session for one tool-free correction turn within its own budget.

    The turn is recorded before it starts. Its answer is validated exactly as the original
    was; a correction that gives no answer leaves the original refusal standing.
    """
    root = descriptor.inputs.root
    protocol = isinstance(offered, ProtocolCorrectionOffered)
    stem = "protocol-correction" if protocol else "citation-correction"
    if isinstance(offered, ProtocolCorrectionOffered):
        from scrutare.findings.protocol_correction import protocol_correction_prompt
        prompt = protocol_correction_prompt(offered.defects, offered.context)
        schema = offered.context.schema
    else:
        prompt = citation_correction_prompt(offered.defects)
        schema = citation_correction_schema(offered.defects)
    system = descriptor.persona.system_prompt
    argv = [str(executable), "run", prompt, "--tools", "none", "--root", str(root),
            "--system=" + system, *_rail_argv(rail, capability),
            "--jsonl", "--yes", "--contract", str(capability.contract),
            "--schema", str(artifact_directory / (stem + ".schema.json")),
            "--budget-tokens", str(lease.limit_tokens),
            "--resume", str(artifact_directory / "session.json"),
            "--session", str(artifact_directory / "correction-session.json"),
            "--max-turns", "1", *_rail_options(rail)]
    # Recorded as offered before anything is written, so any later failure says so.
    record.update(offered=True, defects=len(offered.defects), outcome="failed")
    write_owned_json(artifact_directory / (stem + ".schema.json"),
                     schema, prepared_root=root)
    write_owned_json(artifact_directory / (stem + ".json"), {
        "schema_version": 1, "scrutare_version": __version__,
        "session_key": lease.session_key,
        "refusal": {"reason": offered.reason, "citation_reason": offered.citation_reason},
        "defects": [defect.to_dict() for defect in offered.defects],
        "prompt": prompt, "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "argv": argv, "max_turns": 1, "invocation_limit": lease.limit_tokens,
    }, prepared_root=root)
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError
    correction = _Evidence(evidence.usage, started=True)
    try:
        with ExitStack() as stack:
            stdout = stack.enter_context(_new_capture(directory, "correction.stdout.jsonl"))
            stderr = stack.enter_context(_new_capture(directory, "correction.stderr.txt"))
            exit_code = await _execute(
                argv, _environment(artifact_directory / "home", artifact_directory / "tmp",
                                   rail.provider), artifact_directory / "cwd",
                replace(runtime, timeout_seconds=remaining), stdout, stderr, correction,
                ledger, lease)
        record["exit_code"] = exit_code
    finally:
        # Usage is cumulative across both turns of the session, whatever happened.
        evidence.usage = correction.usage
        evidence.corrupt = evidence.corrupt or correction.corrupt
    saved = _session_document(directory, "correction-session.json")
    if correction.corrupt:
        raise SessionProtocolError("session: corrupted correction evidence")
    # A constrained-turn record must match the rail, as in the first stream.
    constrained_turns(correction.stdout, rail.structured_output)
    validate_prepared_inputs(descriptor.inputs)
    try:
        decoded = decode_citation_correction(
            correction.stdout, saved, offered=offered, persona=lease.persona,
            exit_code=exit_code, expected_limit=lease.limit_tokens, expected_root=root,
            candidate_namespace=str(artifact_directory),
            discard_cut_off=rail.max_output_tokens is not None)
    except CitationValidationError:
        record["outcome"] = "refused"
        raise
    except FindingError:
        record["outcome"] = "refused"
        raise SessionProtocolError("citation correction: refused corrected answer") from None
    evidence.usage = decoded.usage
    if decoded.status != "done" or not decoded.output_available:
        record["outcome"] = "no_answer"
        if offered.citation_reason is not None:
            raise CitationValidationError(offered.citation_reason)
        raise SessionProtocolError("citation correction: no corrected answer")
    # "corrected" is recorded by the caller once the session has completed with it.
    return decoded


async def _run_session(
    descriptor: PersonaReviewInput | PersonaReanchorInput, rail: ModelRail, lease: BudgetLease, *,
    ledger: ReviewBudgetLedger, artifact_directory: Path, runtime: NareRuntime,
    capability: NareCapability, purpose: Literal["review", "reanchor"],
) -> SessionOutcome | ReanchorOutcome:
    """Execute one fresh lease, retaining reported lower bounds and private raw evidence.

    Timeout and cancellation are liveness limits. Token bounds are passed to nare for
    after-turn enforcement; admitted internal turns may overshoot. Provider failure
    counters describe completed responses and do not prove interrupted-call billing.
    """
    try:
        ledger.observe(lease, lease.baseline)
    except (ValueError, AttributeError):
        raise SessionRuntimeError("lease: expected an active unobserved ledger lease") from None
    evidence = _Evidence(lease.baseline)
    decoded: DecodedSession | DecodedReanchorSession | None = None
    status: SessionStatus = "failed"
    reason = "invocation"
    complete = True
    exit_code: int | None = None
    cancelled = False
    prepared_root: Path | None = None
    binding: dict[str, object] = {}
    constrained: int | None = None
    correction: dict[str, object] | None = None
    answered = False
    protocol = None
    correction_key = "citation_correction"
    deadline = asyncio.get_running_loop().time() + getattr(runtime, "timeout_seconds", 0)
    try:
        expected_descriptor = PersonaReanchorInput if purpose == "reanchor" else PersonaReviewInput
        if (not isinstance(descriptor, expected_descriptor)
                or descriptor.persona.name != lease.persona
                or lease.baseline != TokenUsage()):
            raise SessionRuntimeError("session: expected a fresh matching persona descriptor")
        prepared_root = descriptor.inputs.root
        if isinstance(descriptor, PersonaReanchorInput):
            binding = {"purpose": "reanchor",
                       "requests": [asdict(request) for request in descriptor.requests]}
        reason = "inputs"
        validate_prepared_inputs(descriptor.inputs)
        input_args = descriptor.nare_input_args()
        system = descriptor.persona.system_prompt
        if not isinstance(system, str) or not system.strip() or "\x00" in system:
            raise ReviewInputError("inputs: invalid persona system prompt")
        reason = "invocation"
        _rail(rail)
        executable = _executable(runtime)
        if not isinstance(capability, NareCapability):
            raise SessionRuntimeError("capability: expected an inspected runtime")
        _version(capability.version)
        if not _speaks(capability.version, capability.contract):
            raise SessionRuntimeError("capability: expected an inspected supported contract")
        if (rail.structured_output is not None
                and rail.structured_output not in capability.structured_output):
            raise SessionRuntimeError(
                f"capability: nare {capability.version} cannot send structured output "
                f"{rail.structured_output}")
        if purpose == "reanchor" and lease.session_key != f"{lease.persona}/attempt-0002":
            raise SessionRuntimeError("session_key: expected the fresh correction identity")
        from scrutare.config import load_config
        from scrutare.engine.discussion_inputs import DiscussionInput
        protocol_enabled = (purpose == "review" and not isinstance(descriptor, PersonaReanchorInput)
            and (not isinstance(descriptor, DebateInput) or isinstance(descriptor, DiscussionInput))
            and load_config(prepared_root.parent / "config.yaml").findings.protocol_correction)
        if protocol_enabled:
            correction_key = "protocol_correction"
            correction = {"offered": False, "defects": 0, "outcome": None, "exit_code": None}
        elif purpose == "review" and _offers_citation_correction(descriptor):
            correction = {"offered": False, "defects": 0, "outcome": None, "exit_code": None}
        reason = "artifacts"
        if purpose == "reanchor" and artifact_directory.name != "attempt-0002":
            raise SessionArtifactError("artifact_directory: expected the correction attempt")
        with _attempt(artifact_directory, prepared_root, lease.persona) as directory:
            if os.listdir(directory):
                raise SessionArtifactError("artifact_directory: expected an unused attempt")
            for name in ("cwd", "home", "tmp"):
                os.mkdir(name, mode=0o700, dir_fd=directory)
            argv = [str(executable), "run", *input_args, "--system=" + system,
                    *_rail_argv(rail, capability)]
            schema_name = ("reanchor.schema.json" if purpose == "reanchor"
                           else "findings.schema.json")
            schema = (descriptor.output_schema()
                      if isinstance(descriptor, (DebateInput, AssessmentInput)) else
                      reanchor_schema() if purpose == "reanchor" else
                      findings_schema(evidence_version=evidence_version(prepared_root)))
            if protocol_enabled:
                from scrutare.findings.protocol_correction import protocol_context
                protocol = protocol_context(schema, input_args[0])
            argv.extend(("--jsonl", "--yes", "--contract", str(capability.contract), "--schema",
                         str(artifact_directory / schema_name), "--budget-tokens",
                         str(lease.limit_tokens), "--session",
                         str(artifact_directory / "session.json"),
                         "--max-turns", str(runtime.max_turns)))
            argv.extend(_rail_options(rail))
            write_owned_json(artifact_directory / schema_name, schema,
                             prepared_root=prepared_root)
            write_owned_json(artifact_directory / "invocation.json", {
                "schema_version": 1, "scrutare_version": __version__,
                "nare_version": capability.version, "contract": capability.contract,
                "persona": lease.persona, "session_key": lease.session_key, **binding,
                "system_prompt_sha256": sha256(system.encode("utf-8")).hexdigest(),
                "task_prompt_sha256": sha256(input_args[0].encode("utf-8")).hexdigest(),
                "argv": argv, "rail": {"provider": rail.provider, "model": rail.model,
                                        "base_url": rail.base_url,
                                        **({"structured_output": rail.structured_output}
                                           if rail.structured_output is not None else {}),
                                        **({"max_output_tokens": rail.max_output_tokens}
                                           if rail.max_output_tokens is not None else {})},
                "allocated_tokens": lease.allocated_tokens, "invocation_limit": lease.limit_tokens,
                "max_turns": runtime.max_turns, "timeout_seconds": runtime.timeout_seconds,
            }, prepared_root=prepared_root)
            with ExitStack() as stack:
                stdout = stack.enter_context(_new_capture(directory, "stdout.jsonl"))
                stderr = stack.enter_context(_new_capture(directory, "stderr.txt"))
                reason = "launch"
                complete = False
                deadline = asyncio.get_running_loop().time() + runtime.timeout_seconds
                exit_code = await _execute(
                    argv, _environment(artifact_directory / "home", artifact_directory / "tmp",
                                       rail.provider), artifact_directory / "cwd", runtime,
                    stdout, stderr, evidence, ledger, lease,
                )
            reason = "protocol"
            saved = _session_document(directory)
            if evidence.corrupt:
                raise SessionProtocolError("session: corrupted event evidence")
            constrained = constrained_turns(evidence.stdout, rail.structured_output)
            # Nothing nare kept from a session cut off at the cap is parsed or validated.
            capped = rail.max_output_tokens is not None
            if isinstance(descriptor, PersonaReanchorInput):
                try:
                    validate_prepared_inputs(descriptor.inputs)
                except ReviewInputError:
                    ledger.mark_uncertain(lease)
                    raise
                decoded = decode_reanchor_session(
                    evidence.stdout, saved, descriptor=descriptor,
                    exit_code=exit_code, expected_limit=lease.limit_tokens,
                    discard_cut_off=capped,
                )
            else:
                try:
                    decoded = decode_session(
                        evidence.stdout, saved, persona=lease.persona, exit_code=exit_code,
                        expected_limit=lease.limit_tokens, expected_root=prepared_root,
                        descriptor=(descriptor if isinstance(descriptor,
                                                             (DebateInput, AssessmentInput))
                                    else None),
                        evidence_version=evidence_version(prepared_root),
                        candidate_namespace=str(artifact_directory), discard_cut_off=capped,
                        offer_citation_correction=correction is not None and protocol is None,
                        protocol_context=protocol)
                except (CitationCorrectionOffered, ProtocolCorrectionOffered) as offered:
                    assert correction is not None
                    first = offered.evidence
                    if isinstance(offered, ProtocolCorrectionOffered) and (
                            first.nare_version != capability.version
                            or first.contract != capability.contract
                            or first.status in ("done", "blocked")
                            and first.stop_reason == "budget" and not first.partial):
                        raise SessionProtocolError("session: invalid original terminal state")
                    # Every usage seen so far is in the ledger; the turn continues from it.
                    ledger.observe(lease, offered.evidence.usage)
                    evidence.usage = offered.evidence.usage
                    reason, complete = "launch", False
                    decoded = await _correct_citations(
                        offered, descriptor, rail, lease, ledger=ledger, directory=directory,
                        artifact_directory=artifact_directory, executable=executable,
                        runtime=runtime, capability=capability, evidence=evidence,
                        deadline=deadline, record=correction)
                    reason, answered = "protocol", True
            if decoded.status is not None and (
                    decoded.nare_version != capability.version
                    or decoded.contract != capability.contract):
                raise SessionProtocolError("capability: saved session differs from runtime")
            if (decoded.status in ("done", "blocked") and decoded.stop_reason == "budget"
                    and not decoded.partial):
                raise SessionProtocolError("status: unsupported terminal budget stop")
            ledger.observe(lease, decoded.usage)
            evidence.usage = decoded.usage
            complete = decoded.status != "error" or decoded.stop_reason == "budget"
            if capped and decoded.cut_off:
                # A response cut off at the configured cap is never an answer, whatever nare
                # kept from earlier turns (decoding discarded it). It reported its usage. The
                # cap is the reason even when the same turn also exhausted the budget: the
                # cut-off is not an overrun, so no overrun fallback applies to it.
                status, reason, complete = "failed", "output_cap", True
            elif decoded.status == "done":
                status, reason = ("partial", "budget") if decoded.partial else ("complete", "done")
            elif decoded.status == "error" and decoded.stop_reason == "budget":
                status, reason = "partial", "budget"
            elif decoded.status == "blocked":
                reason = "blocked"
            else:
                reason = "not_started" if decoded.status is None else "provider"
            validate_prepared_inputs(descriptor.inputs)
            if answered and correction is not None and status in ("complete", "partial"):
                correction["outcome"] = "corrected"
    except asyncio.CancelledError:
        cancelled, reason, complete = True, "cancelled", not evidence.started
    except asyncio.TimeoutError:
        reason, complete = "timeout", False
    except ReviewInputError:
        reason = "inputs"
        status = "failed"
    except SessionArtifactError:
        reason = "artifacts"
        status = "failed"
    except SessionRuntimeError:
        status = "failed"
    except OSError:
        # Spawn errors cannot have completed a model turn. Capture write/read errors can.
        if not evidence.started:
            complete = True
        status = "failed"
    except CitationValidationError as error:
        reason, status, complete = "citation_validation", "failed", False
        if prepared_root is not None:
            try:
                write_owned_json(artifact_directory / "citation-validation.json", {
                    "schema_version": 2, "status": "unsupported", "reason": error.reason,
                    "claim_truth": "not_assessed",
                }, prepared_root=prepared_root)
            except SessionArtifactError:
                reason = "artifacts"
    except (SessionProtocolError, ValueError, TypeError, OverflowError, RecursionError):
        reason, status, complete = "protocol", "failed", False
    outcome = _outcome(lease, artifact_directory, evidence, status=status, reason=reason,
                       complete=complete, decoded=decoded, exit_code=exit_code, purpose=purpose)
    ledger.settle(lease, evidence.usage, complete)
    if prepared_root is not None:
        try:
            structured: dict[str, object] = {}
            if isinstance(rail, ModelRail) and rail.structured_output is not None:
                if constrained is None and evidence.started:
                    # A session that stopped before decoding counts the constrained turns nare
                    # recorded; each is recorded before its request, so one may not have finished.
                    constrained = constrained_turns(evidence.stdout, rail.structured_output,
                                                    strict=False)
                structured = {"structured_output": {"mode": rail.structured_output,
                                                    "constrained_turns": constrained}}
            if isinstance(rail, ModelRail) and rail.max_output_tokens is not None:
                structured["max_output_tokens"] = {"limit": rail.max_output_tokens,
                                                   "reached": outcome.reason == "output_cap"}
            if correction is not None:
                structured[correction_key] = correction
            write_owned_json(artifact_directory / "result.json", {
                "schema_version": 1, "scrutare_version": __version__,
                "nare_version": capability.version, "contract": capability.contract,
                "session_key": lease.session_key, **binding, **outcome.to_dict(), **structured,
            }, prepared_root=prepared_root)
        except (SessionArtifactError, AttributeError):
            outcome = _outcome(lease, artifact_directory, evidence, status="failed",
                               reason="artifacts", complete=complete, decoded=decoded,
                               exit_code=exit_code, purpose=purpose)
    if cancelled:
        raise asyncio.CancelledError
    return outcome


async def run_persona_session(
    descriptor: PersonaReviewInput, rail: ModelRail, lease: BudgetLease, *,
    ledger: ReviewBudgetLedger, artifact_directory: Path, runtime: NareRuntime,
    capability: NareCapability,
) -> SessionOutcome:
    """Execute one fresh review lease through the common private lifecycle."""
    outcome = await _run_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=artifact_directory,
        runtime=runtime, capability=capability, purpose="review",
    )
    assert isinstance(outcome, SessionOutcome)
    return outcome


async def run_reanchor_session(
    descriptor: PersonaReanchorInput, rail: ModelRail, lease: BudgetLease, *,
    ledger: ReviewBudgetLedger, artifact_directory: Path, runtime: NareRuntime,
    capability: NareCapability,
) -> ReanchorOutcome:
    """Execute anchor-only corrections with a zero baseline and the M1 correction identity.

    The caller must supply attempt-0002 and the session key persona/attempt-0002.
    Correction scheduling and admission remain the caller's responsibility.
    """
    outcome = await _run_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=artifact_directory,
        runtime=runtime, capability=capability, purpose="reanchor",
    )
    assert isinstance(outcome, ReanchorOutcome)
    return outcome
