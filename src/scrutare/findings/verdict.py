"""Pure verdict derivation and canonical serialization of surviving evidence."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from scrutare.config import Category, VerdictSettings
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.models import FindingError


@dataclass(frozen=True)
class Verdict:
    """Immutable evidence and policy, with status determined only by their rule."""

    findings: tuple[MergedFinding, ...]
    config: VerdictSettings

    def __post_init__(self) -> None:
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, MergedFinding) for finding in self.findings
        ):
            raise FindingError("findings: expected a tuple of MergedFinding values")
        if not isinstance(self.config, VerdictSettings):
            raise FindingError("config: expected VerdictSettings")

    def _blocking_categories(self, finding: MergedFinding) -> tuple[Category, ...]:
        return tuple(
            category
            for category in finding.categories
            if category in self.config.blocking_categories
        )

    @property
    def verdict(self) -> Literal["approve", "changes_requested"]:
        """Request changes if any source category is configured as blocking."""
        if any(self._blocking_categories(finding) for finding in self.findings):
            return "changes_requested"
        return "approve"

    @property
    def rule(self) -> Literal["any_blocking_finding", "no_blocking_findings"]:
        """Name the evidence rule that produced this verdict."""
        if self.verdict == "changes_requested":
            return "any_blocking_finding"
        return "no_blocking_findings"

    def to_dict(self) -> dict[str, Any]:
        """Return fresh artifact data retaining every group and source in order."""
        findings = []
        for finding in self.findings:
            blocking = self._blocking_categories(finding)
            findings.append(
                finding.to_dict() | {
                    "blocking": bool(blocking),
                    "blocking_categories": list(blocking),
                }
            )
        return {
            "schema_version": 1,
            "verdict": self.verdict,
            "rule": self.rule,
            "config": {
                "blocking_categories": list(self.config.blocking_categories),
                "advisory_categories": list(self.config.advisory_categories),
            },
            "findings": findings,
        }

    def to_bytes(self) -> bytes:
        """Encode deterministic sorted JSON as UTF-8 with a final newline."""
        data = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        return (data + "\n").encode("utf-8")


def derive_verdict(findings: Iterable[MergedFinding], config: VerdictSettings) -> Verdict:
    """Snapshot already verified, deduplicated findings and apply the category policy."""
    if not isinstance(findings, Iterable) or isinstance(findings, (str, bytes, Mapping)):
        raise FindingError("findings: expected an iterable of MergedFinding values")
    return Verdict(tuple(findings), config)
