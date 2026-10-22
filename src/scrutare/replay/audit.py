"""Read-only verdict reconstruction from the run's captured deciding evidence."""

import json
import os
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from scrutare.config import CATEGORIES, ConfigError, ReviewConfig, _load_yaml, parse_config
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.models import Finding
from scrutare.findings.verdict import Degradation, derive_verdict
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


def _session_records(run_dir: Path) -> list[Path]:
    """Every session attempt directory under the run, without following directory links."""
    found = []
    for root, directories, _ in os.walk(run_dir, followlinks=False):
        current = Path(root)
        if (current.parent.parent.name == "sessions"
                and re.fullmatch(r"attempt-[0-9]{4}", current.name)):
            found.append(current)
            directories[:] = []
        directories.sort()
    return sorted(found)


def _opted_in(config_data: object, field: str = "structured_output") -> bool:
    models = config_data.get("models") if isinstance(config_data, dict) else None
    if not isinstance(models, dict):
        return False
    overrides = models.get("overrides")
    rails = [models.get("default"), *(overrides.values() if isinstance(overrides, dict) else ())]
    return any(isinstance(rail, dict) and rail.get(field) is not None for rail in rails)


def _invocations(attempts: list[Path]) -> list[tuple[Path, object]]:
    """Each launched attempt with its decoded invocation, or None when it is unreadable."""
    invocations: list[tuple[Path, object]] = []
    for attempt in attempts:
        if not os.path.lexists(attempt / "invocation.json"):
            if os.path.lexists(attempt / "stdout.jsonl"):
                invocations.append((attempt, None))  # Ran without recording what it asked.
            continue  # Never launched (for example refused admission): nothing was sent.
        try:
            invocations.append((attempt, decode_artifact(
                read_artifact(attempt / "invocation.json"), artifact="invocation.json")))
        except ReplayError:
            invocations.append((attempt, None))
    return invocations


def _terminal_stop(stdout: bytes) -> tuple[bool, object]:
    """Whether the retained stream ended in nare's result record, and its stop reason."""
    lines = [line for line in stdout.split(b"\n") if line.strip()]
    try:
        terminal = decode_artifact(lines[-1], artifact="stdout.jsonl") if lines else None
    except (ReplayError, RecursionError):
        terminal = None
    if isinstance(terminal, dict) and terminal.get("type") == "result":
        return True, terminal.get("stop_reason")
    return False, None


