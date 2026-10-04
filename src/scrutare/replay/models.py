"""Immutable data exchanged by the offline replay readers and audit."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Literal

from scrutare.config import Strategy, VerdictSettings
from scrutare.findings.verdict import Exhaustion, Verdict


class ReplayError(ValueError):
    """Invalid captured evidence, with diagnostics that omit untrusted values."""


@dataclass(frozen=True)
class AuditIssue:
    """One known artifact field that cannot support a clean comparison."""

    code: str
    path: str
    severity: Literal["difference", "incomplete", "invalid"]


@dataclass(frozen=True)
class ReplayDifference:
    """A semantic or encoding difference between recorded and derived data."""

    path: str
    kind: Literal["added", "removed", "changed", "reordered", "encoding"]
    before: object
    after: object
    finding: str | None = None


@dataclass(frozen=True)
class CapturedPolicy:
    """The verdict partition and optional bound from captured configuration."""

    settings: VerdictSettings
    strategy: Strategy | None
    round_limit: int | None
    document: Mapping[str, object]


@dataclass(frozen=True)
class SavedVerdict:
    """Validated baseline shape, original bytes and explicit bound assertion."""

    raw: bytes
    document: Mapping[str, object]
    exhaustion: Exhaustion | None


@dataclass(frozen=True)
class PostingAudit:
    """Recorded posting identity and reviewer request state."""

    status: str
    verdict_sha256: str | None
    reviewer_request_status: str
    issues: tuple[AuditIssue, ...]


@dataclass(frozen=True)
class ReplayResult:
    """Candidate verdict with separate saved and posted identity comparisons."""

    verdict: Verdict | None
    saved_identical: bool | None
    posted_identical: bool | None
    saved_sha256: str | None
    posted_sha256: str | None
    posting: PostingAudit
    differences: tuple[ReplayDifference, ...]
    issues: tuple[AuditIssue, ...]

    def _issues(self) -> tuple[AuditIssue, ...]:
        return tuple(dict.fromkeys(self.issues + self.posting.issues))

    @property
    def exit_code(self) -> int:
        """Prefer incomplete evidence over known differences and proven identity."""
        if (self.verdict is None or self.saved_identical is None or self.saved_sha256 is None
                or (self.posted_identical is not None and self.posted_sha256 is None)
                or any(issue.severity in ("invalid", "incomplete") for issue in self._issues())):
            return 2
        if (self.saved_identical is False or self.posted_identical is False
                or self.differences or self._issues()):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        """Return fresh JSON data with saved and original posted targets separate."""
        basis = "none"
        if self.verdict is not None and self.verdict.exhaustion is not None:
            basis = "unverified_recorded_assertion"
            if (self.posting.status == "posted" and self.saved_sha256 is not None
                    and self.posted_sha256 == self.saved_sha256
                    and self.posting.verdict_sha256 == self.saved_sha256
                    and not any(issue.severity in ("invalid", "incomplete")
                                for issue in self.posting.issues)):
                basis = "recorded_assertion"
        return {
            "schema_version": 1,
            "status": {0: "identical", 1: "different", 2: "incomplete"}[self.exit_code],
            "verdict": self.verdict.verdict if self.verdict is not None else None,
            "rule": self.verdict.rule if self.verdict is not None else None,
            "recomputed_sha256": (
                sha256(self.verdict.to_bytes()).hexdigest() if self.verdict is not None else None
            ),
            "saved_verdict": {"byte_identical": self.saved_identical,
                              "sha256": self.saved_sha256},
            "posted_verdict": {"byte_identical": self.posted_identical,
                               "sha256": self.posted_sha256, "status": self.posting.status},
            "exhaustion_basis": basis,
            "reviewer_request_status": self.posting.reviewer_request_status,
            "differences": [asdict(difference) for difference in self.differences],
            "issues": [asdict(issue) for issue in self._issues()],
        }
