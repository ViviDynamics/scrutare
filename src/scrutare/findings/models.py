"""Validated immutable findings, with attribution owned by the caller."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Literal, cast

from scrutare.config import CATEGORIES, Category

Side = Literal["LEFT", "RIGHT"]


class FindingError(ValueError):
    """Invalid finding or diff, with diagnostics that omit untrusted values."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FindingError(f"{field}: expected a nonempty string")
    return value


def _path(value: object, field: str = "file") -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\x00" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise FindingError(f"{field}: expected a repository-relative path without traversal")
    return value


def _line(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise FindingError("line: expected a positive integer")
    return value


def _side(value: object) -> Side:
    if not isinstance(value, str) or value not in ("LEFT", "RIGHT"):
        raise FindingError("side: expected LEFT or RIGHT")
    return cast(Side, value)


def _category(value: object) -> Category:
    if not isinstance(value, str) or value not in CATEGORIES:
        raise FindingError(f"category: expected one of {', '.join(CATEGORIES)}")
    return value


@dataclass(frozen=True)
class Anchor:
    """A repository path and positive line on one side of a patch."""

    file: str
    line: int
    side: Side = "RIGHT"

    def __post_init__(self) -> None:
        _path(self.file)
        _line(self.line)
        _side(self.side)


@dataclass(frozen=True)
class Citation:
    """A quoted reference; validation measures capture identity, never claim truth."""

    side: str
    revision: str
    path: str
    start_line: int
    end_line: int
    sha256: str
    validation: str = "unvalidated"
    validation_reason: str | None = None

    def __post_init__(self) -> None:
        if self.side not in ("base", "head"):
            raise FindingError("citation.side: expected base or head")
        for value, size in ((self.revision, 40), (self.sha256, 64)):
            if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{size}}}", value) is None:
                raise FindingError("citation: expected complete revision and content hash")
        _path(self.path)
        if "\\" in self.path:
            raise FindingError("citation.path: ambiguous separators")
        _line(self.start_line)
        _line(self.end_line)
        if self.end_line < self.start_line:
            raise FindingError("citation: reversed line range")
        reasons = (
            "revision_mismatch",
            "path_not_captured",
            "content_hash_mismatch",
            "line_range_outside_capture",
        )
        if (
            self.validation not in ("unvalidated", "valid", "unsupported")
            or self.validation == "unsupported"
            and self.validation_reason not in reasons
            or self.validation != "unsupported"
            and self.validation_reason is not None
        ):
            raise FindingError("citation: invalid mechanical validation status")


@dataclass(frozen=True)
class EvidenceV2:
    """Model assertions kept distinct from mechanically validated citations."""

    trigger: str
    preconditions: tuple[str, ...]
    expected: str
    observed: str
    impact: str
    citations: tuple[Citation, ...]
    version: int = 2

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 2:
            raise FindingError("evidence.version: expected 2")
        for name in ("trigger", "expected", "observed", "impact"):
            _text(getattr(self, name), name)
        if not isinstance(self.preconditions, tuple):
            raise FindingError("preconditions: expected tuple")
        for value in self.preconditions:
            _text(value, "preconditions")
        if (
            not isinstance(self.citations, tuple)
            or not self.citations
            or any(not isinstance(c, Citation) for c in self.citations)
        ):
            raise FindingError("citations: expected nonempty tuple of Citation")


def parse_evidence(value: object, *, artifact: bool = False) -> EvidenceV2:
    if not isinstance(value, Mapping) or set(value) != {
        "version",
        "trigger",
        "preconditions",
        "expected",
        "observed",
        "impact",
        "citations",
    }:
        raise FindingError("evidence: expected closed version 2 record")
    if not isinstance(value["preconditions"], (list, tuple)) or not isinstance(
        value["citations"], (list, tuple)
    ):
        raise FindingError("evidence: expected arrays")
    citations = []
    citation_fields = {"side", "revision", "path", "start_line", "end_line", "sha256"}
    for item in value["citations"]:
        expected_fields = citation_fields | (
            {"validation", "validation_reason"} if artifact else set()
        )
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise FindingError("citation: unknown or missing field")
        citations.append(Citation(**item))
    return EvidenceV2(
        value["trigger"],
        tuple(value["preconditions"]),
        value["expected"],
        value["observed"],
        value["impact"],
        tuple(citations),
        value["version"],
    )


