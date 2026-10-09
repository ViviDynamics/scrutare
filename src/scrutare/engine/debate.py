"""Bounded perspective discussions and verified senior-chair arbitration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from scrutare import __version__
from scrutare.config import ReviewConfig
from scrutare.engine.debate_inputs import DebateInput
from scrutare.engine.fanout import _await_wave, _prepare_execution, _run_initial_wave
from scrutare.engine.nare_session import run_persona_session
from scrutare.engine.panel import (
    PanelResult,
    _correct,
    _correction_inputs,
    _fatal_correction,
    _validate_binding,
)
from scrutare.engine.panel_artifacts import preflight_panel, publish_panel
from scrutare.engine.review_inputs import ReviewInputError, prepared_hashes
from scrutare.engine.session_artifacts import (
    SessionArtifactError,
    create_attempt_directory,
    write_owned_json,
)
from scrutare.engine.session_models import NareRuntime, ReanchorOutcome, SessionOutcome
from scrutare.findings import Exhaustion, Finding, dedupe_findings, derive_verdict, parse_diff
from scrutare.findings.verification import check_anchors, finish_reanchor
from scrutare.personas import PersonaDefinition, load_persona, resolve_personas
from scrutare.replay.artifacts import decode_artifact, read_artifact


def _usable(outcome: SessionOutcome) -> bool:
    return (
        outcome.status in ("complete", "partial")
        and outcome.output_available
        and outcome.accounting_complete
    )


async def run_debate(run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime) -> PanelResult:
    """Every chair finding is pool-bound; only explicit completed deadlocks exhaust rounds."""
    preflight_panel(run_dir)
    names = {p.name for p in resolve_personas(
        config.personas, procedures=config.inspection.procedures,
    )}
    chair_name = "debate-chair"
    while chair_name in names:
        chair_name += "-chair"
    context = await _prepare_execution(
        run_dir, config, runtime=runtime, budget_personas=(chair_name,)
    )
    hashes = prepared_hashes(context.inputs)
    chair = PersonaDefinition(chair_name, load_persona(
        "senior-dev", procedures=config.inspection.procedures,
    ).system_prompt)
    initial = await _run_initial_wave(context)
    corrections: tuple[ReanchorOutcome, ...] = ()
    verification = None
    verdict = None
    status: Literal["complete", "partial", "failed"] = "failed"
    reason = "initial"
    rounds: list[dict[str, object]] = []
    pool: tuple[Finding, ...] = ()
    try:
        _validate_binding(context, hashes)
        if context.ledger.accounting_complete and all(_usable(o) for o in initial.outcomes):
            anchors = parse_diff((context.inputs.root / "diff.patch").read_bytes())
            check = check_anchors((f for o in initial.outcomes for f in o.findings), anchors)
            descriptors = _correction_inputs(context, check)
            corrections = await _correct(context, descriptors)
            reason = "correction"
            if context.ledger.accounting_complete and not any(
                _fatal_correction(o) for o in corrections
            ):
                verification = finish_reanchor(
                    check, (c for o in corrections for c in o.corrections)
                )
                pool = verification.accepted
                partial = initial.partial or any(o.status != "complete" for o in corrections)
                for number in range(1, config.rounds.max + 1):
                    _validate_binding(context, hashes)
                    positions = tuple(
                        DebateInput(
                            d.persona,
                            context.inputs,
                            pool,
                            (),
                            False,
                            config.verdict.advisory_categories,
                        )
                        for d in context.descriptors
                    )
                    attempts = tuple(
                        create_attempt_directory(
                            context.run_dir,
                            d.persona.name,
                            number + 2,
                            prepared_root=context.inputs.root,
                        )
                        for d in positions
                    )
                    leases = tuple(
                        context.ledger.admit(
                            d.persona.name, f"{d.persona.name}/attempt-{number + 2:04d}"
                        )
                        for d in positions
                    )
                    if any(lease is None for lease in leases):
                        for lease in leases:
                            if lease is not None:
                                context.ledger.settle(lease, lease.baseline, True)
                        reason = "perspective_budget"
                        break
                    outcomes = await _await_wave(
                        [
                            asyncio.create_task(
                                run_persona_session(
                                    d,
                                    config.models.for_persona(d.persona.name),
                                    lease,
                                    ledger=context.ledger,
                                    artifact_directory=attempt,
                                    runtime=runtime,
                                    capability=context.capability,
                                )
                            )
                            for d, lease, attempt in zip(positions, leases, attempts)
                            if lease is not None
                        ]
                    )
                    record: dict[str, object] = {
                        "round": number,
                        "verified_pool": [asdict(f) for f in pool],
                        "positions": [o.to_dict() for o in outcomes],
                        "chair": None,
                        "converged": False,
                    }
                    rounds.append(record)
                    if (
                        not all(_usable(o) for o in outcomes)
                        or not context.ledger.accounting_complete
                    ):
                        reason = "perspectives"
                        break
                    partial = partial or any(o.status != "complete" for o in outcomes)
                    chair_input = DebateInput(
                        chair,
                        context.inputs,
                        pool,
                        tuple(
                            {"persona": o.persona, "findings": [asdict(f) for f in o.findings]}
                            for o in outcomes
                        ),
                        True,
                        config.verdict.advisory_categories,
                    )
                    attempt = create_attempt_directory(
                        context.run_dir, chair_name, number, prepared_root=context.inputs.root
                    )
                    lease = context.ledger.admit(chair_name, f"{chair_name}/attempt-{number:04d}")
                    if lease is None:
                        reason = "chair_budget"
                        break
                    decision = await run_persona_session(
                        chair_input,
                        config.models.for_persona("senior-dev"),
                        lease,
                        ledger=context.ledger,
                        artifact_directory=attempt,
                        runtime=runtime,
                        capability=context.capability,
                    )
                    record["chair"] = decision.to_dict()
                    if not _usable(decision) or not context.ledger.accounting_complete:
                        reason = "chair"
                        break
                    # The executor already reconciled and validated this saved typed output.
                    saved = decode_artifact(
                        read_artifact(attempt / "session.json"), artifact="session"
                    )
                    if not isinstance(saved, dict) or not isinstance(saved.get("output"), dict):
                        raise ReviewInputError("Cannot publish debate: missing chair decision.")
                    output = saved["output"]
                    accepted = chair_input.select(output)
                    converged = output["converged"]
                    record["converged"] = converged
                    partial = partial or decision.status != "complete"
                    if converged:
                        verdict = derive_verdict(dedupe_findings(accepted), config.verdict)
                        status, reason = "partial" if partial else "complete", "converged"
                        break
                    if number == config.rounds.max:
                        verdict = derive_verdict(
                            dedupe_findings(pool),
                            config.verdict,
                            exhaustion=Exhaustion("debate", number, config.rounds.max),
                        )
                        status, reason = "partial" if partial else "complete", "deadlock"
                _validate_binding(context, hashes)
    except (ReviewInputError, SessionArtifactError):
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
        "schema_version": 1,
        "scrutare_version": __version__,
        "head_sha": context.inputs.head_sha,
        "strategy": "debate",
        "status": status,
        "reason": reason,
        "inputs_sha256": hashes,
        "initial": initial.to_dict(),
        "corrections": [o.to_dict() for o in corrections],
        "rounds": rounds,
        "ledger": context.ledger.snapshot(),
    }
    write_owned_json(context.run_dir / "debate.json", document, prepared_root=context.inputs.root)
    publish_panel(context.run_dir, context.inputs.root, document, verdict)
    return result
