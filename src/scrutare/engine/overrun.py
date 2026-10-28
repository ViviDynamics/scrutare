"""Recognise a measured phase overrun; any other failure still voids the review.

The engine and replay both derive the degraded record here from the same retained outcome
records, so replay reconstructs the fallback from evidence without running a model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, cast

from scrutare.findings.verdict import Degradation


def _usage_total(outcome: Mapping[str, object]) -> int:
    usage = outcome.get("usage")
    total = usage.get("total") if isinstance(usage, Mapping) else None
    if type(total) is not int or total < 0:
        raise ValueError("overrun: missing accounting")
    return total


def _count(outcome: Mapping[str, object], field: str) -> int:
    value = outcome.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(f"overrun: invalid {field}")
    return value


def overran(outcome: Mapping[str, object]) -> bool:
    """An admitted session stopped for its budget with complete, exact accounting."""
    return (outcome.get("status") == "partial" and outcome.get("reason") == "budget"
            and outcome.get("accounting_complete") is True)


def artifact_status(outcome: Mapping[str, object]) -> str:
    """The assessment artifact status an overrun outcome records (no output: failed)."""
    return "partial" if outcome.get("output_available") is True else "failed"


def native_overrun(directory: Path, outcome: Mapping[str, object]) -> None:
    """Confirm a recorded overrun against the native session nare saved, or raise ValueError.

    The saved session must carry the granted budget and the recorded usage, and show either
    a budget stop or a finished turn past that budget.
    """
    from scrutare.engine.session_artifacts import accepted_session_path
    from scrutare.replay.artifacts import decode_artifact, read_artifact

    session = decode_artifact(read_artifact(accepted_session_path(directory)),
                              artifact="session.json")
    if not isinstance(session, dict):
        raise ValueError("overrun: missing native session")
    budget = session.get("budget")
    usage = session.get("usage")
    recorded = outcome.get("usage")
    fields = ("input", "output", "cache_read", "cache_write")
    if (not isinstance(budget, dict) or not isinstance(usage, dict)
            or not isinstance(recorded, Mapping)):
        raise ValueError("overrun: incomplete native session")
    limit = budget.get("tokens")
    if (type(limit) is not int or limit != outcome.get("allocated_tokens")
            or limit != outcome.get("invocation_limit")
            or any(usage.get(field) != recorded.get(field) for field in fields)):
        raise ValueError("overrun: native session disagrees with the recorded outcome")
    total = sum(cast(int, usage[field]) for field in fields)
    stopped = session.get("status") == "error" and session.get("stop_reason") == "budget"
    if total != _usage_total(outcome) or not (stopped or (
            session.get("status") == "done" and total > limit)):
        raise ValueError("overrun: native session shows no budget overrun")


def phase_degradation(
    phase: Literal["assessment", "discussion"], outcomes: Sequence[Mapping[str, object]], *,
    round_number: int | None, allowance: int | None,
) -> Degradation | None:
    """Return the degraded record when the step's only failures are measured overruns.

    Every session in the step must have been admitted and accounted exactly; at least one
    ended for its budget, and every other one completed. A failed, malformed, refused or
    uncertainly accounted session is not an overrun and returns None.
    """
    if not outcomes or not any(overran(outcome) for outcome in outcomes):
        return None
    if any(not overran(outcome) and not (
            outcome.get("status") == "complete" and outcome.get("output_available") is True
            and outcome.get("accounting_complete") is True) for outcome in outcomes):
        return None
    return Degradation(
        phase, round_number, allowance,
        sum(_count(outcome, "allocated_tokens") for outcome in outcomes),
        sum(_usage_total(outcome) for outcome in outcomes),
        sum(_count(outcome, "overshoot_tokens") for outcome in outcomes),
        "anchor_validated" if phase == "assessment" else "pre_round_assessment",
    )
