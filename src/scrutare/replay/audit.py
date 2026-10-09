"""Read-only verdict reconstruction from the run's captured deciding evidence."""

import json
from hashlib import sha256
from pathlib import Path

from scrutare.config import CATEGORIES, ConfigError, _load_yaml, parse_config
from scrutare.findings.verdict import derive_verdict
from scrutare.replay.artifacts import (
    decode_artifact,
    parse_findings,
    parse_policy,
    parse_saved_verdict,
    read_artifact,
    validate_exhaustion,
)
from scrutare.replay.differences import diff_verdicts
from scrutare.replay.models import (
    AuditIssue,
    CapturedPolicy,
    ReplayDifference,
    ReplayError,
    ReplayResult,
)
from scrutare.replay.posting import inspect_posting


def _yaml_issues(run_dir: Path, policy: CapturedPolicy) -> tuple[AuditIssue, ...]:
    """Check supplied deciding YAML fields, without resolving current defaults."""
    path = run_dir / "config.yaml"
    try:
        path.lstat()
    except FileNotFoundError:
        return ()
    except OSError:
        return (AuditIssue("config_snapshot_unreadable", "config.yaml", "incomplete"),)
    try:
        data = _load_yaml(read_artifact(path))
    except (ReplayError, ConfigError, UnicodeError, ValueError):
        return (AuditIssue("config_snapshot_unreadable", "config.yaml", "incomplete"),)
    try:
        if not isinstance(data, dict):
            raise ValueError
        issues = []

        def compare(field: str, old: object, new: object) -> None:
            if old != new:
                issues.append(AuditIssue("config_snapshots_disagree", f"config.yaml.{field}",
                                         "difference"))

        if "strategy" in data:
            strategy = data["strategy"]
            if not isinstance(strategy, str) or strategy not in ("panel", "iterative", "debate"):
                raise ValueError
            compare("strategy", strategy, policy.strategy)
        if "rounds" in data:
            rounds = data["rounds"]
            if not isinstance(rounds, dict):
                raise ValueError
            if "max" in rounds:
                bound = rounds["max"]
                if type(bound) is not int or bound <= 0:
                    raise ValueError
                compare("rounds.max", bound, policy.round_limit)
        if "verdict" in data:
            verdict = data["verdict"]
            if not isinstance(verdict, dict):
                raise ValueError
            for field in ("blocking_categories", "advisory_categories"):
                if field not in verdict:
                    continue
                categories = verdict[field]
                if (not isinstance(categories, list)
                        or any(not isinstance(item, str) or item not in CATEGORIES
                               for item in categories)
                        or len(set(categories)) != len(categories)):
                    raise ValueError
                compare(f"verdict.{field}", set(categories), set(getattr(policy.settings, field)))
        return tuple(issues)
    except (ValueError, TypeError, RecursionError):
        return (AuditIssue("config_snapshot_unsupported", "config.yaml", "incomplete"),)


def _context_issues(run_dir: Path, policy: CapturedPolicy) -> tuple[AuditIssue, ...]:
    """Audit optional contextual evidence without fetching, rewriting, or running models."""
    from scrutare.engine.review_inputs import PreparedReviewInputs

    context = policy.document.get("context")
    present = (run_dir / "review-inputs/repository-context.json").exists()
    if not present and (not isinstance(context, dict) or context.get("enabled") is not True):
        return ()
    try:
        config = parse_config(read_artifact(run_dir / "config.json"))
        if not config.context.enabled:
            raise ValueError
        metadata = decode_artifact(read_artifact(run_dir / "metadata.json"),
                                   artifact="metadata.json")
        files = decode_artifact(read_artifact(run_dir / "review-inputs/files.json"),
                                artifact="files.json")
        if not isinstance(metadata, dict) or not isinstance(files, list):
            raise ValueError
        PreparedReviewInputs((run_dir / "review-inputs").absolute(), metadata["head_sha"],
                             tuple(file["filename"] for file in files))
        return ()
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return (AuditIssue("repository_context_invalid", "repository-context.json", "invalid"),)


