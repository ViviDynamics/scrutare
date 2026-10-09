"""Read-only verdict reconstruction from the run's captured deciding evidence."""

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
    findings, findings_issues = parse_findings(decode_artifact(
        read_artifact(run_dir / "findings.json"), artifact="findings.json",
    ))
    policy = parse_policy(decode_artifact(
        read_artifact(run_dir / "config.json"), artifact="config.json",
    ))
    issues = findings_issues + _yaml_issues(run_dir, policy) + _context_issues(run_dir, policy)
    posting = inspect_posting(run_dir)
    posted_sha256 = posting.verdict_sha256
    raw = None
    try:
        raw = read_artifact(run_dir / "verdict.json")
        saved = parse_saved_verdict(raw)
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
    verdict = derive_verdict(findings, policy.settings, exhaustion=exhaustion)
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
