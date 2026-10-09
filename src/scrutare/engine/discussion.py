"""Evidence-based dispute selection and immutable discussion proposals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from scrutare.engine.assessment import Assessment
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.evidence import validate_evidence
from scrutare.findings.models import Citation, Finding, FindingError, _text, parse_evidence


def _claim_key(finding: Finding) -> tuple[object, ...]:
    evidence = finding.evidence
    if evidence is None:
        raise FindingError("discussion: expected structured evidence")

    def normalized(text: str) -> str:
        return " ".join(text.split())

    return (
        finding.anchor,
        normalized(finding.problem),
        normalized(finding.reason),
        normalized(evidence.trigger),
        tuple(normalized(p) for p in evidence.preconditions),
        normalized(evidence.expected),
        normalized(evidence.observed),
        normalized(evidence.impact),
    )


def equivalent_groups(findings: tuple[Finding, ...]) -> tuple[MergedFinding, ...]:
    """Conservatively match normalized assertions, never merge different triggers/causes."""
    groups: dict[tuple[object, ...], list[Finding]] = {}
    for finding in findings:
        groups.setdefault(_claim_key(finding), []).append(finding)
    return tuple(
        MergedFinding(group[0].anchor, group[0].problem, tuple(group)) for group in groups.values()
    )


def disputed_ids(rows: tuple[Assessment, ...]) -> tuple[str, ...]:
    statuses: dict[tuple[object, ...], set[str]] = {}
    for row in rows:
        statuses.setdefault(_claim_key(row.candidate), set()).add(row.status)
    return tuple(
        cast(str, row.candidate.candidate_id)
        for row in rows
        if row.status == "unresolved"
        or row.status == "supported"
        and bool(row.counter_citations)
        or len(statuses[_claim_key(row.candidate)]) > 1
    )


@dataclass(frozen=True)
class Proposal:
    candidate_id: str
    action: Literal["retain", "refute", "unresolved"]
    reason: str
    citations: tuple[Citation, ...]
    transition_supported: bool
    refusal: str | None


def proposal_wire(proposal: Proposal) -> dict[str, Any]:
    from scrutare.findings.models import artifact_data

    return cast(dict[str, Any], artifact_data(proposal))


def parse_proposals(
    value: object, candidates: tuple[Finding, ...], root: Path, *, artifact: bool = False
) -> tuple[Proposal, ...]:
    if not isinstance(value, dict) or set(value) != {"proposals"}:
        raise FindingError("discussion: expected closed proposal envelope")
    if not isinstance(value["proposals"], list):
        raise FindingError("discussion: expected proposal array")
    originals = {finding.candidate_id: finding for finding in candidates}
    if len(originals) != len(candidates) or None in originals:
        raise FindingError("discussion: expected distinct v2 candidate IDs")
    parsed = {}
    for row in value["proposals"]:
        fields = {"candidate_id", "action", "reason", "citations"}
        if artifact:
            fields |= {"transition_supported", "refusal"}
        if not isinstance(row, dict) or set(row) != fields:
            raise FindingError("discussion: unknown or missing proposal field")
        identifier = _text(row["candidate_id"], "candidate_id")
        if identifier not in originals or identifier in parsed:
            raise FindingError("discussion: unknown or duplicate candidate")
        action = row["action"]
        if action not in ("retain", "refute", "unresolved"):
            raise FindingError("discussion: unauthorized transition")
        reason = _text(row["reason"], "discussion.reason")
        if not isinstance(row["citations"], list):
            raise FindingError("discussion: expected citation array")
        citations: tuple[Citation, ...] = ()
        if row["citations"]:
            evidence = parse_evidence(
                {
                    "version": 2,
                    "trigger": "discussion",
                    "preconditions": [],
                    "expected": "discussion",
                    "observed": "discussion",
                    "impact": reason,
                    "citations": row["citations"],
                },
                artifact=artifact,
            )
            checked = validate_evidence(replace(originals[identifier], evidence=evidence), root)
            assert checked.evidence is not None
            citations = checked.evidence.citations
            if artifact and citations != evidence.citations:
                raise FindingError("discussion: citation validation labels disagree")
        supported = action == "retain" or bool(citations)
        refusal = None if supported else "missing_counterevidence"
        if artifact and (
            type(row["transition_supported"]) is not bool
            or row["transition_supported"] != supported
            or row["refusal"] != refusal
        ):
            raise FindingError("discussion: transition validation labels disagree")
        parsed[identifier] = Proposal(identifier, action, reason, citations, supported, refusal)
    if set(parsed) != set(originals):
        raise FindingError("discussion: incomplete candidate coverage")
    return tuple(parsed[cast(str, finding.candidate_id)] for finding in candidates)
