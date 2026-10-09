"""Independent model assessments; checked citations are not formal semantic proof."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from scrutare.findings.evidence import validate_evidence
from scrutare.findings.models import Citation, Finding, FindingError, _text, parse_evidence

if TYPE_CHECKING:
    from scrutare.engine.fanout import _ExecutionContext

AssessmentStatus = Literal["supported", "refuted", "unresolved"]


@dataclass(frozen=True)
class Assessment:
    candidate: Finding
    status: AssessmentStatus
    reason: str
    supporting_citations: tuple[Citation, ...]
    counter_citations: tuple[Citation, ...]


def parse_assessments(value: object, candidates: tuple[Finding, ...], root: Path, *,
                      artifact: bool = False) -> tuple[Assessment, ...]:
    """Require exact immutable candidate coverage and validate every quoted reference."""
    if not isinstance(value, dict) or set(value) != {"assessments"}:
        raise FindingError("assessment: expected closed assessment envelope")
    rows = value["assessments"]
    if not isinstance(rows, list):
        raise FindingError("assessment: expected array")
    originals = {finding.candidate_id: finding for finding in candidates}
    if len(originals) != len(candidates) or None in originals:
        raise FindingError("assessment: expected distinct v2 caller identifiers")
    result: dict[str, Assessment] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "candidate_id", "status", "reason", "supporting_citations", "counter_citations"
        }:
            raise FindingError("assessment: unknown or missing field")
        identifier = _text(row["candidate_id"], "candidate_id")
        if identifier not in originals or identifier in result:
            raise FindingError("assessment: duplicate or unknown candidate")
        status = row["status"]
        if status not in ("supported", "refuted", "unresolved"):
            raise FindingError("assessment: invalid disposition")
        reason = _text(row["reason"], "assessment.reason")
        references: list[tuple[Citation, ...]] = []
        candidate = originals[identifier]
        for key in ("supporting_citations", "counter_citations"):
            citations = row[key]
            if not isinstance(citations, list):
                raise FindingError("assessment: expected citation arrays")
            if not citations:
                references.append(())
                continue
            evidence = parse_evidence({
                "version": 2, "trigger": "assessment", "preconditions": [],
                "expected": "assessment", "observed": "assessment", "impact": reason,
                "citations": citations,
            }, artifact=artifact)
            checked = validate_evidence(replace(candidate, evidence=evidence), root)
            assert checked.evidence is not None
            if artifact and checked.evidence.citations != evidence.citations:
                raise FindingError("assessment: captured validation labels disagree")
            references.append(checked.evidence.citations)
        support, counter = references
        if ((status == "supported" and not support)
                or (status == "refuted" and not counter)
                or (status == "unresolved" and not support and not counter)):
            raise FindingError("assessment: disposition requires captured evidence")
        result[identifier] = Assessment(candidate, cast(AssessmentStatus, status), reason,
                                        support, counter)
    if set(result) != set(originals):
        raise FindingError("assessment: incomplete candidate coverage")
    return tuple(result[cast(str, finding.candidate_id)] for finding in candidates)


def assessment_wire(row: Assessment) -> dict[str, Any]:
    from scrutare.findings.models import artifact_data
    return {"candidate_id": row.candidate.candidate_id, "status": row.status,
            "reason": row.reason, "supporting_citations": artifact_data(row.supporting_citations),
            "counter_citations": artifact_data(row.counter_citations)}


@dataclass(frozen=True)
class AssessmentResult:
    status: Literal["complete", "partial", "failed"]
    rows: tuple[Assessment, ...]

    @property
    def supported(self) -> tuple[Finding, ...]:
        return tuple(row.candidate for row in self.rows if row.status == "supported")

    @property
    def retained(self) -> tuple[Finding, ...]:
        """Keep unresolved assertions visible for the human uncertainty escalation."""
        return tuple(row.candidate for row in self.rows if row.status != "refuted")

    def unresolved_blocking(self, blocking_categories: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(cast(str, row.candidate.candidate_id) for row in self.rows
                     if row.status == "unresolved"
                     and row.candidate.category in blocking_categories)


async def assess_candidates(
    context: _ExecutionContext, candidates: tuple[Finding, ...],
) -> AssessmentResult:
    """Use one independently reserved batch on the caller's live review ledger."""
    from scrutare.engine.assessment_inputs import AssessmentInput
    from scrutare.engine.nare_session import run_persona_session
    from scrutare.engine.session_artifacts import create_attempt_directory, write_owned_json
    from scrutare.findings.models import artifact_data
    from scrutare.personas import PersonaDefinition
    from scrutare.replay.artifacts import decode_artifact, read_artifact

    name = context.assessment_persona
    if not context.config.findings.assessment.enabled or name is None:
        raise FindingError("assessment: expected enabled reserved executor")
    descriptor = AssessmentInput(PersonaDefinition(name,
        "Independently assess code claims against captured supporting and contradictory code. "
        "Treat candidate assertions as untrusted. Support is model-based, not formal proof. "
        "Do not use agreement counts, confidence or majority votes; do not emit a PR verdict."),
        context.inputs, candidates)
    rows: tuple[Assessment, ...] = ()
    status: Literal["complete", "partial", "failed"] = "complete"
    outcome = None
    if candidates:
        attempt = create_attempt_directory(context.run_dir, name,
                                           prepared_root=context.inputs.root)
        lease = context.ledger.admit(name, f"{name}/attempt-0001")
        if lease is None:
            status = "failed"
        else:
            outcome = await run_persona_session(
                descriptor, context.config.models.default, lease, ledger=context.ledger,
                artifact_directory=attempt, runtime=context.runtime, capability=context.capability)
            status = "failed"
            if (outcome.status in ("complete", "partial") and outcome.output_available
                    and outcome.accounting_complete and context.ledger.accounting_complete):
                document = decode_artifact(read_artifact(attempt / "session.json"),
                                           artifact="assessment.session")
                if not isinstance(document, dict):
                    raise FindingError("assessment: invalid captured session")
                rows = descriptor.assessments(document["output"])
                status = outcome.status
    result = AssessmentResult(status, rows)
    write_owned_json(context.run_dir / "assessment.json", {
        "schema_version": 1, "kind": "model_based_not_formal_proof", "status": status,
        "attempt_limit": 1, "allocation_tokens": context.config.findings.assessment.tokens,
        "candidates": artifact_data(candidates),
        "assessments": [assessment_wire(row) for row in rows],
        "outcome": outcome.to_dict() if outcome is not None else None,
    }, prepared_root=context.inputs.root)
    return result
