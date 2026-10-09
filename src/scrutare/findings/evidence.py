"""Mechanically bind references to immutable captured bytes, without semantic adjudication."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

from scrutare.findings.models import Finding, FindingError


class CitationValidationError(FindingError):
    """A fixed mechanical refusal that may be persisted without model text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"citation: {reason}")


def evidence_schema() -> dict[str, Any]:
    citation = {
        "side": {"type": "string", "enum": ["base", "head"]},
        "revision": {"type": "string"},
        "path": {"type": "string"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "sha256": {"type": "string"},
    }
    properties: dict[str, Any] = {
        name: {"type": "string"} for name in ("trigger", "expected", "observed", "impact")
    }
    properties.update(
        version={"type": "integer", "enum": [2]},
        preconditions={"type": "array", "items": {"type": "string"}},
        citations={
            "type": "array",
            "items": {
                "type": "object",
                "properties": citation,
                "required": list(citation),
                "additionalProperties": False,
            },
        },
    )
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def evidence_version(root: Path) -> int:
    """Select the contract from caller-captured configuration, never inferred output."""
    from scrutare.config import load_config

    return 2 if load_config(root.parent / "config.yaml").findings.evidence == "v2" else 1


def validate_evidence(finding: Finding, root: Path) -> Finding:
    """Refuse unsupported references; successful identity checks do not prove the claim."""
    from scrutare.engine.repository_context import MANIFEST, regular
    from scrutare.engine.review_inputs import _read_json

    if finding.evidence is None:
        return finding
    manifest = _read_json(root / MANIFEST)
    citations = []
    for citation in finding.evidence.citations:
        entry = next(
            (
                entry
                for entry in manifest["entries"]
                if entry["side"] == citation.side and entry["path"] == citation.path
            ),
            None,
        )
        if manifest["revisions"].get(citation.side, {}).get("sha") != citation.revision:
            raise CitationValidationError("revision_mismatch")
        if entry is None or entry["status"] != "captured":
            raise CitationValidationError("path_not_captured")
        artifact = entry["artifact"]
        if not isinstance(artifact, str) or Path(artifact).name != artifact:
            raise CitationValidationError("content_hash_mismatch")
        content = regular(root / artifact)
        if (
            entry["revision"] != citation.revision
            or entry["sha256"] != citation.sha256
            or sha256(content).hexdigest() != citation.sha256
        ):
            raise CitationValidationError("content_hash_mismatch")
        content.decode("utf-8")
        line_count = content.count(b"\n") + int(bool(content) and not content.endswith(b"\n"))
        if citation.end_line > line_count:
            raise CitationValidationError("line_range_outside_capture")
        citations.append(replace(citation, validation="valid", validation_reason=None))
    return replace(finding, evidence=replace(finding.evidence, citations=tuple(citations)))
