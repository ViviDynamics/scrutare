"""Validated immutable session records containing candidate findings and token counts."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scrutare.findings.models import Finding, finding_wire
from scrutare.findings.verification import ReanchorCorrection

SessionStatus = Literal["complete", "partial", "failed", "not_started"]


def _integer(value: object, field: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{field}: expected a {'positive' if positive else 'nonnegative'} integer")


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: expected a nonempty string")


def _persona(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value):
        raise ValueError("persona: expected a lowercase slug")


def _boolean(value: object, field: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field}: expected a boolean")


@dataclass(frozen=True)
class TokenUsage:
    """Disjoint reported token counters, without clipping to admission bounds."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __post_init__(self) -> None:
        for field in ("input", "output", "cache_read", "cache_write"):
            _integer(getattr(self, field), field)

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def to_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "total": self.total,
        }


CostSource = Literal["provider_reported", "unknown"]


@dataclass(frozen=True)
class SessionCost:
    """Dollars a backend reported for model requests, or unknown. Unknown is never zero."""

    usd: float | None = None
    source: CostSource = "unknown"

    def __post_init__(self) -> None:
        if self.source == "unknown":
            if self.usd is not None:
                raise ValueError("cost: an unknown cost carries no amount")
        elif self.source != "provider_reported":
            raise ValueError("cost: expected a provider_reported or unknown source")
        else:
            usd = self.usd
            if (usd is None or type(usd) not in (int, float) or not math.isfinite(usd)
                    or usd < 0):
                raise ValueError("cost: expected a reported nonnegative finite amount")
            object.__setattr__(self, "usd", float(usd))

    def __add__(self, other: "SessionCost") -> "SessionCost":
        """The sum of two reported amounts; unknown as soon as either part is."""
        if not isinstance(other, SessionCost):
            return NotImplemented
        if self.usd is None or other.usd is None:
            return SessionCost()
        return SessionCost(self.usd + other.usd, "provider_reported")

    def to_dict(self) -> dict[str, object]:
        return {"usd": self.usd, "source": self.source}


def _seconds(value: object, field: str) -> None:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{field}: expected a nonnegative finite number")


@dataclass(frozen=True)
class CapacityUsage:
    """Provider capacity refusals nare waited out, kept apart from usage, turns and retries.

    A queued refusal is not a request that was sent: nothing here is a token or a failure.
    ``source`` is ``result`` when nare's terminal record reported the counters, or ``events``
    when the session ended without one and they were counted from its progress events.
    """

    refusals: int = 0
    wait_seconds: float = 0.0
    limit: int | None = None
    slot_wait_seconds: float = 0.0
    source: Literal["result", "events"] = "result"

    def __post_init__(self) -> None:
        _integer(self.refusals, "refusals")
        _seconds(self.wait_seconds, "wait_seconds")
        _seconds(self.slot_wait_seconds, "slot_wait_seconds")
        if self.limit is not None:
            _integer(self.limit, "limit", positive=True)
        if self.source not in ("result", "events"):
            raise ValueError("source: expected result or events")

    def to_dict(self) -> dict[str, object]:
        return {"refusals": self.refusals, "wait_seconds": self.wait_seconds,
                "limit": self.limit, "slot_wait_seconds": self.slot_wait_seconds,
                "source": self.source}


@dataclass(frozen=True)
class NareRuntime:
    executable: Path
    max_turns: int = 50
    timeout_seconds: float = 600.0

    def __post_init__(self) -> None:
        if not isinstance(self.executable, Path):
            raise ValueError("executable: expected a Path")
        _integer(self.max_turns, "max_turns", positive=True)
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds: expected a positive finite number")


@dataclass(frozen=True)
class NareFlag:
    """One `nare run` option as `nare contract` advertises it under `flags`."""

    name: str
    takes_value: bool
    choices: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"-{1,2}[A-Za-z0-9][A-Za-z0-9-]*",
                                                              self.name):
            raise ValueError("flag: expected an option name")
        _boolean(self.takes_value, "takes_value")
        if self.choices is not None and (
                not isinstance(self.choices, tuple)
                or any(not isinstance(choice, str) for choice in self.choices)):
            raise ValueError("choices: expected a tuple of strings or null")


