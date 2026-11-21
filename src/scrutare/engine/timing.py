"""Review and session latency records: measurements, never inputs to a verdict.

Each launched session writes `timing.json` beside its `result.json`, and each review run
writes one `timing.json` at its root before anything is posted. Wall clock is Scrutare's own
monotonic clock. Retry and capacity waits are the seconds nare announced in its stream and
result. Time to first token and decode rate need per-request timing that nare's stream does
not carry yet (ViviDynamics/nare#315), so both are recorded as null rather than estimated.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrutare.engine.session_artifacts import write_owned_bytes

TIMING_FILE = "timing.json"
# Measurements the stream cannot support yet. They stay null until nare reports them.
_UNMEASURED = ("first_token_seconds", "decode_tokens_per_second")
_SESSION_FIELDS = ("schema_version", "provider", "model", "invocations", "wall_seconds",
                   "tokens", "output_tokens", "retries", "retry_wait_seconds",
                   "capacity_wait_seconds", "capacity_slot_wait_seconds", *_UNMEASURED)
_TOTAL_FIELDS = ("sessions", "session_wall_seconds", "longest_session_seconds",
                 "tokens", "output_tokens", "retries", "retry_wait_seconds",
                 "capacity_wait_seconds", "capacity_slot_wait_seconds")
_REVIEW_FIELDS = ("schema_version", "wall_seconds", *_TOTAL_FIELDS, *_UNMEASURED, "rails")
_RAIL_FIELDS = ("provider", "model", "sessions", "session_wall_seconds", "tokens",
                "output_tokens")


class TimingError(ValueError):
    """A timing record that is not well formed; carries no captured content."""


def _seconds(value: object, field: str) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise TimingError(f"{field}: expected nonnegative finite seconds")
    return round(float(value), 3)


def _count(value: object, field: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise TimingError(f"{field}: expected a {'positive' if positive else 'nonnegative'} "
                          "integer")
    return value


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TimingError(f"{field}: expected a nonempty string")
    return value


def _unmeasured(document: dict[str, Any]) -> None:
    if any(document[field] is not None for field in _UNMEASURED):
        raise TimingError("timing: first token and decode rate are not measured yet")


@dataclass(frozen=True)
class SessionTiming:
    """One launched session, over every invocation it made (a correction turn included)."""

    provider: str
    model: str
    invocations: int
    wall_seconds: float
    tokens: int
    output_tokens: int
    retries: int = 0
    retry_wait_seconds: float = 0.0
    capacity_wait_seconds: float = 0.0
    capacity_slot_wait_seconds: float = 0.0

    def __post_init__(self) -> None:
        _name(self.provider, "provider")
        _name(self.model, "model")
        _count(self.invocations, "invocations", positive=True)
        _count(self.tokens, "tokens")
        _count(self.output_tokens, "output_tokens")
        if self.output_tokens > self.tokens:
            raise TimingError("output_tokens: expected at most the session's tokens")
        _count(self.retries, "retries")
        for field in ("wall_seconds", "retry_wait_seconds", "capacity_wait_seconds",
                      "capacity_slot_wait_seconds"):
            object.__setattr__(self, field, _seconds(getattr(self, field), field))

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "provider": self.provider, "model": self.model,
                "invocations": self.invocations, "wall_seconds": self.wall_seconds,
                "tokens": self.tokens, "output_tokens": self.output_tokens,
                "retries": self.retries,
                "retry_wait_seconds": self.retry_wait_seconds,
                "capacity_wait_seconds": self.capacity_wait_seconds,
                "capacity_slot_wait_seconds": self.capacity_slot_wait_seconds,
                "first_token_seconds": None, "decode_tokens_per_second": None}

    @classmethod
    def from_dict(cls, document: object) -> SessionTiming:
        if (not isinstance(document, dict) or set(document) != set(_SESSION_FIELDS)
                or document["schema_version"] != 1 or type(document["schema_version"]) is not int):
            raise TimingError("timing: expected a session timing record")
        _unmeasured(document)
        timing = cls(**{field: document[field] for field in _SESSION_FIELDS
                        if field not in ("schema_version", *_UNMEASURED)})
        if timing.to_dict() != document:
            raise TimingError("timing: expected millisecond seconds")
        return timing


def _totals(sessions: list[SessionTiming]) -> dict[str, object]:
    return {
        "sessions": len(sessions),
        "session_wall_seconds": round(float(sum(s.wall_seconds for s in sessions)), 3),
        "longest_session_seconds": max((s.wall_seconds for s in sessions), default=0.0),
        "tokens": sum(s.tokens for s in sessions),
        "output_tokens": sum(s.output_tokens for s in sessions),
        "retries": sum(s.retries for s in sessions),
        "retry_wait_seconds": round(float(sum(s.retry_wait_seconds for s in sessions)), 3),
        "capacity_wait_seconds": round(float(sum(s.capacity_wait_seconds for s in sessions)), 3),
        "capacity_slot_wait_seconds": round(
            float(sum(s.capacity_slot_wait_seconds for s in sessions)), 3),
    }


def review_timing(wall_seconds: float, sessions: list[SessionTiming]) -> dict[str, object]:
    """The run-level record: review wall clock, session totals and a breakdown per rail."""
    rails: dict[tuple[str, str], list[SessionTiming]] = {}
    for session in sessions:
        rails.setdefault((session.provider, session.model), []).append(session)
    return {
        "schema_version": 1, "wall_seconds": _seconds(wall_seconds, "wall_seconds"),
        **_totals(sessions), "first_token_seconds": None, "decode_tokens_per_second": None,
        "rails": [{"provider": provider, "model": model, "sessions": len(group),
                   "session_wall_seconds": round(float(sum(s.wall_seconds for s in group)), 3),
                   "tokens": sum(s.tokens for s in group),
                   "output_tokens": sum(s.output_tokens for s in group)}
                  for (provider, model), group in sorted(rails.items())],
    }


def session_timing_paths(run_dir: Path) -> list[Path]:
    """Every session attempt's timing record under this run, in a stable order.

    Sessions live under any `sessions/` directory of the run (an iterative round has its
    own). Directory links are not followed: a run's sessions are its own directories.
    """
    found = []
    for root, directories, files in os.walk(run_dir, followlinks=False):
        current = Path(root)
        directories.sort()
        if (current.parent.parent.name == "sessions"
                and re.fullmatch(r"attempt-[0-9]{4}", current.name)):
            directories[:] = []
            if TIMING_FILE in files:
                found.append(current / TIMING_FILE)
    return sorted(found)


def read_session_timings(run_dir: Path) -> list[SessionTiming]:
    """Decode every session timing record of a run; any malformed one is an error."""
    timings = []
    for path in session_timing_paths(run_dir):
        if path.is_symlink() or not path.is_file():
            raise TimingError("timing: expected a regular session record")
        try:
            document = json.loads(path.read_bytes())
        except (OSError, ValueError, RecursionError):
            raise TimingError("timing: unreadable session record") from None
        timings.append(SessionTiming.from_dict(document))
    return timings


def validate_review_timing(document: object, sessions: list[SessionTiming]) -> dict[str, Any]:
    """A run-level record that is well formed and agrees with its sessions' records.

    Wall clock is a measurement, so no rerun can reproduce it. What can be checked is that
    the totals are the sums of the session records and that the review lasted at least as
    long as its longest session.
    """
    if (not isinstance(document, dict) or set(document) != set(_REVIEW_FIELDS)
            or type(document["schema_version"]) is not int or document["schema_version"] != 1):
        raise TimingError("timing: expected a review timing record")
    _unmeasured(document)
    wall = _seconds(document["wall_seconds"], "wall_seconds")
    rails = document["rails"]
    if (not isinstance(rails, list) or any(
            not isinstance(rail, dict) or set(rail) != set(_RAIL_FIELDS) for rail in rails)):
        raise TimingError("timing: expected a rail breakdown")
    if document != review_timing(wall, sessions):
        raise TimingError("timing: totals disagree with the session records")
    if wall < _seconds(document["longest_session_seconds"], "longest_session_seconds"):
        raise TimingError("timing: the review is shorter than one of its sessions")
    return document


def write_review_timing(run_dir: Path, wall_seconds: float) -> dict[str, object]:
    """Install the run-level record from the session records already written."""
    document = review_timing(wall_seconds, read_session_timings(run_dir))
    write_owned_bytes(run_dir / TIMING_FILE,
                      (json.dumps(document, sort_keys=True, indent=2, allow_nan=False)
                       + "\n").encode("utf-8"),
                      prepared_root=run_dir / "review-inputs")
    return document


def _duration(seconds: float) -> str:
    whole = int(round(seconds))
    if whole < 60:
        return f"{whole} s"
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{hours} h {minutes} min" if hours else f"{minutes} min {secs} s")


def footer_line(document: dict[str, Any]) -> str:
    """One compact summary line: review time, sessions and tokens."""
    sessions = document["sessions"]
    return (f"Review time: {_duration(document['wall_seconds'])}, {sessions} "
            f"session{'' if sessions == 1 else 's'}, {document['tokens']} tokens "
            f"({document['output_tokens']} output)")
