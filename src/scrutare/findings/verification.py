"""Check a captured diff and finalize one caller-supplied re-anchor round."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from scrutare.findings.models import Anchor, Finding, FindingError

DropReason = Literal["missing_correction", "invalid_correction"]


def _validate_anchors(anchors: object) -> None:
    if not isinstance(anchors, frozenset) or any(
        not isinstance(anchor, Anchor) for anchor in anchors
    ):
        raise FindingError("anchors: expected a frozenset of Anchor values")


def _validate_findings(findings: object, field: str) -> None:
    if not isinstance(findings, tuple) or any(
        not isinstance(finding, Finding) for finding in findings
    ):
        raise FindingError(f"{field}: expected a tuple of Finding values")


def _partition(
    findings: tuple[Finding, ...], anchors: frozenset[Anchor]
) -> tuple[tuple[Finding, ...], tuple[Finding, ...]]:
    accepted: list[Finding] = []
    requests: dict[Finding, None] = {}
    for finding in findings:
        if finding.anchor in anchors:
            accepted.append(finding)
        else:
            requests.setdefault(finding, None)
    return tuple(accepted), tuple(requests)


@dataclass(frozen=True)
class AnchorCheck:
    """An immutable diff index, initial decisions, and original caller order."""

    anchors: frozenset[Anchor]
    accepted: tuple[Finding, ...]
    reanchor_requests: tuple[Finding, ...]
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        _validate_anchors(self.anchors)
        _validate_findings(self.accepted, "accepted")
        _validate_findings(self.reanchor_requests, "reanchor_requests")
        _validate_findings(self.findings, "findings")
        if (self.accepted, self.reanchor_requests) != _partition(self.findings, self.anchors):
            raise FindingError("check: decisions must match the captured findings and anchors")


@dataclass(frozen=True)
class ReanchorCorrection:
    """A requested original finding with only a replacement anchor supplied."""

    original: Finding
    anchor: Anchor

    def __post_init__(self) -> None:
        if not isinstance(self.original, Finding):
            raise FindingError("original: expected a Finding")
        if not isinstance(self.anchor, Anchor):
            raise FindingError("anchor: expected an Anchor")


@dataclass(frozen=True)
class DroppedFinding:
    """An unresolved original and a fixed reason suitable for audit artifacts."""

    original: Finding
    reason: DropReason

    def __post_init__(self) -> None:
        if not isinstance(self.original, Finding):
            raise FindingError("original: expected a Finding")
        if not isinstance(self.reason, str) or self.reason not in (
            "missing_correction", "invalid_correction"
        ):
            raise FindingError("reason: expected missing_correction or invalid_correction")


@dataclass(frozen=True)
class VerificationResult:
    """Terminal survivors and dropped originals, each in caller order."""

    accepted: tuple[Finding, ...]
    dropped: tuple[DroppedFinding, ...]

    def __post_init__(self) -> None:
        _validate_findings(self.accepted, "accepted")
        if not isinstance(self.dropped, tuple) or any(
            not isinstance(drop, DroppedFinding) for drop in self.dropped
        ):
            raise FindingError("dropped: expected a tuple of DroppedFinding values")


def check_anchors(findings: Iterable[Finding], anchors: frozenset[Anchor]) -> AnchorCheck:
    """Capture findings once and request one correction per equal invalid original."""
    _validate_anchors(anchors)
    captured = tuple(findings)
    _validate_findings(captured, "findings")
    accepted, requests = _partition(captured, anchors)
    return AnchorCheck(anchors, accepted, requests, captured)


def finish_reanchor(
    check: AnchorCheck, corrections: Iterable[ReanchorCorrection] = ()
) -> VerificationResult:
    """Finalize the supplied round against the captured index, without further requests.

    Corrections cannot change evidence or name an accepted/unrequested finding.
    Equal duplicate originals share one correction; all input occurrences survive
    or drop in their original order for the later dedupe stage.
    """
    if not isinstance(check, AnchorCheck):
        raise FindingError("check: expected an AnchorCheck")
    requested = frozenset(check.reanchor_requests)
    supplied: dict[Finding, Anchor] = {}
    for correction in corrections:
        if not isinstance(correction, ReanchorCorrection):
            raise FindingError("corrections: expected ReanchorCorrection values")
        if correction.original not in requested:
            raise FindingError("original: correction must name a requested finding")
        if correction.original in supplied:
            raise FindingError("corrections: expected at most one correction per original")
        supplied[correction.original] = correction.anchor

    accepted: list[Finding] = []
    dropped: list[DroppedFinding] = []
    for finding in check.findings:
        if finding.anchor in check.anchors:
            accepted.append(finding)
            continue
        anchor = supplied.get(finding)
        if anchor is None:
            dropped.append(DroppedFinding(finding, "missing_correction"))
        elif anchor not in check.anchors:
            dropped.append(DroppedFinding(finding, "invalid_correction"))
        else:
            accepted.append(replace(finding, anchor=anchor))
    return VerificationResult(tuple(accepted), tuple(dropped))