def artifact_data(value: Any) -> Any:
    """Serialize dataclasses, preserving the historical omitted legacy fields."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: artifact_data(getattr(value, field.name))
            for field in fields(value)
            if not (
                isinstance(value, Finding)
                and field.name in ("evidence", "candidate_id")
                and getattr(value, field.name) is None
            )
        }
    if isinstance(value, dict):
        return {key: artifact_data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [artifact_data(item) for item in value]
    return value


@dataclass(frozen=True)
class Finding:
    """A review observation without model-owned verdict or severity fields."""

    anchor: Anchor
    category: Category
    problem: str
    reason: str
    persona: str
    evidence: EvidenceV2 | None = None
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, Anchor):
            raise FindingError("anchor: expected an Anchor")
        _category(self.category)
        _text(self.problem, "problem")
        _text(self.reason, "reason")
        _text(self.persona, "persona")
        if (self.evidence is None) != (self.candidate_id is None):
            raise FindingError("finding: evidence and caller identifier must share version")
        if self.evidence is not None:
            if not isinstance(self.evidence, EvidenceV2):
                raise FindingError("evidence: expected EvidenceV2")
            _text(self.candidate_id, "candidate_id")


def parse_finding(
    data: Mapping[str, object],
    *,
    persona: str,
    evidence_version: int = 1,
    candidate_id: str | None = None,
    artifact: bool = False,
) -> Finding:
    """Parse only approved wire fields, attributing them to the supplied persona."""
    if not isinstance(data, Mapping):
        raise FindingError("finding: expected a mapping")
    fields: tuple[str, ...] = ("file", "line", "side", "category", "problem", "reason")
    if evidence_version not in (1, 2):
        raise FindingError("finding: unsupported evidence version")
    if evidence_version == 2:
        fields += ("evidence",)
        if artifact:
            fields += ("candidate_id",)
            candidate_id = _text(data.get("candidate_id"), "candidate_id")
    if any(not isinstance(key, str) or key not in fields for key in data):
        raise FindingError(f"finding: unknown field; allowed: {', '.join(fields)}")
    return Finding(
        Anchor(_path(data.get("file")), _line(data.get("line")), _side(data.get("side", "RIGHT"))),
        _category(data.get("category")),
        _text(data.get("problem"), "problem"),
        _text(data.get("reason"), "reason"),
        _text(persona, "persona"),
        parse_evidence(data.get("evidence"), artifact=artifact) if evidence_version == 2 else None,
        candidate_id if evidence_version == 2 else None,
    )


def finding_wire(finding: Finding) -> dict[str, Any]:
    """Caller artifact representation with diff anchor flattened as in legacy records."""
    data = cast(dict[str, Any], artifact_data(finding))
    anchor = cast(dict[str, Any], data.pop("anchor"))
    return anchor | data


def finding_from_artifact(data: Mapping[str, Any], *, evidence_version: int = 1) -> Finding:
    if set(data) != (
        {"anchor", "category", "problem", "reason", "persona"}
        | ({"evidence", "candidate_id"} if evidence_version == 2 else set())
    ):
        raise FindingError("finding: artifact version disagrees with fields")
    anchor = data["anchor"]
    if not isinstance(anchor, dict) or set(anchor) != {"file", "line", "side"}:
        raise FindingError("anchor: invalid artifact")
    return parse_finding(
        anchor | {key: value for key, value in data.items() if key not in ("anchor", "persona")},
        persona=data["persona"],
        evidence_version=evidence_version,
        artifact=True,
    )
