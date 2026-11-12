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
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict, TypeVar
from urllib.parse import urlsplit

from scrutare import __version__
from scrutare.config import (
    CAPACITY_REFUSALS,
    CAPACITY_WAIT_SECONDS,
    EFFORT_LEVELS,
    REQUEST_RETRY_SECONDS,
    ConfigError,
    ModelRail,
    ModelSettings,
)
from scrutare.engine.assessment_inputs import AssessmentInput
from scrutare.engine.budgets import BudgetLease, ReviewBudgetLedger
from scrutare.engine.debate_inputs import DebateInput
from scrutare.engine.persona_inputs import PersonaReanchorInput, PersonaReviewInput
from scrutare.engine.reanchor import reanchor_schema
from scrutare.engine.review_inputs import ReviewInputError, validate_prepared_inputs
from scrutare.engine.session_artifacts import SessionArtifactError, write_owned_json
from scrutare.engine.session_models import (
    NareCapability,
    NareFlag,
    NareRuntime,
    ReanchorOutcome,
    SessionCost,
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
    decode_live_event,
    decode_reanchor_session,
    decode_session,
    findings_schema,
    require_sent_effort,
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
    cancelled: bool, credit: Callable[[], float] | None = None,
) -> _T:
    """Preserve capture while applying the common liveness and cancellation policy.

    ``credit`` returns the seconds by which the deadline has moved out so far (queue time
    a session announced). It is read again whenever the deadline is reached, so the limit
    ends a process that stays silent past what it was credited.
    """
    if cancelled:
        await _cleanup_capture(process, capture)
        raise asyncio.CancelledError
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        if credit is None:
            return await asyncio.wait_for(asyncio.shield(capture), timeout)
        while True:
            remaining = started + timeout + credit() - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                return await asyncio.wait_for(asyncio.shield(capture), remaining)
            except asyncio.TimeoutError:
                # The deadline may have moved while waiting; it is read again above.
                continue
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
_CONTRACT_3_FROM = (2026, 11, 33)
SUPPORTED_CONTRACTS = (1, 2, 3)


def _speaks(version: str, contract: object) -> bool:
    if type(contract) is not int or contract not in SUPPORTED_CONTRACTS:
        return False
    release = tuple(map(int, version.split(".")))
    expected = 3 if release >= _CONTRACT_3_FROM else 2 if release >= _CONTRACT_2_FROM else 1
    return contract == expected


# Options every contract 3 invocation sends, with whether each takes a value. A runtime that
# does not list one cannot be driven within Scrutare's recovery policy, so inspection fails.
_CONTRACT_3_REQUIRED = {
    "--provider": True, "--model": True, "--base-url": True, "--tools": True, "--root": True,
    "--system": True, "--contract": True, "--schema": True, "--budget-tokens": True,
    "--session": True, "--resume": True, "--max-turns": True, "--max-tokens": True,
    "--max-cutoffs": True, "--request-retries": True, "--max-corrections": True,
    "--jsonl": False, "--yes": False,
}
_PROVIDERS = ("anthropic", "openai")


def _flags(contract: dict[str, object]) -> tuple[NareFlag, ...]:
    """Every `nare run` option a contract 3 runtime advertises, or a refused inspection.

    Each contract 3 release lists its options, so a missing or malformed list is a contract
    object Scrutare does not understand, not an older runtime.
    """
    entry = contract.get("flags")
    try:
        if not isinstance(entry, dict):
            raise ValueError
        flags = []
        for name, record in entry.items():
            if not isinstance(record, dict) or type(record.get("takes_value")) is not bool:
                raise ValueError
            choices = record.get("choices")
            if choices is not None and (not isinstance(choices, list) or any(
                    not isinstance(choice, str) for choice in choices)):
                raise ValueError
            flags.append(NareFlag(name, record["takes_value"],
                                  None if choices is None else tuple(choices)))
        advertised = {flag.name: flag.takes_value for flag in flags}
        if any(advertised.get(name) is not takes_value
               for name, takes_value in _CONTRACT_3_REQUIRED.items()):
            raise ValueError
    except (ValueError, TypeError):
        raise SessionRuntimeError("contract: expected advertised run flags") from None
    return tuple(sorted(flags, key=lambda flag: flag.name))


