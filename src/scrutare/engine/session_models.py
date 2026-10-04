"""Validated immutable session records containing candidate findings and token counts."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scrutare.findings.models import Finding

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
class NareCapability:
    version: str
    contract: int

    def __post_init__(self) -> None:
        _text(self.version, "version")
        _integer(self.contract, "contract", positive=True)


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

    def __post_init__(self) -> None:
        _persona(self.persona)
        if self.status not in ("complete", "partial", "failed", "not_started"):
            raise ValueError("status: expected a session status")
        _text(self.reason, "reason")
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, Finding) or finding.persona != self.persona
            for finding in self.findings
        ):
            raise ValueError("findings: expected a tuple of caller-attributed findings")
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

    def to_dict(self) -> dict[str, object]:
        return {
            "persona": self.persona,
            "status": self.status,
            "reason": self.reason,
            "findings": [
                {
                    "file": finding.anchor.file,
                    "line": finding.anchor.line,
                    "side": finding.anchor.side,
                    "category": finding.category,
                    "problem": finding.problem,
                    "reason": finding.reason,
                    "persona": finding.persona,
                }
                for finding in self.findings
            ],
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
        }