@dataclass(frozen=True)
class NareCapability:
    version: str
    contract: int
    # Constrained-answer modes the runtime advertises (nare 2026.10.12+), for the providers
    # named by structured_output_providers.
    structured_output: tuple[str, ...] = ()
    proxy_num_retries: bool = False
    # Every `nare run` option a contract 3 runtime advertises. Contracts 1 and 2 carry None:
    # their optional flags are decided from the release number, as they always were.
    flags: tuple[NareFlag, ...] | None = None
    structured_output_providers: tuple[str, ...] = ("openai",)
    # Rails on which the runtime marks prompt-cache breakpoints when asked (contract 3).
    cache_breakpoints: tuple[str, ...] = ()
    # Capacity modes advertised for the OpenAI rail: fixed (2026.11.95+), adaptive (2026.11.96+).
    capacity: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.version, "version")
        _integer(self.contract, "contract", positive=True)
        _boolean(self.proxy_num_retries, "proxy_num_retries")
        if (not isinstance(self.capacity, tuple)
                or any(mode not in ("fixed", "adaptive") for mode in self.capacity)):
            raise ValueError("capacity: expected a tuple of capacity modes")
        if (not isinstance(self.structured_output, tuple)
                or any(not isinstance(mode, str) or not mode for mode in self.structured_output)):
            raise ValueError("structured_output: expected a tuple of mode names")
        for field in ("structured_output_providers", "cache_breakpoints"):
            if (not isinstance(getattr(self, field), tuple) or any(
                    not isinstance(name, str) or not name for name in getattr(self, field))):
                raise ValueError(f"{field}: expected a tuple of provider names")
        if self.flags is not None and (
                not isinstance(self.flags, tuple)
                or any(not isinstance(flag, NareFlag) for flag in self.flags)
                or len({flag.name for flag in self.flags}) != len(self.flags)):
            raise ValueError("flags: expected distinct advertised options or null")

    def flag(self, name: str) -> NareFlag | None:
        """The advertised option of this name, or None (always None on contracts 1 and 2)."""
        return next((flag for flag in self.flags or () if flag.name == name), None)

    def advertises(self, name: str, value: str | None = None) -> bool:
        """Whether the runtime lists this option and, for an option with choices, this value."""
        flag = self.flag(name)
        if flag is None:
            return False
        if value is None:
            return True
        return flag.takes_value and (flag.choices is None or value in flag.choices)


@dataclass(frozen=True)
class SessionOutcome:
    """A session result before anchor verification or verdict derivation."""

    persona: str
    status: SessionStatus
    reason: str
    findings: tuple[Finding, ...]
    output_available: bool
    usage: TokenUsage
    accounting_complete: bool
    allocated_tokens: int
    overshoot_tokens: int
    session_id: str | None
    artifact_directory: Path
    invocation_limit: int
    nare_status: str | None = None
    stop_reason: str | None = None
    exit_code: int | None = None
    # What the backend reported for this session's requests, or unknown.
    cost: SessionCost = SessionCost()
    # Present only when the session asked nare to queue capacity refusals.
    capacity: CapacityUsage | None = None

    def __post_init__(self) -> None:
        _validate_execution_outcome(self)
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, Finding) or finding.persona != self.persona
            for finding in self.findings
        ):
            raise ValueError("findings: expected a tuple of caller-attributed findings")

    def to_dict(self) -> dict[str, object]:
        return {
            **_execution_dict(self),
            "findings": [finding_wire(finding) for finding in self.findings],
        }


@dataclass(frozen=True)
class ReanchorOutcome:
    """A fresh correction attempt with anchor-only candidates and honest execution metadata."""

    persona: str
    status: SessionStatus
    reason: str
    corrections: tuple[ReanchorCorrection, ...]
    output_available: bool
    usage: TokenUsage
    accounting_complete: bool
    allocated_tokens: int
    overshoot_tokens: int
    session_id: str | None
    artifact_directory: Path
    invocation_limit: int
    nare_status: str | None = None
    stop_reason: str | None = None
    exit_code: int | None = None
    # What the backend reported for this session's requests, or unknown.
    cost: SessionCost = SessionCost()
    # Present only when the session asked nare to queue capacity refusals.
    capacity: CapacityUsage | None = None

    def __post_init__(self) -> None:
        _validate_execution_outcome(self)
        if not isinstance(self.corrections, tuple) or any(
            not isinstance(correction, ReanchorCorrection)
            or correction.original.persona != self.persona for correction in self.corrections
        ):
            raise ValueError("corrections: expected a tuple of caller-attributed corrections")

    def to_dict(self) -> dict[str, object]:
        return {
            **_execution_dict(self),
            "corrections": [
                {"original": {
                    "file": correction.original.anchor.file,
                    "line": correction.original.anchor.line,
                    "side": correction.original.anchor.side,
                    "category": correction.original.category,
                    "problem": correction.original.problem,
                    "reason": correction.original.reason,
                    "persona": correction.original.persona,
                }, "anchor": {"file": correction.anchor.file, "line": correction.anchor.line,
                              "side": correction.anchor.side}}
                for correction in self.corrections
            ],
        }


