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
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict, TypeVar
from urllib.parse import urlsplit

from scrutare import __version__
from scrutare.config import ModelRail
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
    DecodedReanchorSession,
    DecodedSession,
    SessionProtocolError,
    decode_cost_event,
    decode_reanchor_session,
    decode_session,
    findings_schema,
)
from scrutare.findings.evidence import CitationValidationError, evidence_version
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
            if (not isinstance(contract, dict) or type(contract.get("contract")) is not int
                    or contract["contract"] != 1 or contract.get("nare") != version):
                raise SessionRuntimeError("contract: expected contract 1 and matching nare release")
            return NareCapability(version, 1)
    except SessionRuntimeError:
        raise
    except (OSError, ValueError, asyncio.TimeoutError, RecursionError):
        raise SessionRuntimeError(
            "inspection: cannot verify executable version and contract 1"
        ) from None


def _rail(rail: ModelRail) -> None:
    if (not isinstance(rail, ModelRail) or rail.provider not in ("anthropic", "openai")
            or not isinstance(rail.model, str) or not rail.model.strip()
            or "\x00" in rail.model):
        raise SessionRuntimeError("rail: expected an explicit provider and nonempty model")
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


def _session_document(directory: int) -> bytes | None:
    try:
        descriptor = os.open("session.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
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
        if not isinstance(capability, NareCapability) or capability.contract != 1:
            raise SessionRuntimeError("capability: expected inspected contract 1")
        _version(capability.version)
        if purpose == "reanchor" and lease.session_key != f"{lease.persona}/attempt-0002":
            raise SessionRuntimeError("session_key: expected the fresh correction identity")
        reason = "artifacts"
        if purpose == "reanchor" and artifact_directory.name != "attempt-0002":
            raise SessionArtifactError("artifact_directory: expected the correction attempt")
        with _attempt(artifact_directory, prepared_root, lease.persona) as directory:
            if os.listdir(directory):
                raise SessionArtifactError("artifact_directory: expected an unused attempt")
            for name in ("cwd", "home", "tmp"):
                os.mkdir(name, mode=0o700, dir_fd=directory)
            argv = [str(executable), "run", *input_args, "--system=" + system,
                    "--provider", rail.provider, "--model=" + rail.model]
            if (rail.provider == "openai"
                    and tuple(map(int, capability.version.split("."))) >= (2026, 10, 4)):
                argv.append("--stream")
            if rail.base_url is not None:
                argv.extend(("--base-url", rail.base_url))
            schema_name = ("reanchor.schema.json" if purpose == "reanchor"
                           else "findings.schema.json")
            schema = (descriptor.output_schema() if isinstance(descriptor, DebateInput) else
                      reanchor_schema() if purpose == "reanchor" else
                      findings_schema(evidence_version=evidence_version(prepared_root)))
            argv.extend(("--jsonl", "--yes", "--contract", "1", "--schema",
                         str(artifact_directory / schema_name), "--budget-tokens",
                         str(lease.limit_tokens), "--session",
                         str(artifact_directory / "session.json"),
                         "--max-turns", str(runtime.max_turns)))
            write_owned_json(artifact_directory / schema_name, schema,
                             prepared_root=prepared_root)
            write_owned_json(artifact_directory / "invocation.json", {
                "schema_version": 1, "scrutare_version": __version__,
                "nare_version": capability.version, "contract": capability.contract,
                "persona": lease.persona, "session_key": lease.session_key, **binding,
                "system_prompt_sha256": sha256(system.encode("utf-8")).hexdigest(),
                "task_prompt_sha256": sha256(input_args[0].encode("utf-8")).hexdigest(),
                "argv": argv, "rail": {"provider": rail.provider, "model": rail.model,
                                        "base_url": rail.base_url},
                "allocated_tokens": lease.allocated_tokens, "invocation_limit": lease.limit_tokens,
                "max_turns": runtime.max_turns, "timeout_seconds": runtime.timeout_seconds,
            }, prepared_root=prepared_root)
            with ExitStack() as stack:
                stdout = stack.enter_context(_new_capture(directory, "stdout.jsonl"))
                stderr = stack.enter_context(_new_capture(directory, "stderr.txt"))
                reason = "launch"
                complete = False
                exit_code = await _execute(
                    argv, _environment(artifact_directory / "home", artifact_directory / "tmp",
                                       rail.provider), artifact_directory / "cwd", runtime,
                    stdout, stderr, evidence, ledger, lease,
                )
            reason = "protocol"
            saved = _session_document(directory)
            if evidence.corrupt:
                raise SessionProtocolError("session: corrupted event evidence")
            if isinstance(descriptor, PersonaReanchorInput):
                try:
                    validate_prepared_inputs(descriptor.inputs)
                except ReviewInputError:
                    ledger.mark_uncertain(lease)
                    raise
                decoded = decode_reanchor_session(
                    evidence.stdout, saved, descriptor=descriptor,
                    exit_code=exit_code, expected_limit=lease.limit_tokens,
                )
            else:
                decoded = decode_session(evidence.stdout, saved, persona=lease.persona,
                                         exit_code=exit_code, expected_limit=lease.limit_tokens,
                                         expected_root=prepared_root,
                                         descriptor=(descriptor
                                                     if isinstance(descriptor, DebateInput)
                                                     else None),
                                         evidence_version=evidence_version(prepared_root),
                                         candidate_namespace=str(artifact_directory))
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
            if decoded.status == "done":
                status, reason = ("partial", "budget") if decoded.partial else ("complete", "done")
            elif decoded.status == "error" and decoded.stop_reason == "budget":
                status, reason = "partial", "budget"
            elif decoded.status == "blocked":
                reason = "blocked"
            else:
                reason = "not_started" if decoded.status is None else "provider"
            validate_prepared_inputs(descriptor.inputs)
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
            write_owned_json(artifact_directory / "result.json", {
                "schema_version": 1, "scrutare_version": __version__,
                "nare_version": capability.version, "contract": capability.contract,
                "session_key": lease.session_key, **binding, **outcome.to_dict(),
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
