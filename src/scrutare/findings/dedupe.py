"""Deterministic anchored dedupe that retains every source observation."""

from collections.abc import Iterable
from dataclasses import dataclass

from scrutare.config import CATEGORIES, Category
from scrutare.findings.models import Anchor, Finding, FindingError, _text


def _normalized_problem(problem: str) -> str:
    return " ".join(problem.split())


@dataclass(frozen=True)
class MergedFinding:
    """One anchored problem with immutable, ordered source evidence."""

    anchor: Anchor
    problem: str
    sources: tuple[Finding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, Anchor):
            raise FindingError("anchor: expected an Anchor")
        _text(self.problem, "problem")
        if not isinstance(self.sources, tuple) or not self.sources or any(
            not isinstance(source, Finding) for source in self.sources
        ):
            raise FindingError("sources: expected a nonempty tuple of Finding values")
        normalized = _normalized_problem(self.problem)
        if any(
            source.anchor != self.anchor or _normalized_problem(source.problem) != normalized
            for source in self.sources
        ):
            raise FindingError("sources: anchors and normalized problems must match the group")

    @property
    def categories(self) -> tuple[Category, ...]:
        """All source categories, ordered by the configuration authority."""
        present = {source.category for source in self.sources}
        return tuple(category for category in CATEGORIES if category in present)

    @property
    def personas(self) -> tuple[str, ...]:
        """Unique caller-owned attribution in first-seen order."""
        return tuple(dict.fromkeys(source.persona for source in self.sources))

    @property
    def reasons(self) -> tuple[str, ...]:
        """Unique unmodified reasons in first-seen order."""
        return tuple(dict.fromkeys(source.reason for source in self.sources))

    def to_dict(self) -> dict[str, object]:
        """Return fresh JSON-compatible artifact data, including every source."""
        return {
            "file": self.anchor.file,
            "line": self.anchor.line,
            "side": self.anchor.side,
            "problem": self.problem,
            "categories": list(self.categories),
            "personas": list(self.personas),
            "reasons": list(self.reasons),
            "sources": [
                {
                    "file": source.anchor.file,
                    "line": source.anchor.line,
                    "side": source.anchor.side,
                    "category": source.category,
                    "problem": source.problem,
                    "reason": source.reason,
                    "persona": source.persona,
                }
                for source in self.sources
            ],
        }


def dedupe_findings(findings: Iterable[Finding]) -> tuple[MergedFinding, ...]:
    """Group exact anchors and whitespace-normalized problems in caller order.

    Case, punctuation, and wording stay significant. Every input occurrence
    survives as source evidence, including equal duplicates and category conflicts.
    """
    groups: dict[tuple[Anchor, str], list[Finding]] = {}
    for finding in findings:
        if not isinstance(finding, Finding):
            raise FindingError("findings: expected Finding values")
        key = (finding.anchor, _normalized_problem(finding.problem))
        groups.setdefault(key, []).append(finding)
    return tuple(
        MergedFinding(sources[0].anchor, sources[0].problem, tuple(sources))
        for sources in groups.values()
    )
