"""Evidence disputes preserve immutable sources; only independent assessment changes status."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from scrutare.config import ReviewConfig
from scrutare.engine.assessment import (
    Assessment,
    AssessmentResult,
    assess_candidates,
    assessment_fallback,
    assessment_wire,
)
from scrutare.engine.budgets import BudgetLease
from scrutare.engine.discussion import Proposal, disputed_ids, equivalent_groups, proposal_wire
from scrutare.engine.discussion_inputs import DiscussionInput
from scrutare.engine.fanout import (
    _await_wave,
    _ExecutionContext,
    _prepare_execution,
    _run_initial_wave,
)
from scrutare.engine.nare_session import run_persona_session
from scrutare.engine.overrun import phase_degradation
from scrutare.engine.panel import (
    PanelResult,
    _correct,
    _correction_inputs,
    _fatal_correction,
    _validate_binding,
)
from scrutare.engine.panel_artifacts import preflight_panel, publish_panel
from scrutare.engine.review_inputs import ReviewInputError, prepared_content_hashes
from scrutare.engine.session_artifacts import (
    SessionArtifactError,
    accepted_session_path,
    create_attempt_directory,
    write_owned_json,
)
from scrutare.engine.session_models import NareRuntime, ReanchorOutcome
from scrutare.findings import Exhaustion, derive_verdict, parse_diff
from scrutare.findings.models import FindingError
from scrutare.findings.verdict import Degradation, Verdict
from scrutare.findings.verification import check_anchors, finish_reanchor
from scrutare.replay.artifacts import decode_artifact, read_artifact


async def run_focused_debate(
    run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime
) -> PanelResult:
    preflight_panel(run_dir)
    context = await _prepare_execution(run_dir, config, runtime=runtime)
    hashes = prepared_content_hashes(context.inputs)
    initial = await _run_initial_wave(context)
    corrections: tuple[ReanchorOutcome, ...] = ()
    verification = verdict = None
    status: Literal["complete", "partial", "failed"] = "failed"
    reason = "discovery_incomplete"
    rows: tuple[Assessment, ...] = ()
    rounds: list[dict[str, object]] = []
    bypassed: tuple[str, ...] = ()
    degradation: Degradation | None = None
    try:
        _validate_binding(context, hashes)
        if (
            not initial.partial
            and context.ledger.accounting_complete
            and all(
                o.status == "complete" and o.output_available and o.accounting_complete
                for o in initial.outcomes
            )
        ):
            check = check_anchors(
                (f for o in initial.outcomes for f in o.findings),
                parse_diff((context.inputs.root / "diff.patch").read_bytes()),
            )
            corrections = await _correct(context, _correction_inputs(context, check))
            if context.ledger.accounting_complete and not any(
                _fatal_correction(o) or o.status != "complete" for o in corrections
            ):
                verification = finish_reanchor(
                    check, (c for o in corrections for c in o.corrections)
                )
                assessment = await assess_candidates(context, verification.accepted)
                reason = "assessment_incomplete"
                status = assessment.status
                if assessment.status != "complete":
                    # Opt-in: a measured overrun keeps the anchor-validated findings.
                    degradation = assessment_fallback(context, assessment)
                    if degradation is not None:
                        verdict = derive_verdict(
                            equivalent_groups(verification.accepted), config.verdict,
                            evidence_version=2, degradation=degradation)
                        status, reason = "partial", "assessment_overrun_fallback"
                else:
                    rows = assessment.rows
                    disputed = disputed_ids(rows)
                    bypassed = tuple(
                        cast(str, row.candidate.candidate_id)
                        for row in rows
                        if row.status == "supported" and row.candidate.candidate_id not in disputed
                    )
                    if disputed:
                        rows, rounds, status, reason, verdict = await _discuss(
                            context, rows, hashes
                        )
                    else:
                        verdict = derive_verdict(
                            equivalent_groups(assessment.retained),
                            config.verdict,
                            evidence_version=2,
                        )
                        status, reason = "complete", "undisputed"
        _validate_binding(context, hashes)
    except (ReviewInputError, SessionArtifactError, FindingError):
        status, reason, verdict = "failed", "evidence", None
    result = PanelResult(
        status,
        verdict,
        initial,
        corrections,
        verification,
        context.ledger.usage,
        context.ledger.accounting_complete,
        context.run_dir,
    )
    document = {
        "schema_version": 2,
        "kind": "focused_evidence_discussion",
        "strategy": "debate",
        "head_sha": context.inputs.head_sha,
        "status": status,
        "reason": reason,
        "inputs_sha256": hashes,
        "initial": initial.to_dict(),
        "corrections": [o.to_dict() for o in corrections],
        "bypassed_candidate_ids": list(bypassed),
        "rounds": rounds,
        "final_assessments": [assessment_wire(row) for row in rows],
        "ledger": context.ledger.snapshot(),
    }
    if verdict is not None and verdict.degradation is not None:
        document["degraded"] = verdict.degradation.to_dict()
    write_owned_json(context.run_dir / "debate.json", document, prepared_root=context.inputs.root)
    publish_panel(context.run_dir, context.inputs.root, document, verdict)
    return result


async def _discuss(
    context: _ExecutionContext, rows: tuple[Assessment, ...], hashes: dict[str, str]
) -> tuple[
    tuple[Assessment, ...],
    list[dict[str, object]],
    Literal["complete", "partial", "failed"],
    str,
    Verdict | None,
]:
    """Only independently reassessed rows change disposition; peers never count as votes."""
    rounds: list[dict[str, object]] = []
    allowance = context.config.findings.discussion.tokens is not None
    for number in range(1, context.config.rounds.max + 1):
        _validate_binding(context, hashes)
        identifiers = disputed_ids(rows)
        selected = tuple(row for row in rows if row.candidate.candidate_id in identifiers)
        pool = tuple(row.candidate for row in selected)
        started, usage_before = monotonic(), context.ledger.usage.total
        descriptors = tuple(
            DiscussionInput(
                d.persona,
                context.inputs,
                pool,
                tuple(assessment_wire(row) for row in selected),
                False,
                (),
            )
            for d in context.descriptors
        )
        attempts = tuple(
            create_attempt_directory(
                context.run_dir, d.persona.name, number + 2, prepared_root=context.inputs.root
            )
            for d in descriptors
        )
        keys = tuple((d.persona.name, f"{d.persona.name}/attempt-{number + 2:04d}")
                     for d in descriptors)
        leases: tuple[BudgetLease | None, ...]
        if allowance:
            # A separate discussion allowance: positions never spend discovery grants.
            admitted = context.ledger.admit_phase("discussion", keys)
            leases = admitted if admitted is not None else (None,)
        else:
            leases = tuple(context.ledger.admit(persona, key) for persona, key in keys)
        if any(lease is None for lease in leases):
            for lease in leases:
                if lease is not None:
                    context.ledger.settle(lease, lease.baseline, True)
            degradation = _refused_fallback(context, number)
            if degradation is None:
                return rows, rounds, "failed", "perspective_budget", None
            # The round could not start: its grants are spent. Keep the completed rounds.
            rounds.append({"round": number, "candidate_ids": list(identifiers),
                           "admission": "refused", "positions": [], "proposals": [],
                           "reassessment": None, "disposition_changes": [],
                           "new_citations": [], "tokens": 0, "latency_seconds": 0.0})
            return _fall_back(context, rows, rounds, degradation)
        outcomes = await _await_wave(
            [
                asyncio.create_task(
                    run_persona_session(
                        d,
                        context.config.models.for_persona(d.persona.name),
                        lease,
                        ledger=context.ledger,
                        artifact_directory=attempt,
                        runtime=context.runtime,
                        capability=context.capability,
                    )
                )
                for d, lease, attempt in zip(descriptors, leases, attempts)
                if lease is not None
            ]
        )
        record: dict[str, object] = {
            "round": number,
            "candidate_ids": list(identifiers),
            "positions": [o.to_dict() for o in outcomes],
            "proposals": [],
            "reassessment": None,
            "disposition_changes": [],
            "new_citations": [],
            "tokens": 0,
            "latency_seconds": 0.0,
        }
        rounds.append(record)
        if not context.ledger.accounting_complete or any(
            o.status != "complete" or not o.output_available or not o.accounting_complete
            for o in outcomes
        ):
            _finish_round(record, context, usage_before, started)
            degradation = _discussion_fallback(context, number, [o.to_dict() for o in outcomes])
            if degradation is None:
                return rows, rounds, "failed", "perspectives_incomplete", None
            return _fall_back(context, rows, rounds, degradation)
        parsed_proposals: list[Proposal] = []
        for descriptor, attempt in zip(descriptors, attempts):
            session = decode_artifact(
                read_artifact(accepted_session_path(attempt)), artifact="discussion.session"
            )
            if not isinstance(session, dict):
                raise FindingError("discussion: missing captured session")
            parsed_proposals.extend(descriptor.proposals(session["output"]))
        proposals = tuple(parsed_proposals)
        record["proposals"] = [proposal_wire(p) for p in proposals]
        before_refs = {
            citation
            for row in selected
            for citation in row.supporting_citations + row.counter_citations
        }
        record["new_citations"] = [
            proposal_wire(p)["citations"]
            for p in proposals
            if any(c not in before_refs for c in p.citations)
        ]
        needs_assessment = any(row.status in ("unresolved", "refuted") for row in selected) or any(
            p.action in ("refute", "unresolved") and p.transition_supported for p in proposals
        )
        if needs_assessment:
            assessment = await assess_candidates(
                context,
                pool,
                attempt_number=number + 1,
                counterevidence=tuple(proposal_wire(p) for p in proposals),
                phase="discussion" if allowance else None,
            )
            record["reassessment"] = f"assessment-round-{number:04d}.json"
            if assessment.status != "complete":
                _finish_round(record, context, usage_before, started)
                if assessment.outcome is None:
                    # Refused admission: the discussion grants are spent.
                    degradation = _refused_fallback(context, number)
                    if degradation is not None:
                        record["admission"] = "refused"
                else:
                    degradation = _discussion_fallback(
                        context, number, [assessment.outcome.to_dict()])
                if degradation is None:
                    return rows, rounds, assessment.status, "reassessment_incomplete", None
                return _fall_back(context, rows, rounds, degradation)
            updated = {row.candidate.candidate_id: row for row in assessment.rows}
            record["disposition_changes"] = [
                {
                    "candidate_id": row.candidate.candidate_id,
                    "before": row.status,
                    "after": updated[row.candidate.candidate_id].status,
                    "reason": updated[row.candidate.candidate_id].reason,
                }
                for row in selected
                if row.status != updated[row.candidate.candidate_id].status
            ]
            rows = tuple(updated.get(row.candidate.candidate_id, row) for row in rows)
        _finish_round(record, context, usage_before, started)
        _validate_binding(context, hashes)
        unresolved = AssessmentResult("complete", rows).unresolved_blocking(
            context.config.verdict.blocking_categories
        )
        # A supported assertion survives even when peer opposition was uncited. Only
        # uncertainty or new independent contradictory assessments justify another round.
        remaining = disputed_ids(rows) if needs_assessment else ()
        if not remaining or number == context.config.rounds.max:
            retained = AssessmentResult("complete", rows).retained
            exhausted = bool(remaining)
            verdict = derive_verdict(
                equivalent_groups(retained),
                context.config.verdict,
                evidence_version=2,
                unresolved_candidates=unresolved,
                exhaustion=Exhaustion("debate", number, context.config.rounds.max)
                if exhausted
                else None,
            )
            return (
                rows,
                rounds,
                ("partial" if exhausted or unresolved else "complete"),
                ("deadlock" if exhausted else "resolved"),
                verdict,
            )
    raise AssertionError("positive round bound required")


def _discussion_fallback(context: _ExecutionContext, number: int,
                         outcomes: list[dict[str, object]]) -> Degradation | None:
    """The opt-in degraded record for a measured discussion overrun, else None (void)."""
    settings = context.config.findings.discussion
    if settings.overrun != "fallback" or not context.ledger.accounting_complete:
        return None
    return phase_degradation("discussion", outcomes, round_number=number,
                             allowance=settings.tokens)


def _refused_fallback(context: _ExecutionContext, number: int) -> Degradation | None:
    """Opt-in: admission refused for spent discussion grants, with complete accounting."""
    settings = context.config.findings.discussion
    if settings.overrun != "fallback" or not context.ledger.accounting_complete:
        return None
    return Degradation("discussion", number, settings.tokens, 0, 0, 0, "pre_round_assessment")


def _fall_back(
    context: _ExecutionContext, rows: tuple[Assessment, ...], rounds: list[dict[str, object]],
    degradation: Degradation,
) -> tuple[tuple[Assessment, ...], list[dict[str, object]], Literal["partial"], str, Verdict]:
    """Keep the last complete (pre-round) assessment; uncertainty still escalates."""
    result = AssessmentResult("complete", rows)
    verdict = derive_verdict(
        equivalent_groups(result.retained), context.config.verdict, evidence_version=2,
        unresolved_candidates=result.unresolved_blocking(
            context.config.verdict.blocking_categories),
        degradation=degradation)
    return rows, rounds, "partial", "discussion_overrun_fallback", verdict


def _finish_round(record: dict[str, object], context: _ExecutionContext,
                  usage_before: int, started: float) -> None:
    record["tokens"] = context.ledger.usage.total - usage_before
    record["latency_seconds"] = monotonic() - started
