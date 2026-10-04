"""Typed anchor-only corrections bound to exact caller-owned findings."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scrutare.findings.models import Anchor, Finding, FindingError
from scrutare.findings.verification import ReanchorCorrection


@dataclass(frozen=True)
class ReanchorRequest:
    """A stable local identifier and the immutable original it may correct."""

    request_id: str
    original: Finding

    def __post_init__(self) -> None:
        if (not isinstance(self.request_id, str)
                or not re.fullmatch(r"r[0-9]{4,}", self.request_id)
                or int(self.request_id[1:]) <= 0
                or self.request_id != f"r{int(self.request_id[1:]):04d}"):
            raise FindingError("request_id: expected a stable correction identifier")
        if not isinstance(self.original, Finding):
            raise FindingError("original: expected a Finding")


def _validate_requests(requests: tuple[ReanchorRequest, ...]) -> None:
    if not isinstance(requests, tuple) or any(
        not isinstance(request, ReanchorRequest) for request in requests
    ):
        raise FindingError("requests: expected a tuple of ReanchorRequest values")
    if (len({request.request_id for request in requests}) != len(requests)
            or len({request.original for request in requests}) != len(requests)):
        raise FindingError("requests: expected distinct identifiers and originals")


def make_reanchor_requests(originals: tuple[Finding, ...]) -> tuple[ReanchorRequest, ...]:
    """Deduplicate equal originals in caller order and assign deterministic local IDs."""
    if not isinstance(originals, tuple) or any(
        not isinstance(original, Finding) for original in originals
    ):
        raise FindingError("originals: expected a tuple of Finding values")
    return tuple(ReanchorRequest(f"r{index:04d}", original)
                 for index, original in enumerate(dict.fromkeys(originals), start=1))


def reanchor_schema() -> dict[str, object]:
    """Return the closed anchor-only schema proved through the actual nare CLI."""
    return {
        "type": "object",
        "properties": {
            "corrections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
                    },
                    "required": ["request_id", "file", "line", "side"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["corrections"],
        "additionalProperties": False,
    }


def parse_reanchor_output(
    value: object, requests: tuple[ReanchorRequest, ...],
) -> tuple[ReanchorCorrection, ...]:
    """Bind supplied anchors to known originals, leaving diff existence to verification."""
    _validate_requests(requests)
    if (not isinstance(value, dict) or set(value) != {"corrections"}
            or not isinstance(value["corrections"], list)):
        raise FindingError("corrections: expected an anchor-only corrections document")
    originals = {request.request_id: request.original for request in requests}
    seen: set[str] = set()
    corrections: list[ReanchorCorrection] = []
    for item in value["corrections"]:
        if not isinstance(item, dict) or set(item) != {"request_id", "file", "line", "side"}:
            raise FindingError("correction: expected request_id, file, line and side only")
        request_id = item["request_id"]
        if not isinstance(request_id, str) or request_id not in originals or request_id in seen:
            raise FindingError("request_id: expected a known identifier at most once")
        seen.add(request_id)
        corrections.append(ReanchorCorrection(
            originals[request_id], Anchor(item["file"], item["line"], item["side"]),
        ))
    return tuple(corrections)