def _validate_execution_outcome(self: SessionOutcome | ReanchorOutcome) -> None:
    _persona(self.persona)
    if self.status not in ("complete", "partial", "failed", "not_started"):
        raise ValueError("status: expected a session status")
    _text(self.reason, "reason")
    _boolean(self.output_available, "output_available")
    _boolean(self.accounting_complete, "accounting_complete")
    if not isinstance(self.usage, TokenUsage):
        raise ValueError("usage: expected TokenUsage")
    for field in ("allocated_tokens", "overshoot_tokens", "invocation_limit"):
        _integer(getattr(self, field), field)
    if self.overshoot_tokens != max(0, self.usage.total - self.invocation_limit):
        raise ValueError("overshoot_tokens: inconsistent with cumulative usage and limit")
    if not isinstance(self.artifact_directory, Path):
        raise ValueError("artifact_directory: expected a Path")
    for field in ("session_id", "nare_status", "stop_reason"):
        if getattr(self, field) is not None:
            _text(getattr(self, field), field)
    if self.exit_code is not None and type(self.exit_code) is not int:
        raise ValueError("exit_code: expected an integer or null")
    if not isinstance(self.cost, SessionCost):
        raise ValueError("cost: expected SessionCost")
    if self.capacity is not None and not isinstance(self.capacity, CapacityUsage):
        raise ValueError("capacity: expected CapacityUsage or null")


def _execution_dict(self: SessionOutcome | ReanchorOutcome) -> dict[str, object]:
    return {
        "persona": self.persona,
        "status": self.status,
        "reason": self.reason,
        "output_available": self.output_available,
        "usage": self.usage.to_dict(),
        "accounting_complete": self.accounting_complete,
        "allocated_tokens": self.allocated_tokens,
        "overshoot_tokens": self.overshoot_tokens,
        "session_id": self.session_id,
        "artifact_directory": str(self.artifact_directory),
        "invocation_limit": self.invocation_limit,
        "nare_status": self.nare_status,
        "stop_reason": self.stop_reason,
        "exit_code": self.exit_code,
        "cost": self.cost.to_dict(),
        # Absent unless capacity queueing was requested, so earlier records keep their bytes.
        **({"capacity": self.capacity.to_dict()} if self.capacity is not None else {}),
    }


@dataclass(frozen=True)
class FanOutResult:
    outcomes: tuple[SessionOutcome, ...]
    failed: bool
    partial: bool
    usage: TokenUsage
    review_exhausted: bool
    review_overshoot_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.outcomes, tuple) or any(
            not isinstance(outcome, SessionOutcome) for outcome in self.outcomes
        ):
            raise ValueError("outcomes: expected a tuple of SessionOutcome records")
        for field in ("failed", "partial", "review_exhausted"):
            _boolean(getattr(self, field), field)
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("usage: expected TokenUsage")
        _integer(self.review_overshoot_tokens, "review_overshoot_tokens")

    def to_dict(self) -> dict[str, object]:
        return {
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "failed": self.failed,
            "partial": self.partial,
            "usage": self.usage.to_dict(),
            "review_exhausted": self.review_exhausted,
            "review_overshoot_tokens": self.review_overshoot_tokens,
            **({"capacity": capacity_totals(self.outcomes)}
               if any(o.capacity is not None for o in self.outcomes) else {}),
        }


def capacity_totals(outcomes: tuple[SessionOutcome | ReanchorOutcome, ...]) -> dict[str, object]:
    """A wave's queued refusals and waits, summed apart from its token usage."""
    recorded = [o.capacity for o in outcomes if o.capacity is not None]
    return {"sessions": len(recorded),
            "refusals": sum(c.refusals for c in recorded),
            "wait_seconds": round(sum(c.wait_seconds for c in recorded), 3),
            "slot_wait_seconds": round(sum(c.slot_wait_seconds for c in recorded), 3)}