def _max_output_tokens_issues(run_dir: Path, config_data: object) -> tuple[AuditIssue, ...]:
    """Check that each session passed and recorded exactly the cap its configured rail set.

    Runs that never set a cap and whose sessions never recorded one are not audited, so
    earlier bundles replay as before. A cut-off session must say so, and carry no answer.
    """
    attempts = _session_records(run_dir)
    invocations = _invocations(attempts)

    def recorded(document: object) -> bool:
        if not isinstance(document, dict):
            return False
        rail, argv = document.get("rail"), document.get("argv")
        return ((isinstance(rail, dict) and "max_output_tokens" in rail)
                or (isinstance(argv, list) and "--max-tokens" in argv))

    opted_in = _opted_in(config_data, "max_output_tokens")
    if not opted_in and not any(recorded(document) for _, document in invocations):
        return ()
    if opted_in and not attempts:
        return (AuditIssue("max_output_tokens_evidence_missing", "sessions", "incomplete"),)
    try:
        config = parse_config(json.dumps(config_data))
    except (ConfigError, TypeError, ValueError):
        return (AuditIssue("max_output_tokens_config_invalid", "config.json.models", "invalid"),)

    def key(rail: Any) -> tuple[object, ...]:
        return (rail.provider, rail.model, rail.base_url, rail.max_output_tokens)

    configured = {key(rail)
                  for rail in (config.models.default, *(r for _, r in config.models.overrides))}
    personas = {p if isinstance(p, str) else p.name for p in config.personas}
    issues = []
    for attempt, document in invocations:
        where = attempt.relative_to(run_dir).as_posix()
        try:
            if not isinstance(document, dict):
                raise ValueError
            rail, argv = document["rail"], document["argv"]
            if not isinstance(rail, dict) or not isinstance(argv, list):
                raise ValueError
            cap = rail.get("max_output_tokens")
            if "max_output_tokens" in rail and (type(cap) is not int or cap <= 0):
                raise ValueError
            recorded_rail = (rail.get("provider"), rail.get("model"), rail.get("base_url"), cap)
            persona = document.get("persona")
            if recorded_rail not in ({key(config.models.for_persona(persona))}
                                     if persona in personas else configured):
                raise ValueError
            flags = [index for index, value in enumerate(argv) if value == "--max-tokens"]
            if (len(flags) != (cap is not None)
                    or any(argv[index + 1:index + 2] != [str(cap)] for index in flags)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            issues.append(AuditIssue("max_output_tokens_unrecorded",
                                     f"{where}/invocation.json", "invalid"))
            continue
        if not os.path.lexists(attempt / "result.json"):
            issues.append(AuditIssue("max_output_tokens_evidence_missing",
                                     f"{where}/result.json", "incomplete"))
            continue
        try:
            outcome = decode_artifact(read_artifact(attempt / "result.json"),
                                      artifact="result.json")
        except ReplayError:
            outcome = None
        if not isinstance(outcome, dict):
            valid = False
        elif cap is None:
            valid = "max_output_tokens" not in outcome and outcome.get("reason") != "output_cap"
        else:
            record = outcome.get("max_output_tokens")
            reached = record.get("reached") if isinstance(record, dict) else None
            valid = (isinstance(record, dict) and set(record) == {"limit", "reached"}
                     and type(record["limit"]) is int and record["limit"] == cap
                     and type(reached) is bool
                     and reached == (outcome.get("reason") == "output_cap")
                     and (not reached or (outcome.get("status") == "failed"
                                          and outcome.get("output_available") is False
                                          and outcome.get("findings", []) == []
                                          and outcome.get("corrections", []) == [])))
            if valid:
                try:
                    finished, stop = _terminal_stop(read_artifact(attempt / "stdout.jsonl"))
                except ReplayError:
                    if reached or outcome.get("nare_status") is not None or outcome.get(
                            "status") in ("complete", "partial"):
                        # A session that reached nare's result had its stream captured.
                        issues.append(AuditIssue("max_output_tokens_evidence_missing",
                                                 f"{where}/stdout.jsonl", "incomplete"))
                        continue
                    finished, stop = False, None
                valid = (stop == "max_tokens") == bool(reached) if finished else not reached
        if not valid:
            issues.append(AuditIssue("max_output_tokens_result_disagrees",
                                     f"{where}/result.json", "invalid"))
    return tuple(issues)


def _structured_output_issues(run_dir: Path, config_data: object) -> tuple[AuditIssue, ...]:
    """Check that each session recorded exactly the constraint its configured rail asked for.

    Runs that never opted in and whose sessions never recorded a constraint are not audited,
    so earlier bundles replay as before.
    """
    attempts = _session_records(run_dir)
    invocations = _invocations(attempts)

    def recorded(document: object) -> bool:
        if not isinstance(document, dict):
            return False
        rail, argv = document.get("rail"), document.get("argv")
        return ((isinstance(rail, dict) and "structured_output" in rail)
                or (isinstance(argv, list) and "--structured-output" in argv))

    opted_in = _opted_in(config_data)
    if not opted_in and not any(recorded(document) for _, document in invocations):
        return ()
    if opted_in and not attempts:
        return (AuditIssue("structured_output_evidence_missing", "sessions", "incomplete"),)
    try:
        config = parse_config(json.dumps(config_data))
    except (ConfigError, TypeError, ValueError):
        return (AuditIssue("structured_output_config_invalid", "config.json.models", "invalid"),)
    def key(rail: Any) -> tuple[object, ...]:
        return (rail.provider, rail.model, rail.base_url, rail.structured_output)

    configured = {key(rail)
                  for rail in (config.models.default, *(r for _, r in config.models.overrides))}
    # Configured personas use their own rail; reserved executors (assessor, chair) never
    # share a configured name and may use any configured rail.
    personas = {p if isinstance(p, str) else p.name for p in config.personas}
    issues = []
    for attempt, document in invocations:
        where = attempt.relative_to(run_dir).as_posix()
        try:
            if not isinstance(document, dict):
                raise ValueError
            rail, argv = document["rail"], document["argv"]
            if not isinstance(rail, dict) or not isinstance(argv, list):
                raise ValueError
            mode = rail.get("structured_output")
            recorded_rail = (rail.get("provider"), rail.get("model"), rail.get("base_url"), mode)
            persona = document.get("persona")
            if ("structured_output" in rail and mode != "json_schema") or recorded_rail not in (
                    {key(config.models.for_persona(persona))} if persona in personas
                    else configured):
                raise ValueError
            flags = [index for index, value in enumerate(argv) if value == "--structured-output"]
            if (len(flags) != (mode is not None)
                    or any(argv[index + 1:index + 2] != [mode] for index in flags)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            issues.append(AuditIssue("structured_output_unrecorded",
                                     f"{where}/invocation.json", "invalid"))
            continue
        # A launched session always writes its result, even when cancelled.
        if not os.path.lexists(attempt / "result.json"):
            issues.append(AuditIssue("structured_output_evidence_missing",
                                     f"{where}/result.json", "incomplete"))
            continue
        try:
            outcome = decode_artifact(read_artifact(attempt / "result.json"),
                                      artifact="result.json")
        except ReplayError:
            outcome = None
        record = outcome.get("structured_output") if isinstance(outcome, dict) else None
        count = record.get("constrained_turns") if isinstance(record, dict) else None
        finished = isinstance(outcome, dict) and outcome.get("status") in ("complete", "partial")
        valid = (record is None and isinstance(outcome, dict)
                 and "structured_output" not in outcome) if mode is None else (
            isinstance(record, dict) and set(record) == {"mode", "constrained_turns"}
            and record["mode"] == mode
            and (count is None or (type(count) is int and count >= 0))
            and not (finished and count is None))
        if valid and mode is not None:
            from scrutare.engine.session_output import constrained_turns
            try:
                retained = constrained_turns(read_artifact(attempt / "stdout.jsonl"), mode,
                                             strict=False)
            except ReplayError:
                retained = None
            if retained is None and count is not None:
                # A counted session started, so its event stream was captured.
                issues.append(AuditIssue("structured_output_evidence_missing",
                                         f"{where}/stdout.jsonl", "incomplete"))
                continue
            # No count means the session never started, so no constrained turn was recorded.
            valid = retained is None or retained == (count or 0)
        if not valid:
            issues.append(AuditIssue("structured_output_result_disagrees",
                                     f"{where}/result.json", "invalid"))
    return tuple(issues)


def _panel_fallback(run_dir: Path, degradation: Degradation, persona: str,
                    outcome: dict[str, Any]) -> None:
    """The published record must state this fallback, with complete exact accounting."""
    panel = decode_artifact(read_artifact(run_dir / "panel.json"), artifact="panel.json")
    ledger = panel.get("ledger") if isinstance(panel, dict) else None
    if (not isinstance(panel, dict) or not isinstance(ledger, dict)
            or ledger.get("accounting_complete") is not True
            or panel.get("status") != "partial"
            or panel.get("reason") != "assessment_overrun_fallback"
            or panel.get("degraded") != degradation.to_dict()):
        raise ValueError("fallback: published record disagrees")
    personas = ledger.get("personas")
    usage = outcome.get("usage")
    entry = next((p for p in personas if isinstance(p, dict) and p.get("persona") == persona),
                 None) if isinstance(personas, list) else None
    if (not isinstance(entry, dict) or not isinstance(usage, dict)
            or entry.get("usage") != usage):
        raise ValueError("fallback: assessor accounting disagrees")


class _Fallback(Exception):
    """The validated assessor fallback replaces assessment rows by the candidates."""


def _assessment_fallback(run_dir: Path, config: ReviewConfig, assessment: dict[str, Any],
                         originals: tuple[Finding, ...],
                         findings: tuple[MergedFinding, ...]) -> Degradation:
    """Accept a recorded assessor fallback only from a measured, exactly accounted overrun."""
    from scrutare.engine.discussion import equivalent_groups
    from scrutare.engine.overrun import artifact_status, native_overrun, phase_degradation
    from scrutare.findings.dedupe import dedupe_findings

    outcome = assessment.get("outcome")
    tokens = config.findings.assessment.tokens
    if (not isinstance(outcome, dict) or outcome.get("allocated_tokens") != tokens
            or assessment.get("status") != artifact_status(outcome)
            or assessment.get("assessments") != []):
        raise ValueError("assessment: fallback needs the admitted assessor outcome")
    persona = outcome.get("persona")
    if not isinstance(persona, str) or "/" in persona:
        raise ValueError("assessment: fallback needs the assessor identity")
    native_overrun(run_dir / "sessions" / persona / "attempt-0001", outcome)
    degradation = phase_degradation("assessment", (outcome,), round_number=None,
                                    allowance=tokens)
    if degradation is None:
        raise ValueError("assessment: fallback needs a measured overrun")
    grouped = (equivalent_groups(originals) if config.findings.discussion.enabled
               else dedupe_findings(originals))
    if findings != grouped:
        raise ValueError("assessment: fallback must keep every anchor-validated finding")
    _panel_fallback(run_dir, degradation, persona, outcome)
    if config.findings.discussion.enabled:
        trace = decode_artifact(read_artifact(run_dir / "debate.json"), artifact="debate.json")
        if (not isinstance(trace, dict) or trace.get("kind") != "focused_evidence_discussion"
                or trace.get("status") != "partial" or trace.get("rounds") != []
                or trace.get("final_assessments") != []
                or trace.get("degraded") != degradation.to_dict()):
            raise ValueError("assessment: fallback trace disagrees")
    return degradation


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
            if config.findings.discussion.enabled:
                from scrutare.engine.discussion import equivalent_groups
                grouped = equivalent_groups(checked)
                if findings != grouped:
                    raise ValueError
                findings = grouped
            else:
                findings = dedupe_findings(checked)
        except (OSError, ValueError, TypeError, KeyError):
            raise ReplayError("findings: captured citation validation disagrees") from None
    issues = (findings_issues + _yaml_issues(run_dir, policy) + _context_issues(run_dir, policy)
              + _structured_output_issues(run_dir, config_data)
              + _max_output_tokens_issues(run_dir, config_data))
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
    degradation = None
    if isinstance(config_data, dict) and "findings" in config_data:
        config = parse_config(json.dumps(config_data))
        if config.findings.assessment.enabled:
            from scrutare.engine.assessment import AssessmentResult, parse_assessments
            from scrutare.findings.models import finding_from_artifact
            try:
                assessment = decode_artifact(read_artifact(run_dir / "assessment.json"),
                                             artifact="assessment.json")
                # Only the opt-in policy may replace a complete assessment by a fallback.
                fallback = (isinstance(assessment, dict)
                            and assessment.get("status") != "complete"
                            and config.findings.assessment.overrun == "fallback")
                if (not isinstance(assessment, dict)
                        or assessment.get("schema_version") != 1
                        or assessment.get("kind") != "model_based_not_formal_proof"
                        or (assessment.get("status") != "complete" and not fallback)
                        or type(assessment.get("attempt_limit")) is not int
                        or assessment["attempt_limit"] != 1
                        or assessment.get("allocation_tokens") != config.findings.assessment.tokens
                        or not isinstance(assessment.get("candidates"), list)):
                    raise ValueError
                originals = tuple(finding_from_artifact(item, evidence_version=2)
                                  for item in assessment["candidates"])
                outcome = assessment.get("outcome")
                if fallback:
                    degradation = _assessment_fallback(run_dir, config, assessment, originals,
                                                       findings)
                elif originals and (not isinstance(outcome, dict)
                                  or outcome.get("status") != "complete"
                                  or outcome.get("accounting_complete") is not True
                                  or outcome.get("output_available") is not True
                                  or outcome.get("allocated_tokens")
                                  != config.findings.assessment.tokens):
                    raise ValueError
                for original in originals:
                    if validate_evidence(original, run_dir / "review-inputs") != original:
                        raise ValueError
                if fallback:
                    raise _Fallback
                rows = parse_assessments({"assessments": assessment["assessments"]}, originals,
                                         run_dir / "review-inputs", artifact=True)
                if config.findings.discussion.enabled:
                    from scrutare.replay.discussion import replay_discussion
                    rows, degradation = replay_discussion(run_dir, config, rows, exhaustion)
                result = AssessmentResult("complete", rows)
                sources = tuple(source for group in findings for source in group.sources)
                from scrutare.engine.discussion import equivalent_groups
                groups = (equivalent_groups(result.retained) if config.findings.discussion.enabled
                          else dedupe_findings(result.retained))
                if sources != tuple(source for group in groups
                                    for source in group.sources):
                    raise ValueError
                unresolved = result.unresolved_blocking(config.verdict.blocking_categories)
            except _Fallback:
                pass
            except (OSError, ValueError, TypeError, KeyError):
                raise ReplayError("assessment: captured policy or evidence disagrees") from None
    verdict = derive_verdict(findings, policy.settings, exhaustion=exhaustion,
                             evidence_version=evidence_version, unresolved_candidates=unresolved,
                             degradation=degradation)
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
