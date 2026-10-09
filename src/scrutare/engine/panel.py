"""One independent review wave and one optional anchor correction opportunity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from scrutare import __version__
from scrutare.config import ReviewConfig
from scrutare.engine.fanout import (
    _await_wave,
    _ExecutionContext,
    _prepare_execution,
    _run_initial_wave,
)
from scrutare.engine.nare_session import run_reanchor_session
from scrutare.engine.panel_artifacts import encode_panel, preflight_panel, publish_panel
from scrutare.engine.persona_inputs import PersonaReanchorInput
from scrutare.engine.reanchor import make_reanchor_requests
from scrutare.engine.review_inputs import (
    ReviewInputError,
    _config_snapshot,
    prepared_content_hashes,
    validate_prepared_inputs,
)
from scrutare.engine.session_artifacts import (
    SessionArtifactError,
    create_attempt_directory,
    write_owned_json,
)
from scrutare.engine.session_models import FanOutResult, NareRuntime, ReanchorOutcome, TokenUsage
from scrutare.findings import dedupe_findings, derive_verdict, parse_diff
from scrutare.findings.models import artifact_data as asdict
from scrutare.findings.verdict import Verdict
from scrutare.findings.verification import (
    AnchorCheck,
    VerificationResult,
    check_anchors,
    finish_reanchor,
)


@dataclass(frozen=True)
class PanelResult:
    """Terminal panel evidence with the initial snapshot and final shared usage."""

    status: Literal["complete", "partial", "failed"]
    verdict: Verdict | None
    initial: FanOutResult
    corrections: tuple[ReanchorOutcome, ...]
    verification: VerificationResult | None
    usage: TokenUsage
    accounting_complete: bool
    run_dir: Path


def _validate_binding(context: _ExecutionContext, hashes: dict[str, str]) -> None:
    validate_prepared_inputs(context.inputs)
    _config_snapshot(context.run_dir, context.config)
    if any(sha256((context.inputs.root / name).read_bytes()).hexdigest() != digest
           for name, digest in hashes.items()):
        raise ReviewInputError("Cannot publish panel: original prepared inputs changed.")


def _correction_inputs(context: _ExecutionContext, check: AnchorCheck
                       ) -> tuple[PersonaReanchorInput, ...]:
    return tuple(
        PersonaReanchorInput(context.inputs, descriptor.persona, make_reanchor_requests(originals))
        for descriptor in context.descriptors
        if (originals := tuple(finding for finding in check.reanchor_requests
                               if finding.persona == descriptor.persona.name))
    )


async def _correct(context: _ExecutionContext, descriptors: tuple[PersonaReanchorInput, ...]
                   ) -> tuple[ReanchorOutcome, ...]:
    # Create every destination before reserving; then reserve synchronously before any task runs.
    attempts = tuple(create_attempt_directory(context.run_dir, d.persona.name, 2,
                                              prepared_root=context.inputs.root)
                     for d in descriptors)
    leases = tuple(context.ledger.admit(d.persona.name, f"{d.persona.name}/attempt-0002")
                   for d in descriptors)
    outcomes: list[ReanchorOutcome | None] = [None] * len(descriptors)
    tasks: list[asyncio.Task[ReanchorOutcome]] = []
    admitted: list[int] = []
    try:
        for index, (descriptor, attempt, lease) in enumerate(zip(descriptors, attempts, leases)):
            if lease is None:
                outcome = ReanchorOutcome(descriptor.persona.name, "not_started", "review_budget",
                                          (), False, TokenUsage(), True, 0, 0, None, attempt, 0)
                outcomes[index] = outcome
                write_owned_json(attempt / "result.json", {
                    "schema_version": 1, "scrutare_version": __version__,
                    "nare_version": context.capability.version,
                    "contract": context.capability.contract,
                    "purpose": "reanchor", "requests": [asdict(r) for r in descriptor.requests],
                    **outcome.to_dict(),
                }, prepared_root=context.inputs.root)
    except SessionArtifactError:
        # No correction task exists yet; release reserved zero-use grants on evidence failure.
        for lease in leases:
            if lease is not None:
                context.ledger.settle(lease, TokenUsage(), True)
        raise
    for index, (descriptor, attempt, lease) in enumerate(zip(descriptors, attempts, leases)):
        if lease is not None:
            admitted.append(index)
            tasks.append(asyncio.create_task(run_reanchor_session(
                descriptor, context.config.models.for_persona(descriptor.persona.name), lease,
                ledger=context.ledger, artifact_directory=attempt, runtime=context.runtime,
                capability=context.capability,
            )))
    completed = await _await_wave(tasks)
    for index, outcome in zip(admitted, completed):
        outcomes[index] = outcome
    return tuple(outcome for outcome in outcomes if outcome is not None)


def _fatal_correction(outcome: ReanchorOutcome) -> bool:
    if outcome.status == "failed" or not outcome.accounting_complete:
        return True
    if outcome.status == "not_started":
        return False
    # Only a known budget stop may lack a correction document after usable initial coverage.
    return not outcome.output_available and not (
        outcome.status == "partial" and outcome.reason == "budget")


def _document(context: _ExecutionContext, result: PanelResult, reason: str,
              inputs_hashes: dict[str, str], config_hash: str,
              descriptors: tuple[PersonaReanchorInput, ...]) -> dict[str, object]:
    verification = result.verification
    return {
        "schema_version": 1, "scrutare_version": __version__,
        "head_sha": context.inputs.head_sha, "strategy": "panel", "convergence_passes": 1,
        "status": result.status, "reason": reason,
        "inputs_sha256": inputs_hashes, "config_sha256": config_hash,
        "initial": {"purpose": "review", "result": result.initial.to_dict()},
        "corrections": [
            {"purpose": "reanchor", "requests": [asdict(r) for r in descriptor.requests],
             "result": outcome.to_dict()}
            for descriptor, outcome in zip(descriptors, result.corrections)
        ],
        "verification": None if verification is None else {
            "accepted": [asdict(finding) for finding in verification.accepted],
            "dropped": [asdict(drop) for drop in verification.dropped],
        },
        "ledger": context.ledger.snapshot(),
    }


async def run_panel(run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime) -> PanelResult:
    """Produce replay-compatible final evidence, withholding verdicts on incomplete coverage."""
    preflight_panel(run_dir)
    context = await _prepare_execution(run_dir, config, runtime=runtime)
    hashes = prepared_content_hashes(context.inputs)
    config_hash = sha256(encode_panel(config.to_dict())).hexdigest()
    try:
        initial = await _run_initial_wave(context)
    except (ReviewInputError, SessionArtifactError):
        # Preserve a diagnostic when the owned destination is still safe. The initial
        # executor may have retained outcomes only in its session files before raising.
        publish_panel(context.run_dir, context.inputs.root, {
            "schema_version": 1, "scrutare_version": __version__,
            "head_sha": context.inputs.head_sha, "strategy": "panel", "convergence_passes": 1,
            "status": "failed", "reason": "initial_evidence",
            "inputs_sha256": hashes, "config_sha256": config_hash,
            "initial": {"purpose": "review", "result": None}, "corrections": [],
            "verification": None, "ledger": context.ledger.snapshot(),
        }, None)
        raise
    corrections: tuple[ReanchorOutcome, ...] = ()
    descriptors: tuple[PersonaReanchorInput, ...] = ()
    verification = None
    verdict = None
    reason = "initial"
    status: Literal["complete", "partial", "failed"] = "failed"
    eligible = context.ledger.accounting_complete and all(
        outcome.status in ("complete", "partial") and outcome.output_available
        and outcome.accounting_complete for outcome in initial.outcomes)
    if config.findings.assessment.enabled and initial.partial:
        eligible, reason, status = False, "discovery_incomplete", "partial"
    try:
        _validate_binding(context, hashes)
        if eligible:
            anchors = parse_diff((context.inputs.root / "diff.patch").read_bytes())
            check = check_anchors((finding for outcome in initial.outcomes
                                   for finding in outcome.findings), anchors)
            descriptors = _correction_inputs(context, check)
            corrections = await _correct(context, descriptors)
            reason = "correction"
            if context.ledger.accounting_complete and not any(
                    _fatal_correction(outcome) for outcome in corrections):
                verification = finish_reanchor(check, (correction for outcome in corrections
                                                       for correction in outcome.corrections))
                status = ("partial" if initial.partial or any(
                    outcome.status != "complete" for outcome in corrections) else "complete")
                reason = "converged"
                accepted = verification.accepted
                unresolved: tuple[str, ...] = ()
                if config.findings.assessment.enabled and status != "complete":
                    reason = "assessment_incomplete"
                elif config.findings.assessment.enabled:
                    from scrutare.engine.assessment import assess_candidates
                    assessment = await assess_candidates(context, accepted)
                    if assessment.status != "complete":
                        status, reason = assessment.status, "assessment_incomplete"
                    else:
                        accepted = assessment.retained
                        unresolved = assessment.unresolved_blocking(
                            config.verdict.blocking_categories)
                        if unresolved:
                            status, reason = "partial", "semantic_uncertainty"
                if reason != "assessment_incomplete":
                    verdict = derive_verdict(
                        dedupe_findings(accepted), config.verdict,
                        evidence_version=2 if config.findings.evidence == "v2" else 1,
                        unresolved_candidates=unresolved)
        _validate_binding(context, hashes)
    except ReviewInputError:
        status, reason, verdict = "failed", "inputs", None
    except SessionArtifactError:
        status, reason, verdict = "failed", "artifacts", None
    result = PanelResult(status, verdict, initial, corrections, verification, context.ledger.usage,
                         context.ledger.accounting_complete, context.run_dir)
    document = _document(context, result, reason, hashes, config_hash, descriptors)
    publish_panel(context.run_dir, context.inputs.root, document, verdict)
    return result
