"""Immutable data exchanged by the offline replay readers and audit."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

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
