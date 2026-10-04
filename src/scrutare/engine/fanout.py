"""One concurrent, budget-admitted wave producing candidate session records."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

from scrutare import __version__
from scrutare.config import ReviewConfig
from scrutare.engine.budgets import BudgetLease, ReviewBudgetLedger
from scrutare.engine.nare_session import inspect_nare_runtime, run_persona_session
from scrutare.engine.persona_inputs import PersonaReviewInput, prepare_persona_inputs
from scrutare.engine.review_inputs import (
    PreparedReviewInputs,
    prepare_review_inputs,
    validate_prepared_inputs,
)
from scrutare.engine.session_artifacts import (
    SessionArtifactError,
    _directory,
    create_attempt_directory,
    write_owned_json,
)
from scrutare.engine.session_models import (
    FanOutResult,
    NareCapability,
    NareRuntime,
    SessionOutcome,
    TokenUsage,
)


def _reserve_wave(run: Path) -> None:
    """Reserve the whole wave exclusively before inspection or child execution."""
    try:
        with _directory(run) as parent:
            try:
                os.stat("fanout.json", dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SessionArtifactError("Cannot reserve wave: existing fanout artifact.")
            os.mkdir("sessions", mode=0o700, dir_fd=parent)
    except OSError:
        raise SessionArtifactError(
            "Cannot reserve wave: existing or unsafe session artifact."
        ) from None


@dataclass(frozen=True)
class _ExecutionContext:
    """One prepared run and its live ledger shared across fresh invocation phases."""

    inputs: PreparedReviewInputs
    descriptors: tuple[PersonaReviewInput, ...]
    ledger: ReviewBudgetLedger
    capability: NareCapability
    runtime: NareRuntime
    config: ReviewConfig
    run_dir: Path
    initial_attempts: tuple[Path, ...]
    initial_leases: tuple[BudgetLease | None, ...]


async def _prepare_execution(
    run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime,
) -> _ExecutionContext:
    """Prepare and reserve every ordered initial grant before inspecting the runtime."""
    inputs = prepare_review_inputs(run_dir, config)
    descriptors = prepare_persona_inputs(inputs, config.personas)
    run = inputs.root.parent
    _reserve_wave(run)
    ledger = ReviewBudgetLedger(tuple(d.persona.name for d in descriptors), config.budgets)
    attempts = tuple(
        create_attempt_directory(run, d.persona.name, prepared_root=inputs.root)
        for d in descriptors
    )
    # Admission is synchronous and ordered: no child can spend before every grant is reserved.
    leases = tuple(
        ledger.admit(d.persona.name, f"{d.persona.name}/attempt-0001") for d in descriptors
    )
    capability = await inspect_nare_runtime(runtime)
    return _ExecutionContext(inputs, descriptors, ledger, capability, runtime, config, run,
                             attempts, leases)


async def _run_initial_wave(context: _ExecutionContext) -> FanOutResult:
    """Settle initial sessions and preserve their immutable manifest beside the live ledger."""
    inputs, descriptors, ledger = context.inputs, context.descriptors, context.ledger
    config, runtime, capability = context.config, context.runtime, context.capability
    run, attempts, leases = context.run_dir, context.initial_attempts, context.initial_leases
    tasks: list[asyncio.Task[SessionOutcome]] = []
    admitted: list[int] = []
    outcomes: list[SessionOutcome | None] = [None] * len(descriptors)
    for index, (descriptor, attempt, lease) in enumerate(zip(descriptors, attempts, leases)):
        if lease is None:
            outcome = SessionOutcome(
                descriptor.persona.name,
                "not_started",
                "review_budget",
                (),
                False,
                TokenUsage(),
                True,
                0,
                0,
                None,
                attempt,
                0,
            )
            outcomes[index] = outcome
            write_owned_json(
                attempt / "result.json",
                {
                    "schema_version": 1,
                    "scrutare_version": __version__,
                    "nare_version": capability.version,
                    "contract": capability.contract,
                    **outcome.to_dict(),
                },
                prepared_root=inputs.root,
            )
    for index, (descriptor, attempt, lease) in enumerate(zip(descriptors, attempts, leases)):
        if lease is not None:
            admitted.append(index)
            tasks.append(
                asyncio.create_task(
                    run_persona_session(
                        descriptor,
                        config.models.for_persona(descriptor.persona.name),
                        lease,
                        ledger=ledger,
                        artifact_directory=attempt,
                        runtime=runtime,
                        capability=capability,
                    )
                )
            )
    try:
        completed = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        # Repeated cancellation cannot cut short process-group cleanup in the executor.
        cleanup = asyncio.gather(*tasks, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        cleanup.result()
        raise
    for index, outcome in zip(admitted, completed):
        outcomes[index] = outcome
    ordered = tuple(outcome for outcome in outcomes if outcome is not None)
    snapshot = ledger.snapshot()
    result = FanOutResult(
        outcomes=ordered,
        failed=any(o.status == "failed" for o in ordered),
        partial=(
            any(o.status in ("partial", "not_started") for o in ordered)
            or not ledger.accounting_complete
        ),
        usage=ledger.usage,
        review_exhausted=ledger.exhausted,
        review_overshoot_tokens=max(0, ledger.usage.total - config.budgets.review_max_tokens),
    )
    validate_prepared_inputs(inputs)
    write_owned_json(
        run / "fanout.json",
        {
            "schema_version": 1,
            "scrutare_version": __version__,
            "head_sha": inputs.head_sha,
            "nare_version": capability.version,
            "contract": capability.contract,
            "personas": [
                {
                    "name": d.persona.name,
                    "system_prompt": d.persona.system_prompt,
                    "rail": asdict(config.models.for_persona(d.persona.name)),
                }
                for d in descriptors
            ],
            "inputs_sha256": {
                name: sha256((inputs.root / name).read_bytes()).hexdigest()
                for name in ("diff.patch", "files.json", "context.json")
            },
            "result": result.to_dict(),
            "ledger": snapshot,
        },
        prepared_root=inputs.root,
    )
    return result


async def fan_out(run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime) -> FanOutResult:
    """Review the exact prepared capture once, leaving verification and verdicts to callers."""
    context = await _prepare_execution(run_dir, config, runtime=runtime)
    return await _run_initial_wave(context)