def replay_run(run_dir: Path) -> ReplayResult:
    """Recompute captured findings with captured policy, never executing the review."""
    findings_data = decode_artifact(
        read_artifact(run_dir / "findings.json"), artifact="findings.json",
    )
    config_data = decode_artifact(read_artifact(run_dir / "config.json"), artifact="config.json")
    evidence_version = 1
    if isinstance(config_data, dict) and "findings" in config_data:
        try:
            config = parse_config(json.dumps(config_data))
        except ConfigError:
            raise ReplayError("config.findings: invalid captured evidence contract") from None
        evidence_version = 2 if config.findings.evidence == "v2" else 1
    policy = parse_policy(config_data)
    findings, findings_issues = parse_findings(findings_data, evidence_version=evidence_version)
    if evidence_version == 2:
        from scrutare.findings.dedupe import dedupe_findings
        from scrutare.findings.evidence import validate_evidence
        try:
            checked = tuple(validate_evidence(source, run_dir / "review-inputs")
                            for group in findings for source in group.sources)
            if tuple(source for group in findings for source in group.sources) != checked:
                raise ValueError
            findings = dedupe_findings(checked)
        except (OSError, ValueError, TypeError, KeyError):
            raise ReplayError("findings: captured citation validation disagrees") from None
    issues = findings_issues + _yaml_issues(run_dir, policy) + _context_issues(run_dir, policy)
    posting = inspect_posting(run_dir)
    posted_sha256 = posting.verdict_sha256
    raw = None
    try:
        raw = read_artifact(run_dir / "verdict.json")
        saved = parse_saved_verdict(raw, evidence_version=evidence_version)
    except ReplayError:
        return ReplayResult(None, None, None, sha256(raw).hexdigest() if raw is not None else None,
                            posted_sha256, posting, (), issues + (
                                AuditIssue("saved_verdict_unavailable", "verdict.json",
                                           "incomplete"),
                            ))
    saved_sha256 = sha256(saved.raw).hexdigest()
    try:
        exhaustion = validate_exhaustion(saved, policy)
    except ReplayError:
        return ReplayResult(None, None, None, saved_sha256, posted_sha256, posting, (), issues + (
            AuditIssue("exhaustion_config_disagrees", "verdict.exhaustion", "incomplete"),
        ))
    unresolved: tuple[str, ...] = ()
    if isinstance(config_data, dict) and "findings" in config_data:
        config = parse_config(json.dumps(config_data))
        if config.findings.assessment.enabled:
            from scrutare.engine.assessment import AssessmentResult, parse_assessments
            from scrutare.findings.models import finding_from_artifact
            try:
                assessment = decode_artifact(read_artifact(run_dir / "assessment.json"),
                                             artifact="assessment.json")
                if (not isinstance(assessment, dict)
                        or assessment.get("schema_version") != 1
                        or assessment.get("kind") != "model_based_not_formal_proof"
                        or assessment.get("status") != "complete"
                        or type(assessment.get("attempt_limit")) is not int
                        or assessment["attempt_limit"] != 1
                        or assessment.get("allocation_tokens") != config.findings.assessment.tokens
                        or not isinstance(assessment.get("candidates"), list)):
                    raise ValueError
                originals = tuple(finding_from_artifact(item, evidence_version=2)
                                  for item in assessment["candidates"])
                outcome = assessment.get("outcome")
                if originals and (not isinstance(outcome, dict)
                                  or outcome.get("status") != "complete"
                                  or outcome.get("accounting_complete") is not True
                                  or outcome.get("output_available") is not True
                                  or outcome.get("allocated_tokens")
                                  != config.findings.assessment.tokens):
                    raise ValueError
                for original in originals:
                    if validate_evidence(original, run_dir / "review-inputs") != original:
                        raise ValueError
                rows = parse_assessments({"assessments": assessment["assessments"]}, originals,
                                         run_dir / "review-inputs", artifact=True)
                result = AssessmentResult("complete", rows)
                sources = tuple(source for group in findings for source in group.sources)
                if sources != tuple(source for group in dedupe_findings(result.retained)
                                    for source in group.sources):
                    raise ValueError
                unresolved = result.unresolved_blocking(config.verdict.blocking_categories)
            except (OSError, ValueError, TypeError, KeyError):
                raise ReplayError("assessment: captured policy or evidence disagrees") from None
    verdict = derive_verdict(findings, policy.settings, exhaustion=exhaustion,
                             evidence_version=evidence_version, unresolved_candidates=unresolved)
    candidate_bytes = verdict.to_bytes()
    identical = saved.raw == candidate_bytes
    differences = diff_verdicts(saved.document, verdict.to_dict())
    if not identical and not differences:
        differences = (ReplayDifference("verdict.encoding", "encoding", saved_sha256,
                                        sha256(candidate_bytes).hexdigest()),)
    posted_identical = (
        sha256(candidate_bytes).hexdigest() == posted_sha256 if posted_sha256 is not None else None
    )
    return ReplayResult(verdict, identical, posted_identical, saved_sha256, posted_sha256,
                        posting, differences, issues)
