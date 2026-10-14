"""Reconstruct recorded policy transitions; never certify model assertions as truth."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from scrutare.config import ReviewConfig
from scrutare.engine.assessment import Assessment, assessment_wire, parse_assessments
from scrutare.engine.discussion import Proposal, disputed_ids, parse_proposals, proposal_wire
from scrutare.engine.overrun import phase_degradation
from scrutare.findings import Exhaustion
from scrutare.findings.models import artifact_data, finding_from_artifact
from scrutare.findings.verdict import Degradation
from scrutare.replay.artifacts import decode_artifact, read_artifact
from scrutare.replay.models import ReplayError


def _document(path: Path) -> dict[str, object]:
    value = decode_artifact(read_artifact(path), artifact=path.name)
    if not isinstance(value, dict):
        raise ValueError("discussion: expected object")
    return value


def replay_discussion(
    run_dir: Path, config: ReviewConfig, rows: tuple[Assessment, ...], exhaustion: Exhaustion | None
) -> tuple[tuple[Assessment, ...], Degradation | None]:
    root = run_dir / "review-inputs"
    trace = _document(run_dir / "debate.json")
    rounds = trace.get("rounds")
    # Only the opt-in policy may end discussion on a recorded, measured overrun.
    fallback = "degraded" in trace and config.findings.discussion.overrun == "fallback"
    degradation: Degradation | None = None
    if (
        trace.get("schema_version") != 2
        or trace.get("kind") != "focused_evidence_discussion"
        or trace.get("status") not in ("complete", "partial")
        or (fallback and trace.get("status") != "partial")
        or not isinstance(rounds, list)
        or len(rounds) > config.rounds.max
    ):
        raise ValueError("discussion: incomplete trace")
    bypass = [
        row.candidate.candidate_id
        for row in rows
        if row.status == "supported" and row.candidate.candidate_id not in disputed_ids(rows)
    ]
    if trace.get("bypassed_candidate_ids") != bypass:
        raise ValueError("discussion: bypass policy disagrees")
    initial_assessment = _document(run_dir / "assessment.json")
    initial_outcome = initial_assessment.get("outcome")
    assessor_used = _tokens(initial_outcome) if isinstance(initial_outcome, dict) else 0
    remaining = disputed_ids(rows)
    for number, record in enumerate(rounds, 1):
        if not remaining or not isinstance(record, dict) or record.get("round") != number:
            raise ValueError("discussion: unexpected round")
        identifiers = disputed_ids(rows)
        if record.get("candidate_ids") != list(identifiers):
            raise ValueError("discussion: disputed pool disagrees")
        selected = tuple(row for row in rows if row.candidate.candidate_id in identifiers)
        pool = tuple(row.candidate for row in selected)
        positions = record.get("positions")
        proposals = record.get("proposals")
        if not isinstance(positions, list) or not isinstance(proposals, list):
            raise ValueError("discussion: missing positions")
        from scrutare.personas import resolve_personas

        if config.routing.enabled:
            from scrutare.engine.routing import plan_routing

            focus = _document(root / "routing-focus.json")
            supporting = focus.get("global_supporting_paths")
            if not isinstance(supporting, list) or any(
                not isinstance(path, str) for path in supporting
            ):
                raise ValueError("discussion: invalid routed supporting paths")
            files = decode_artifact(read_artifact(root / "files.json"), artifact="files.json")
            plan = plan_routing(files, tuple(supporting), config)
            if plan.record != focus:
                raise ValueError("discussion: captured routing plan disagrees")
            names = [p.name for p in plan.personas]
        else:
            names = [p.name for p in resolve_personas(
                config.personas, procedures=config.inspection.procedures
            )]
        if [p.get("persona") for p in positions if isinstance(p, dict)] != names:
            raise ValueError("discussion: perspective coverage disagrees")
        if fallback and number == len(rounds) and not all(
                isinstance(p, dict) and _usable(p) for p in positions):
            # The positions overran: nothing from this round may change a disposition.
            degradation = phase_degradation(
                "discussion", positions, round_number=number,
                allowance=config.findings.discussion.tokens)
            if (degradation is None or proposals != [] or record.get("reassessment") is not None
                    or record.get("disposition_changes") != []
                    or record.get("new_citations") != []
                    or record.get("tokens") != sum(_tokens(p) for p in positions)):
                raise ValueError("discussion: fallback round disagrees")
            break
        parsed: list[Proposal] = []
        total_tokens = 0
        for position in positions:
            if not isinstance(position, dict):
                raise ValueError
            _complete(position)
            persona = cast(str, position["persona"])
            session = _document(
                run_dir / "sessions" / persona / f"attempt-{number + 2:04d}" / "session.json"
            )
            parsed.extend(parse_proposals(session["output"], pool, root))
            total_tokens += _tokens(position)
        if proposals != [proposal_wire(p) for p in parsed]:
            raise ValueError("discussion: proposal history disagrees")
        # Validate the serialized derived refusal labels too.
        for start in range(0, len(proposals), len(pool)):
            parse_proposals(
                {"proposals": proposals[start : start + len(pool)]}, pool, root, artifact=True
            )
        needs = any(row.status in ("unresolved", "refuted") for row in selected) or any(
            p.action in ("refute", "unresolved") and p.transition_supported for p in parsed
        )
        if needs:
            filename = f"assessment-round-{number:04d}.json"
            if record.get("reassessment") != filename:
                raise ValueError("discussion: missing independent reassessment")
            assessment = _document(run_dir / filename)
            if (
                assessment.get("schema_version") != 1
                or assessment.get("kind") != "model_based_not_formal_proof"
                or assessment.get("status") != "complete"
                or assessment.get("allocation_tokens") != config.findings.assessment.tokens
                or assessment.get("attempt_limit") != 1
            ):
                raise ValueError("discussion: invalid independent reassessment")
            originals = tuple(
                finding_from_artifact(item, evidence_version=2)
                for item in cast(list[dict[str, Any]], assessment["candidates"])
            )
            if originals != pool:
                raise ValueError("discussion: reassessment rewrote candidates")
            outcome = assessment["outcome"]
            if not isinstance(outcome, dict):
                raise ValueError
            _complete(outcome)
            allocation = outcome.get("allocated_tokens")
            if (
                type(allocation) is not int
                or allocation != config.findings.assessment.tokens - assessor_used
                or allocation <= 0
            ):
                raise ValueError("discussion: assessor allocation disagrees")
            updated_rows = parse_assessments(
                {"assessments": assessment["assessments"]}, pool, root, artifact=True
            )
            assessor_name = outcome.get("persona")
            if not isinstance(assessor_name, str) or (
                not isinstance(initial_outcome, dict)
                or assessor_name != initial_outcome.get("persona")
            ):
                raise ValueError("discussion: reassessment executor disagrees")
            session = _document(
                run_dir / "sessions" / assessor_name / f"attempt-{number + 1:04d}" / "session.json"
            )
            native_rows = parse_assessments(session["output"], pool, root)
            if native_rows != updated_rows:
                raise ValueError("discussion: reassessment disagrees with native output")
            assessor_used += _tokens(outcome)
            if assessor_used > config.findings.assessment.tokens:
                raise ValueError("discussion: assessor exceeded original reservation")
            updated = {row.candidate.candidate_id: row for row in updated_rows}
            changes = [
                {
                    "candidate_id": row.candidate.candidate_id,
                    "before": row.status,
                    "after": updated[row.candidate.candidate_id].status,
                    "reason": updated[row.candidate.candidate_id].reason,
                }
                for row in selected
                if row.status != updated[row.candidate.candidate_id].status
            ]
            if record.get("disposition_changes") != changes:
                raise ValueError("discussion: unauthorized transition")
            rows = tuple(updated.get(row.candidate.candidate_id, row) for row in rows)
            total_tokens += _tokens(outcome)
        elif record.get("reassessment") is not None or record.get("disposition_changes") != []:
            raise ValueError("discussion: unsupported opposition changed disposition")
        before_refs = {
            c for row in selected for c in row.supporting_citations + row.counter_citations
        }
        new_refs = [
            artifact_data(p.citations)
            for p in parsed
            if any(c not in before_refs for c in p.citations)
        ]
        if record.get("new_citations") != new_refs or record.get("tokens") != total_tokens:
            raise ValueError("discussion: evidence or token trace disagrees")
        remaining = disputed_ids(rows) if needs else ()
    if fallback and (degradation is None or trace.get("degraded") != degradation.to_dict()):
        raise ValueError("discussion: recorded fallback disagrees")
    if degradation is None and remaining and len(rounds) != config.rounds.max:
        raise ValueError("discussion: uncertainty before bound")
    expected = (Exhaustion("debate", len(rounds), config.rounds.max)
                if remaining and degradation is None else None)
    if exhaustion != expected:
        raise ValueError("discussion: bound disagrees")
    if trace.get("final_assessments") != [assessment_wire(row) for row in rows]:
        raise ValueError("discussion: final states disagree")
    return rows, degradation


def _usable(outcome: dict[str, object]) -> bool:
    return (outcome.get("status") == "complete" and outcome.get("output_available") is True
            and outcome.get("accounting_complete") is True)


def _complete(outcome: dict[str, object]) -> None:
    if (
        outcome.get("status") != "complete"
        or outcome.get("output_available") is not True
        or outcome.get("accounting_complete") is not True
    ):
        raise ValueError("discussion: technical failure is not convergence")


def _tokens(outcome: dict[str, object]) -> int:
    usage = outcome.get("usage")
    if not isinstance(usage, dict) or type(usage.get("total")) is not int or usage["total"] < 0:
        raise ReplayError("discussion: missing accounting")
    return cast(int, usage["total"])