def _structured_output(contract: dict[str, object],
                       flags: tuple[NareFlag, ...] | None = None,
                       ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The json_schema modes nare advertises and the rails it sends them on, or none.

    An unfamiliar entry advertises nothing rather than failing inspection: waves that never
    ask for the constraint must not depend on it, and require_rail_options refuses one that
    does. Contracts 1 and 2 only ever advertised the OpenAI rail. A contract 3 runtime also
    has to list the flag itself with the mode among its choices.
    """
    entry = contract.get("structured_output")
    if (not isinstance(entry, dict) or not isinstance(entry.get("modes"), list)
            or not isinstance(entry.get("providers"), list)
            or entry.get("flag") != "--structured-output"):
        return (), ()
    modes = tuple(mode for mode in entry["modes"] if mode == "json_schema")
    if flags is None:
        providers: tuple[str, ...] = ("openai",) if "openai" in entry["providers"] else ()
    else:
        flag = next((flag for flag in flags if flag.name == "--structured-output"), None)
        if flag is None or not flag.takes_value:
            return (), ()
        modes = tuple(mode for mode in modes if flag.choices is None or mode in flag.choices)
        providers = tuple(name for name in _PROVIDERS if name in entry["providers"])
    return (modes, providers) if modes and providers else ((), ())


def _proxy_num_retries(contract: dict[str, object],
                       flags: tuple[NareFlag, ...] | None = None) -> bool:
    entry = contract.get("proxy_num_retries")
    if flags is None:
        return entry == {"flag": "--proxy-num-retries", "providers": ["openai"],
                         "request_field": "num_retries", "default": None}
    # Contract 3 entries may gain fields; the flag list says whether the option exists.
    return (isinstance(entry, dict) and entry.get("flag") == "--proxy-num-retries"
            and entry.get("request_field") == "num_retries"
            and isinstance(entry.get("providers"), list) and "openai" in entry["providers"]
            and any(flag.name == "--proxy-num-retries" and flag.takes_value for flag in flags))


# The options a queued session sends, with whether each takes a value, and the contract
# entry's name for each.
_CAPACITY_QUEUE_FLAGS = {"--capacity-queue": False, "--capacity-wait-seconds": True,
                         "--capacity-refusals": True}
_CAPACITY_QUEUE_ENTRY = {"enable": "--capacity-queue", "wait_seconds": "--capacity-wait-seconds",
                         "refusals": "--capacity-refusals"}


def _capacity_queue(contract: dict[str, object],
                    flags: tuple[NareFlag, ...] | None) -> tuple[str, ...]:
    """The rails on which a contract 3 runtime queues provider capacity refusals when asked.

    The entry must name the three options Scrutare sends and the record and result field it
    reconciles; the flag list must carry each option in the shape it is sent. An entry of
    another shape advertises nothing, so only a wave that asks for the queue depends on it.
    """
    entry = contract.get("capacity_queue")
    if flags is None or not isinstance(entry, dict):
        return ()
    named, providers = entry.get("flags"), entry.get("providers")
    advertised = {flag.name: flag.takes_value for flag in flags}
    if (not isinstance(named, dict) or not isinstance(providers, list)
            or any(named.get(key) != flag for key, flag in _CAPACITY_QUEUE_ENTRY.items())
            or entry.get("event_detail") != "capacity"
            or entry.get("result_field") != "capacity_refusals"
            or any(advertised.get(flag) is not takes_value
                   for flag, takes_value in _CAPACITY_QUEUE_FLAGS.items())):
        return ()
    return tuple(name for name in _PROVIDERS if name in providers)


def _cache_breakpoints(contract: dict[str, object],
                       flags: tuple[NareFlag, ...] | None) -> tuple[str, ...]:
    """The rails on which a contract 3 runtime marks prompt-cache breakpoints when asked."""
    entry = contract.get("cache_breakpoints")
    if (flags is None or not isinstance(entry, dict)
            or entry.get("flag") != "--cache-breakpoints"
            or not isinstance(entry.get("providers"), list)
            or not any(flag.name == "--cache-breakpoints" and not flag.takes_value
                       for flag in flags)):
        return ()
    return tuple(name for name in _PROVIDERS if name in entry["providers"])


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
                    "contract: expected supported contract 1, 2 or 3 and matching release")
            # Contracts 1 and 2 keep their release-number rules, whatever they advertise.
            flags = _flags(contract) if contract["contract"] == 3 else None
            modes, providers = _structured_output(contract, flags)
            return NareCapability(
                version, contract["contract"], modes, _proxy_num_retries(contract, flags),
                flags=flags, cache_breakpoints=_cache_breakpoints(contract, flags),
                capacity_queue=_capacity_queue(contract, flags),
                **({"structured_output_providers": providers} if modes else {}))
    except SessionRuntimeError:
        raise
    except (OSError, ValueError, asyncio.TimeoutError, RecursionError):
        raise SessionRuntimeError(
            "inspection: cannot verify executable version and contract"
        ) from None


def _rail_refusal(rail: ModelRail, capability: NareCapability) -> str | None:
    """What this rail asks for that the inspected runtime cannot send, or None."""
    if capability.contract == 3 and capability.flags is None:
        return "contract 3 options without its advertised run flags"
    modern = capability.contract == 3
    if rail.proxy_num_retries is not None and not (
            capability.proxy_num_retries
            and (not modern or capability.advertises("--proxy-num-retries"))):
        return "proxy_num_retries"
    mode = rail.structured_output
    if mode is not None and not (
            mode in capability.structured_output
            and rail.provider in capability.structured_output_providers
            and (not modern or capability.advertises("--structured-output", mode))):
        return f"structured output {mode}"
    # The options below exist only as contract 3 flags; older contracts never send them.
    if rail.request_retries is not None and not (
            modern and (rail.request_retries == 0
                        or _takes_value(capability, "--request-retry-seconds"))):
        return "request_retries"
    if rail.cache_breakpoints and not (
            rail.provider in capability.cache_breakpoints
            and (flag := capability.flag("--cache-breakpoints")) is not None
            and not flag.takes_value):
        return "cache_breakpoints"
    if rail.effort is not None and not capability.advertises("--effort", rail.effort):
        return f"effort {rail.effort}"
    # A queued session must be streamed: only then does nare write each queue record before
    # it waits, which is what the timeout credit reads.
    if rail.capacity_queue and not (
            rail.provider in capability.capacity_queue and _streams(capability)
            and all((flag := capability.flag(name)) is not None
                    and flag.takes_value is takes_value
                    for name, takes_value in _CAPACITY_QUEUE_FLAGS.items())):
        return "capacity_queue"
    return None


def require_rail_options(models: ModelSettings, capability: NareCapability) -> None:
    """Refuse a wave before any session when a rail asks for an option nare cannot send."""
    for rail in (models.default, *(rail for _, rail in models.overrides)):
        refusal = _rail_refusal(rail, capability)
        if refusal is not None:
            raise SessionRuntimeError(
                f"capability: nare {capability.version} cannot send {refusal}; "
                "use a nare release that advertises it")


# The name this check had when structured output was the only gated option.
require_structured_output = require_rail_options


def _rail(rail: ModelRail) -> None:
    if (not isinstance(rail, ModelRail) or rail.provider not in ("anthropic", "openai")
            or not isinstance(rail.model, str) or not rail.model.strip()
            or "\x00" in rail.model):
        raise SessionRuntimeError("rail: expected an explicit provider and nonempty model")
    if rail.structured_output is not None and rail.structured_output != "json_schema":
        raise SessionRuntimeError("rail.structured_output: expected json_schema")
    if rail.max_output_tokens is not None and (
            type(rail.max_output_tokens) is not int or rail.max_output_tokens <= 0):
        raise SessionRuntimeError("rail.max_output_tokens: expected a positive integer or null")
    if rail.proxy_num_retries is not None and (
            type(rail.proxy_num_retries) is not int or rail.proxy_num_retries < 0
            or rail.provider != "openai"):
        raise SessionRuntimeError(
            "rail.proxy_num_retries: expected a nonnegative integer on openai")
    try:
        # The newer opt-in fields are validated once, where configuration validates them.
        ModelRail.__post_init__(rail)
    except (ConfigError, TypeError):
        raise SessionRuntimeError("rail: invalid opt-in option") from None
    if rail.effort is not None and rail.effort not in EFFORT_LEVELS:
        raise SessionRuntimeError("rail.effort: expected a reasoning effort level")
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


# Owner-only prompt files in the attempt directory, used where the runtime lists the flags.
SYSTEM_FILE = "system-prompt.txt"
PROMPT_FILE = "task-prompt.txt"


def _takes_value(capability: NareCapability, flag: str) -> bool:
    """Whether the inspected runtime lists this option as one taking a value (contract 3)."""
    advertised = capability.flag(flag)
    return advertised is not None and advertised.takes_value


def _new_text(directory: int, name: str, text: str) -> None:
    """Create an owner-only file holding exactly these UTF-8 bytes, never replacing one."""
    remaining = memoryview(text.encode("utf-8"))
    with _new_capture(directory, name) as stream:
        # The stream is unbuffered, so one write may take only part of the bytes.
        while remaining:
            remaining = remaining[stream.write(remaining):]


def _same_text(directory: int, name: str, text: str) -> bool:
    """Whether the retained private file still holds exactly these UTF-8 bytes."""
    try:
        return _session_document(directory, name) == text.encode("utf-8")
    except OSError:
        return False


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
    # Resent model requests seen in the stream. A resend reports no usage of its own.
    retries: int = 0
    # Dollars the backend reported for the requests seen so far; None before any cost event.
    cost: SessionCost | None = None
    # Capacity refusals nare queued, and the seconds it announced it would wait on them.
    # A queued refusal was never dispatched, so it reports no usage either.
    queued: int = 0
    queued_seconds: float = 0.0

    def event(self, line: bytes, ledger: ReviewBudgetLedger, lease: BudgetLease) -> None:
        if not line.strip():
            return
        try:
            usage, resent, cost, wait = decode_live_event(line)
            self.retries += resent
            if wait is not None:
                self.queued += 1
                self.queued_seconds += wait
            if cost is not None:
                self.cost = cost if self.cost is None else self.cost + cost
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


def _capacity_limits(rail: ModelRail) -> tuple[int | float, int]:
    """The queue limits a queued rail's sessions are sent: its own, else Scrutare's."""
    seconds, refusals = rail.capacity_wait_seconds, rail.capacity_refusals
    return (CAPACITY_WAIT_SECONDS if seconds is None else seconds,
            CAPACITY_REFUSALS if refusals is None else refusals)


def _queue_credit(rail: ModelRail, announced: float) -> float:
    """Seconds of announced queue time credited against the session timeout.

    A queued session is alive and waiting on purpose, so the waits nare announced move its
    deadline out. The credit is bounded by the rail's wait limit over the whole session, and
    a rail that did not ask for the queue earns none.
    """
    if not isinstance(rail, ModelRail) or not rail.capacity_queue:
        return 0.0
    return min(announced, float(_capacity_limits(rail)[0]))


async def _execute(argv: list[str], environment: dict[str, str], cwd: Path,
                   runtime: NareRuntime, stdout: BinaryIO, stderr: BinaryIO,
                   evidence: _Evidence, ledger: ReviewBudgetLedger,
                   lease: BudgetLease, credit: Callable[[], float] | None = None) -> int:
    process, cancelled = await _start_process(argv, environment, cwd)
    evidence.started = True
    assert process.stdout is not None and process.stderr is not None
    drainage = asyncio.gather(_stdout(process.stdout, stdout, evidence, ledger, lease),
                              _stderr(process.stderr, stderr, ledger, lease), process.wait(),
                              return_exceptions=True)
    try:
        results = await _wait_capture(
            process, drainage, runtime.timeout_seconds, cancelled=cancelled, credit=credit,
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
    cost: SessionCost


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
        # Unknown, never a partial sum or a zero. A session whose accounting is incomplete
        # (it was stopped before its result, or a request failed) saw at most a lower bound:
        # the stream's sum so far, or the reported zero of nare's empty usage. One whose
        # stream reported no cost at all has no amount either.
        "cost": evidence.cost if complete and evidence.cost is not None else SessionCost(),
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


def _streams(capability: NareCapability) -> bool:
    if capability.contract == 3:
        return capability.advertises("--stream")
    return tuple(map(int, capability.version.split("."))) >= (2026, 10, 4)


def _rail_argv(rail: ModelRail, capability: NareCapability) -> list[str]:
    argv = ["--provider", rail.provider, "--model=" + rail.model]
    if rail.provider == "openai" and _streams(capability):
        argv.append("--stream")
    if capability.contract == 3:
        # Preserve Scrutare's recovery policy rather than inherit newer CLI defaults.
        # A rail may opt in to resends; unset keeps the pinned zero.
        retries = rail.request_retries or 0
        argv.extend(("--max-cutoffs", "0", "--request-retries", str(retries),
                     "--max-corrections", "1"))
        if retries:
            # Explicit, so nare's own default wait never decides how long a session stalls.
            seconds = rail.request_retry_seconds
            argv.extend(("--request-retry-seconds",
                         str(REQUEST_RETRY_SECONDS if seconds is None else seconds)))
        # The same for compaction (nare 2026.11.94): these are the values nare used before
        # the options existed, and in elide mode every request is unchanged.
        if capability.advertises("--compaction", "elide"):
            argv.extend(("--compaction", "elide"))
        if (capability.advertises("--compaction-trigger", "0.8")
                and capability.advertises("--compaction-target", "0.6")):
            argv.extend(("--compaction-trigger", "0.8", "--compaction-target", "0.6"))
    if rail.base_url is not None:
        argv.extend(("--base-url", rail.base_url))
    return argv


def _rail_options(rail: ModelRail) -> list[str]:
    argv: list[str] = []
    if rail.structured_output is not None:
        argv.extend(("--structured-output", rail.structured_output))
    if rail.max_output_tokens is not None:
        argv.extend(("--max-tokens", str(rail.max_output_tokens)))
    if rail.proxy_num_retries is not None:
        argv.extend(("--proxy-num-retries", str(rail.proxy_num_retries)))
    if rail.cache_breakpoints:
        argv.append("--cache-breakpoints")
    if rail.effort is not None:
        argv.extend(("--effort", rail.effort))
    if rail.capacity_queue:
        # Both limits are explicit, so nare's own defaults never decide how long a session
        # may queue.
        seconds, refusals = _capacity_limits(rail)
        argv.extend(("--capacity-queue", "--capacity-wait-seconds", str(seconds),
                     "--capacity-refusals", str(refusals)))
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
    prompt_name = stem + ".prompt.txt"
    prompt_file = _takes_value(capability, "--prompt-file")
    system_file = _takes_value(capability, "--system-file")
    # The system option stays on the resume. Without one nare resends the prompt its session
    # file recorded, which is the redacted copy, so the bytes could differ from the first run.
    argv = [str(executable), "run",
            *(("--prompt-file", str(artifact_directory / prompt_name)) if prompt_file
              else (prompt,)),
            "--tools", "none", "--root", str(root),
            *(("--system-file", str(artifact_directory / SYSTEM_FILE)) if system_file
              else ("--system=" + system,)), *_rail_argv(rail, capability),
            "--jsonl", "--yes", "--contract", str(capability.contract),
            "--schema", str(artifact_directory / (stem + ".schema.json")),
            "--budget-tokens", str(lease.limit_tokens),
            "--resume", str(artifact_directory / "session.json"),
            "--session", str(artifact_directory / "correction-session.json"),
            "--max-turns", "1", *_rail_options(rail)]
    # Recorded as offered before anything is written, so any later failure says so.
    record.update(offered=True, defects=len(offered.defects), outcome="failed")
    if system_file and not _same_text(directory, SYSTEM_FILE, system):
        raise SessionArtifactError("system prompt: retained file changed before correction")
    if prompt_file:
        try:
            _new_text(directory, prompt_name, prompt)
        except OSError:
            raise SessionArtifactError("correction prompt: cannot create private file") from None
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
    # The turn continues the session's deadline, with the queue time credited so far.
    credited = _queue_credit(rail, evidence.queued_seconds)
    remaining = deadline + credited - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError
    if isinstance(offered, ProtocolCorrectionOffered):
        validate_prepared_inputs(descriptor.inputs)
    correction = _Evidence(evidence.usage, started=True, cost=evidence.cost)
    first_queued = evidence.queued_seconds
    try:
        with ExitStack() as stack:
            stdout = stack.enter_context(_new_capture(directory, "correction.stdout.jsonl"))
            stderr = stack.enter_context(_new_capture(directory, "correction.stderr.txt"))
            exit_code = await _execute(
                argv, _environment(artifact_directory / "home", artifact_directory / "tmp",
                                   rail.provider), artifact_directory / "cwd",
                replace(runtime, timeout_seconds=remaining), stdout, stderr, correction,
                ledger, lease,
                (lambda: _queue_credit(rail, first_queued + correction.queued_seconds)
                 - credited) if rail.capacity_queue else None)
        record["exit_code"] = exit_code
    finally:
        # Usage is cumulative across both turns of the session, whatever happened.
        evidence.usage = correction.usage
        evidence.cost = correction.cost
        evidence.corrupt = evidence.corrupt or correction.corrupt
        evidence.retries += correction.retries
        evidence.queued += correction.queued
        evidence.queued_seconds += correction.queued_seconds
    saved = _session_document(directory, "correction-session.json")
    if correction.corrupt:
        raise SessionProtocolError("session: corrupted correction evidence")
    if correction.queued and not rail.capacity_queue:
        raise SessionProtocolError("session: unrequested capacity queue evidence")
    # A constrained-turn record must match the rail, as in the first stream.
    constrained_turns(correction.stdout, rail.structured_output)
    require_sent_effort(saved, rail.effort)
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
    evidence.cost = decoded.cost
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
        refusal = _rail_refusal(rail, capability)
        if refusal is not None:
            raise SessionRuntimeError(
                f"capability: nare {capability.version} cannot send {refusal}")
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
            # Long text stays out of argv where the runtime reads it from a file. The bytes
            # nare decodes are the bytes argv would have carried.
            prompt_file = _takes_value(capability, "--prompt-file")
            system_file = _takes_value(capability, "--system-file")
            argv = [str(executable), "run",
                    *(("--prompt-file", str(artifact_directory / PROMPT_FILE)) if prompt_file
                      else input_args[:1]), *input_args[1:],
                    *(("--system-file", str(artifact_directory / SYSTEM_FILE)) if system_file
                      else ("--system=" + system,)),
                    *_rail_argv(rail, capability)]
            if prompt_file:
                _new_text(directory, PROMPT_FILE, input_args[0])
            if system_file:
                _new_text(directory, SYSTEM_FILE, system)
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
                # Each opt-in field is recorded only when this rail sets it.
                "argv": argv, "rail": {"provider": rail.provider, "model": rail.model,
                                        **{name: value for name, value in rail.record().items()
                                           if name not in ("provider", "model")}},
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
                    # Only a rail that queues can move its deadline; every other session
                    # waits exactly as it always has.
                    (lambda: _queue_credit(rail, evidence.queued_seconds))
                    if rail.capacity_queue else None,
                )
            reason = "protocol"
            saved = _session_document(directory)
            if evidence.corrupt:
                raise SessionProtocolError("session: corrupted event evidence")
            constrained = constrained_turns(evidence.stdout, rail.structured_output)
            if evidence.retries and not rail.request_retries:
                # nare was told never to resend, so a retry record is not this session's.
                raise SessionProtocolError("session: unrequested request retry evidence")
            if evidence.queued and not rail.capacity_queue:
                # nare was not told to queue, so a queued refusal is not this session's.
                raise SessionProtocolError("session: unrequested capacity queue evidence")
            require_sent_effort(saved, rail.effort)
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
                    if isinstance(offered, ProtocolCorrectionOffered):
                        validate_prepared_inputs(descriptor.inputs)
                    # Every usage seen so far is in the ledger; the turn continues from it.
                    ledger.observe(lease, offered.evidence.usage)
                    evidence.usage = offered.evidence.usage
                    evidence.cost = offered.evidence.session_cost
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
            if decoded.status is not None:
                # nare's cumulative figure for the session is the one to keep.
                evidence.cost = decoded.cost
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
    ledger.settle(lease, evidence.usage, complete, outcome.cost)
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
            if isinstance(rail, ModelRail) and rail.request_retries is not None:
                # Resends seen in the retained streams; a session that never started saw none.
                structured["request_retries"] = {
                    "limit": rail.request_retries,
                    "retried": evidence.retries if evidence.started else None}
            if isinstance(rail, ModelRail) and rail.cache_breakpoints is not None:
                structured["cache_breakpoints"] = {"enabled": rail.cache_breakpoints}
            if isinstance(rail, ModelRail) and rail.effort is not None:
                structured["effort"] = {"level": rail.effort}
            if isinstance(rail, ModelRail) and rail.capacity_queue is not None:
                queue: dict[str, object] = {"enabled": rail.capacity_queue}
                if rail.capacity_queue:
                    # Refusals queued in the retained streams and the seconds nare announced
                    # for them; a session that never started saw none.
                    seconds, refusals = _capacity_limits(rail)
                    started = evidence.started
                    queue.update(
                        wait_seconds=seconds, refusals_limit=refusals,
                        queued=evidence.queued if started else None,
                        waited_seconds=round(evidence.queued_seconds, 3) if started else None)
                structured["capacity_queue"] = queue
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
