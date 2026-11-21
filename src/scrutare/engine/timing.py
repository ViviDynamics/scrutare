"""Review and session latency records: measurements, never inputs to a verdict.

Each launched session writes `timing.json` beside its `result.json`, and each review run
writes one `timing.json` at its root before anything is posted. Wall clock is Scrutare's own
monotonic clock. Retry and capacity waits are the seconds nare announced in its stream and
result. Time to first token and decode rate come from nare's per-request timing: each
invocation's result line sums its requests as `request_timing`, and a version 2 record keeps
those sums as `requests` and derives both values from them. Either is null when nothing
measured it; neither is ever estimated. Version 1 records, written before nare reported
request timing, carry both as null and no `requests` block, and still validate.
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
# The current record version. Version 1 has no `requests` block and both derived values null.
SCHEMA_VERSION = 2
# Derived from nare's request timing in version 2; always null in version 1.
_DERIVED = ("first_token_seconds", "decode_tokens_per_second")
_SESSION_FIELDS = ("schema_version", "provider", "model", "invocations", "wall_seconds",
                   "tokens", "output_tokens", "retries", "retry_wait_seconds",
                   "capacity_wait_seconds", "capacity_slot_wait_seconds", *_DERIVED)
_TOTAL_FIELDS = ("sessions", "session_wall_seconds", "longest_session_seconds",
                 "tokens", "output_tokens", "retries", "retry_wait_seconds",
                 "capacity_wait_seconds", "capacity_slot_wait_seconds")
_REVIEW_FIELDS = ("schema_version", "wall_seconds", *_TOTAL_FIELDS, *_DERIVED, "rails")
_REQUEST_FIELDS = ("requests", "timed", "headers_seconds", "total_seconds", "first_tokens",
                   "first_token_seconds", "generation_seconds", "generation_output_tokens")
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


def _version(document: object, fields: tuple[str, ...]) -> int:
    """The record's schema version, after checking it has exactly that version's fields."""
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
        return 0
    version = document["schema_version"]
    if version not in (1, SCHEMA_VERSION):
        return 0
    expected = set(fields) | ({"requests"} if version == SCHEMA_VERSION else set())
    return version if set(document) == expected else 0


def _optional_seconds(value: object, field: str) -> float | None:
    return None if value is None else _seconds(value, field)


def _add(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    return left if right is None else round(left + right, 3)


@dataclass(frozen=True)
class RequestTiming:
    """nare's `request_timing` sums, over one invocation or added across several.

    A seconds sum is null when no request measured it. The mean time to first token is
    `first_token_seconds` over `first_tokens`, and the decode rate `generation_output_tokens`
    over `generation_seconds`; each is null when its divisor is zero or null.
    """

    requests: int = 0
    timed: int = 0
    headers_seconds: float | None = None
    total_seconds: float | None = None
    first_tokens: int = 0
    first_token_seconds: float | None = None
    generation_seconds: float | None = None
    generation_output_tokens: int = 0

    def __post_init__(self) -> None:
        for field in ("requests", "timed", "first_tokens", "generation_output_tokens"):
            _count(getattr(self, field), field)
        for field in ("headers_seconds", "total_seconds", "first_token_seconds",
                      "generation_seconds"):
            object.__setattr__(self, field, _optional_seconds(getattr(self, field), field))
        if self.timed > self.requests or self.first_tokens > self.timed:
            raise TimingError("requests: expected timed requests among the requests")
        if (self.first_token_seconds is None) != (self.first_tokens == 0):
            raise TimingError("requests: expected first token seconds for each first token")
        if self.generation_seconds is None and self.generation_output_tokens:
            raise TimingError("requests: expected generation tokens to have generation time")
        if self.timed == 0 and (self.headers_seconds, self.total_seconds,
                                self.generation_seconds) != (None, None, None):
            raise TimingError("requests: expected no seconds without a timed request")

    def __add__(self, other: RequestTiming) -> RequestTiming:
        return RequestTiming(
            self.requests + other.requests, self.timed + other.timed,
            _add(self.headers_seconds, other.headers_seconds),
            _add(self.total_seconds, other.total_seconds),
            self.first_tokens + other.first_tokens,
            _add(self.first_token_seconds, other.first_token_seconds),
            _add(self.generation_seconds, other.generation_seconds),
            self.generation_output_tokens + other.generation_output_tokens)

    @property
    def mean_first_token_seconds(self) -> float | None:
        if not self.first_tokens or self.first_token_seconds is None:
            return None
        return round(self.first_token_seconds / self.first_tokens, 3)

    @property
    def decode_tokens_per_second(self) -> float | None:
        if not self.generation_seconds:
            return None
        return round(self.generation_output_tokens / self.generation_seconds, 3)

    def to_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in _REQUEST_FIELDS}

    @classmethod
    def from_dict(cls, document: object) -> RequestTiming:
        """Decode nare's result field or a record's block: exactly its fields, in ms."""
        if not isinstance(document, dict) or set(document) != set(_REQUEST_FIELDS):
            raise TimingError("requests: expected nare's request timing sums")
        timing = cls(**{field: document[field] for field in _REQUEST_FIELDS})
        if timing.to_dict() != document:
            raise TimingError("requests: expected millisecond seconds")
        return timing


def total_requests(blocks: list[RequestTiming | None]) -> RequestTiming | None:
    """The sum of the blocks that were reported; null when none was."""
    reported = [block for block in blocks if block is not None]
    return sum(reported[1:], reported[0]) if reported else None


def _derived(requests: RequestTiming | None) -> dict[str, object]:
    return {"first_token_seconds": None if requests is None
            else requests.mean_first_token_seconds,
            "decode_tokens_per_second": None if requests is None
            else requests.decode_tokens_per_second}


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
    # nare's request timing summed over the invocations whose result carried it.
    requests: RequestTiming | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in (1, SCHEMA_VERSION):
            raise TimingError("schema_version: expected a known timing record version")
        if self.schema_version == 1 and self.requests is not None:
            raise TimingError("requests: a version 1 record carries no request timing")
        if self.requests is not None and not isinstance(self.requests, RequestTiming):
            raise TimingError("requests: expected request timing sums")
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
        document: dict[str, object] = {
            "schema_version": self.schema_version, "provider": self.provider,
            "model": self.model, "invocations": self.invocations,
            "wall_seconds": self.wall_seconds, "tokens": self.tokens,
            "output_tokens": self.output_tokens, "retries": self.retries,
            "retry_wait_seconds": self.retry_wait_seconds,
            "capacity_wait_seconds": self.capacity_wait_seconds,
            "capacity_slot_wait_seconds": self.capacity_slot_wait_seconds,
            **_derived(self.requests)}
        if self.schema_version == SCHEMA_VERSION:
            document["requests"] = None if self.requests is None else self.requests.to_dict()
        return document

    @classmethod
    def from_dict(cls, document: object) -> SessionTiming:
        version = _version(document, _SESSION_FIELDS)
        if not version:
            raise TimingError("timing: expected a session timing record")
        assert isinstance(document, dict)
        block = document.get("requests")
        timing = cls(**{field: document[field] for field in _SESSION_FIELDS
                        if field not in ("schema_version", *_DERIVED)},
                     requests=None if block is None else RequestTiming.from_dict(block),
                     schema_version=version)
        # Equality also checks both derived values against the block (null in version 1).
        if timing.to_dict() != document:
            raise TimingError("timing: expected millisecond seconds and derived values")
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


def review_timing(wall_seconds: float, sessions: list[SessionTiming], *,
                  schema_version: int = SCHEMA_VERSION) -> dict[str, object]:
    """The run-level record: review wall clock, session totals and a breakdown per rail.

    A version 2 record sums the sessions' request timing and derives the same two values
    from that sum. Version 1, which earlier runs wrote, has neither.
    """
    rails: dict[tuple[str, str], list[SessionTiming]] = {}
    for session in sessions:
        rails.setdefault((session.provider, session.model), []).append(session)
    requests = (total_requests([s.requests for s in sessions])
                if schema_version == SCHEMA_VERSION else None)
    document: dict[str, object] = {
        "schema_version": schema_version, "wall_seconds": _seconds(wall_seconds, "wall_seconds"),
        **_totals(sessions), **_derived(requests),
        "rails": [{"provider": provider, "model": model, "sessions": len(group),
                   "session_wall_seconds": round(float(sum(s.wall_seconds for s in group)), 3),
                   "tokens": sum(s.tokens for s in group),
                   "output_tokens": sum(s.output_tokens for s in group)}
                  for (provider, model), group in sorted(rails.items())],
    }
    if schema_version == SCHEMA_VERSION:
        document["requests"] = None if requests is None else requests.to_dict()
    return document


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
    long as its longest session. A version 2 record's request timing must be the sum of its
    sessions' blocks, and its two derived values the ones that sum gives.
    """
    version = _version(document, _REVIEW_FIELDS)
    if not version:
        raise TimingError("timing: expected a review timing record")
    assert isinstance(document, dict)
    wall = _seconds(document["wall_seconds"], "wall_seconds")
    rails = document["rails"]
    if (not isinstance(rails, list) or any(
            not isinstance(rail, dict) or set(rail) != set(_RAIL_FIELDS) for rail in rails)):
        raise TimingError("timing: expected a rail breakdown")
    if document != review_timing(wall, sessions, schema_version=version):
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
    """One compact summary line: review time, sessions and tokens.

    Time to first token and decode rate are added only when both were measured, so a line
    never reports one without the other.
    """
    sessions = document["sessions"]
    line = (f"Review time: {_duration(document['wall_seconds'])}, {sessions} "
            f"session{'' if sessions == 1 else 's'}, {document['tokens']} tokens "
            f"({document['output_tokens']} output)")
    first, rate = document.get("first_token_seconds"), document.get("decode_tokens_per_second")
    if isinstance(first, (int, float)) and isinstance(rate, (int, float)):
        line += f", first token {first:.1f} s, {rate:.1f} output tokens per second"
    return line
