"""Pure verdict derivation and canonical serialization of surviving evidence."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from scrutare.config import Category, Strategy, VerdictSettings
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.models import FindingError


@dataclass(frozen=True)
class Exhaustion:
    """Captured strategy evidence of reaching the bound without convergence."""

    strategy: Strategy
    rounds_completed: int
    round_limit: int
    converged: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, str) or self.strategy not in (
            "panel", "iterative", "debate"
        ):
            raise FindingError("exhaustion.strategy: expected panel, iterative or debate")
        if type(self.round_limit) is not int or self.round_limit <= 0:
            raise FindingError("exhaustion.round_limit: expected a positive integer")
        if type(self.rounds_completed) is not int or self.rounds_completed != self.round_limit:
            raise FindingError("exhaustion.rounds_completed: expected the exact round limit")
        if self.converged is not False:
            raise FindingError("exhaustion.converged: expected False")


_FALLBACK_STAGES = {"assessment": "anchor_validated", "discussion": "pre_round_assessment"}


@dataclass(frozen=True)
class Degradation:
    """A measured phase overrun answered by the last complete stage, never by approval."""

    phase: Literal["assessment", "discussion"]
    round: int | None
    allowance_tokens: int | None
    grant_tokens: int
    usage_tokens: int
    overrun_tokens: int
    fallback_stage: Literal["anchor_validated", "pre_round_assessment"]

    def __post_init__(self) -> None:
        if not isinstance(self.phase, str) or self.phase not in _FALLBACK_STAGES:
            raise FindingError("degraded.phase: expected assessment or discussion")
        if self.fallback_stage != _FALLBACK_STAGES[self.phase]:
            raise FindingError("degraded.fallback_stage: expected the phase's last complete stage")
        if self.phase == "assessment":
            if self.round is not None:
                raise FindingError("degraded.round: assessment has no discussion round")
        elif type(self.round) is not int or self.round <= 0:
            raise FindingError("degraded.round: expected a positive discussion round")
        if self.allowance_tokens is not None and (
                type(self.allowance_tokens) is not int or self.allowance_tokens <= 0):
            raise FindingError("degraded.allowance_tokens: expected a positive integer or null")
        for name in ("grant_tokens", "usage_tokens", "overrun_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise FindingError(f"degraded.{name}: expected a nonnegative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"phase": self.phase, "round": self.round,
                "allowance_tokens": self.allowance_tokens, "grant_tokens": self.grant_tokens,
                "usage_tokens": self.usage_tokens, "overrun_tokens": self.overrun_tokens,
                "fallback_stage": self.fallback_stage}


@dataclass(frozen=True)
class Verdict:
    """Immutable evidence and policy, with status determined only by their rule."""

    findings: tuple[MergedFinding, ...]
    config: VerdictSettings
    exhaustion: Exhaustion | None = None
    evidence_version: int | None = None
    unresolved_candidates: tuple[str, ...] = ()
    degradation: Degradation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.findings, tuple) or any(
            not isinstance(finding, MergedFinding) for finding in self.findings
        ):
            raise FindingError("findings: expected a tuple of MergedFinding values")
        if len({source.evidence is None for group in self.findings
                for source in group.sources}) > 1:
            raise FindingError("findings: mixed evidence versions")
        if any(citation.validation != "valid" for group in self.findings
               for source in group.sources if source.evidence is not None
               for citation in source.evidence.citations):
            raise FindingError("findings: unvalidated citations cannot feed a verdict")
        versions = {1 if source.evidence is None else 2
                    for group in self.findings for source in group.sources}
        version = self.evidence_version
        if version is None:
            version = next(iter(versions), 1)
            object.__setattr__(self, "evidence_version", version)
        if type(version) is not int or version not in (1, 2) or versions - {version}:
            raise FindingError("findings: evidence contract disagrees with verdict schema")
        if (not isinstance(self.unresolved_candidates, tuple)
                or any(not isinstance(identifier, str) or not identifier.strip()
                       for identifier in self.unresolved_candidates)
                or len(set(self.unresolved_candidates)) != len(self.unresolved_candidates)
                or (self.unresolved_candidates and version != 2)):
            raise FindingError("assessment: expected distinct v2 unresolved identifiers")
        if not isinstance(self.config, VerdictSettings):
            raise FindingError("config: expected VerdictSettings")
        if self.exhaustion is not None and not isinstance(self.exhaustion, Exhaustion):
            raise FindingError("exhaustion: expected Exhaustion or None")
        if self.degradation is not None and (
                not isinstance(self.degradation, Degradation) or version != 2):
            raise FindingError("degraded: expected a v2 Degradation or None")

    def _blocking_categories(self, finding: MergedFinding) -> tuple[Category, ...]:
        return tuple(
            category
            for category in finding.categories
            if category in self.config.blocking_categories
        )

    @property
    def verdict(self) -> Literal["approve", "changes_requested", "escalated"]:
        """Escalate exhaustion, otherwise apply the configured blocking categories.

        A degraded fallback is never an approval: without a blocking finding it escalates.
        """
        if self.exhaustion is not None or self.unresolved_candidates:
            return "escalated"
        if any(self._blocking_categories(finding) for finding in self.findings):
            return "changes_requested"
        if self.degradation is not None:
            return "escalated"
        return "approve"

    @property
    def rule(self) -> Literal[
        "any_blocking_finding", "no_blocking_findings", "rounds_exhausted_without_convergence",
        "unresolved_semantic_assessment", "phase_overrun_fallback"
    ]:
        """Name the evidence rule that produced this verdict."""
        if self.exhaustion is not None:
            return "rounds_exhausted_without_convergence"
        if self.unresolved_candidates:
            return "unresolved_semantic_assessment"
        if self.verdict == "changes_requested":
            return "any_blocking_finding"
        if self.degradation is not None:
            return "phase_overrun_fallback"
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
        data: dict[str, Any] = {
            "schema_version": self.evidence_version,
            "verdict": self.verdict,
            "rule": self.rule,
            "config": {
                "blocking_categories": list(self.config.blocking_categories),
                "advisory_categories": list(self.config.advisory_categories),
            },
            "findings": findings,
        }
        if self.exhaustion is not None:
            data["exhaustion"] = {
                "strategy": self.exhaustion.strategy,
                "rounds_completed": self.exhaustion.rounds_completed,
                "round_limit": self.exhaustion.round_limit,
                "converged": self.exhaustion.converged,
            }
        if self.unresolved_candidates:
            data["assessment_uncertainty"] = {
                "candidate_ids": list(self.unresolved_candidates),
                "attempts_completed": 1, "attempt_limit": 1,
            }
        if self.degradation is not None:
            data["degraded"] = self.degradation.to_dict()
        return data

    def to_bytes(self) -> bytes:
        """Encode deterministic sorted JSON as UTF-8 with a final newline."""
        data = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        return (data + "\n").encode("utf-8")


def derive_verdict(
    findings: Iterable[MergedFinding], config: VerdictSettings, *,
    exhaustion: Exhaustion | None = None, evidence_version: int | None = None,
    unresolved_candidates: tuple[str, ...] = (), degradation: Degradation | None = None,
) -> Verdict:
    """Snapshot already verified, deduplicated findings and apply the category policy."""
    if not isinstance(findings, Iterable) or isinstance(findings, (str, bytes, Mapping)):
        raise FindingError("findings: expected an iterable of MergedFinding values")
    return Verdict(tuple(findings), config, exhaustion, evidence_version, unresolved_candidates,
                   degradation)
