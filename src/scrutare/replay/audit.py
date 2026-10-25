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


def _terminal_cut_off(stdout: bytes) -> tuple[bool, bool]:
    """Whether the retained stream ended in nare's result record, and in a cut-off turn.

    A cut-off reads from the stop reason, or from nare's cut-off error event when the
    budget check fired after that turn; only the last three records are decoded.
    """
    from scrutare.engine.session_output import cut_off_at_max_tokens

    def decoded(line: bytes) -> object:
        try:
            return decode_artifact(line, artifact="stdout.jsonl")
        except (ReplayError, RecursionError):
            return None

    records = [decoded(line) for line in
               [line for line in stdout.split(b"\n") if line.strip()][-3:]]
    terminal = records[-1] if records else None
    if not isinstance(terminal, dict) or terminal.get("type") != "result":
        return False, False
    cut_off = cut_off_at_max_tokens(records[:-1], terminal.get("status"),
                                    terminal.get("stop_reason"))
    if cut_off and terminal.get("stop_reason") == "budget":
        # The engine reconciles the budget record with the result; replay checks the same.
        budget = records[-2]
        detail = budget.get("detail") if isinstance(budget, dict) else None
        cut_off = (isinstance(detail, dict) and detail.get("usage") == terminal.get("usage")
                   and detail.get("budget") == terminal.get("budget"))
    return True, cut_off


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
                    finished, cut_off = _terminal_cut_off(
                        read_artifact(attempt / "stdout.jsonl"))
                except ReplayError:
                    if reached or outcome.get("nare_status") is not None or outcome.get(
                            "status") in ("complete", "partial"):
                        # A session that reached nare's result had its stream captured.
                        issues.append(AuditIssue("max_output_tokens_evidence_missing",
                                                 f"{where}/stdout.jsonl", "incomplete"))
                        continue
                    finished, cut_off = False, False
                valid = cut_off == bool(reached) if finished else not reached
        if not valid:
            issues.append(AuditIssue("max_output_tokens_result_disagrees",
                                     f"{where}/result.json", "invalid"))
    return tuple(issues)


def _stream(stdout: bytes) -> tuple[list[Any], dict[str, Any] | None]:
    """A retained stream's events and its terminal result record, read tolerantly."""
    records = []
    for line in stdout.split(b"\n"):
        if line.strip():
            try:
                records.append(decode_artifact(line, artifact="stdout.jsonl"))
            except (ReplayError, RecursionError):
                return [], None
    if records and isinstance(records[-1], dict) and records[-1].get("type") == "result":
        return records[:-1], records[-1]
    return records, None


_UNOFFERED = {"offered": False, "defects": 0, "outcome": None}
_OUTCOMES = ("corrected", "refused", "no_answer", "failed")


def _citation_correction_issues(run_dir: Path, config_data: object) -> tuple[AuditIssue, ...]:
    """Recompute each discovery session's citation correction from its retained evidence.

    Whether a correction was owed, which defects it named, its prompt, schema and argv,
    and what the corrected answer became, all follow from the first stream, the retained
    review inputs and the correction stream. Runs that never opted in and carry no record
    are not audited, so earlier bundles replay as before.
    """
    from scrutare.engine.session_output import _findings, cut_off_at_max_tokens, findings_schema
    from scrutare.findings.citation_correction import (
        apply_citation_correction,
        citation_correction_prompt,
        citation_correction_schema,
        citation_defects,
    )
    from scrutare.findings.evidence import CitationValidationError
    from scrutare.findings.models import finding_wire

    attempts = _session_records(run_dir)
    names = ("citation-correction.json", "correction.stdout.jsonl")

    def result_of(attempt: Path) -> object:
        try:
            return decode_artifact(read_artifact(attempt / "result.json"), artifact="result.json")
        except ReplayError:
            return None

    try:
        config = parse_config(json.dumps(config_data))
        opted_in = config.findings.citation_correction
    except (ConfigError, TypeError, ValueError):
        opted_in = None
    if not opted_in:
        def has_record(attempt: Path) -> bool:
            result = result_of(attempt)
            return (any(os.path.lexists(attempt / name) for name in names)
                    or (isinstance(result, dict) and "citation_correction" in result))

        recorded = [attempt for attempt in attempts if has_record(attempt)]
        if opted_in is None and recorded:
            return (AuditIssue("citation_correction_config_invalid", "config.json.findings",
                               "invalid"),)
        return tuple(AuditIssue("citation_correction_unconfigured",
                                f"{attempt.relative_to(run_dir).as_posix()}/result.json",
                                "invalid") for attempt in recorded)
    root = run_dir / "review-inputs"
    discovery_schema = findings_schema(evidence_version=2)
    capped = any(rail.max_output_tokens is not None for rail in
                 (config.models.default, *(r for _, r in config.models.overrides)))
    issues = []
    for attempt in attempts:
        where = attempt.relative_to(run_dir).as_posix()
        if not os.path.lexists(attempt / "invocation.json"):
            continue
        try:
            invocation = decode_artifact(read_artifact(attempt / "invocation.json"),
                                         artifact="invocation.json")
            schema = decode_artifact(read_artifact(attempt / "findings.schema.json"),
                                     artifact="findings.schema.json")
        except ReplayError:
            invocation = schema = None
        discovery = (isinstance(invocation, dict) and "purpose" not in invocation
                     and schema == discovery_schema)
        outcome = result_of(attempt)
        if not isinstance(outcome, dict):
            continue
        record = outcome.get("citation_correction")
        if not discovery:
            if record is not None or any(os.path.lexists(attempt / name) for name in names):
                issues.append(AuditIssue("citation_correction_unrecorded",
                                         f"{where}/result.json", "invalid"))
            continue
        if (not isinstance(record, dict) or set(record) != set(_UNOFFERED)
                or type(record["offered"]) is not bool or type(record["defects"]) is not int
                or (record["outcome"] is None) == record["offered"]
                or (record["offered"] and record["outcome"] not in _OUTCOMES)
                or (not record["offered"] and record != _UNOFFERED)):
            issues.append(AuditIssue("citation_correction_unrecorded",
                                     f"{where}/result.json", "invalid"))
            continue
        persona = outcome.get("persona")
        namespace = outcome.get("artifact_directory")
        if not isinstance(persona, str) or not isinstance(namespace, str):
            issues.append(AuditIssue("citation_correction_unrecorded",
                                     f"{where}/result.json", "invalid"))
            continue

        def parse(document: object, persona: str = persona, namespace: str = namespace) -> Any:
            return _findings(document, persona, evidence_version=2, expected_root=root,
                             candidate_namespace=namespace)

        # What the first stream owed: a finished answer within budget, refused only for
        # correctable citation defects.
        try:
            events, terminal = _stream(read_artifact(attempt / "stdout.jsonl"))
        except ReplayError:
            events, terminal = [], None
        defects = None
        refusal: dict[str, object] | None = None
        if isinstance(terminal, dict):
            budget = terminal.get("budget")
            within = (isinstance(budget, dict) and type(budget.get("used_tokens")) is int
                      and type(budget.get("tokens")) is int
                      and budget["used_tokens"] <= budget["tokens"])
            cut = capped and cut_off_at_max_tokens(events, terminal.get("status"),
                                                   terminal.get("stop_reason"))
            if terminal.get("status") == "done" and within and not cut:
                try:
                    parse(terminal.get("output"))
                except CitationValidationError as error:
                    refusal = {"reason": "citation_validation", "citation_reason": error.reason}
                except (ValueError, TypeError, KeyError):
                    refusal = {"reason": "protocol", "citation_reason": None}
                if refusal is not None:
                    defects = citation_defects(terminal.get("output"), root, parse=parse)
        if not record["offered"]:
            if defects is not None or any(os.path.lexists(attempt / name) for name in names):
                issues.append(AuditIssue("citation_correction_disagrees",
                                         f"{where}/result.json", "invalid"))
            continue
        if defects is None or record["defects"] != len(defects):
            issues.append(AuditIssue("citation_correction_disagrees",
                                     f"{where}/result.json", "invalid"))
            continue
        missing = next((name for name in ("citation-correction.json",
                                          "citation-correction.schema.json")
                        if not os.path.lexists(attempt / name)), None)
        if missing is not None:
            issues.append(AuditIssue("citation_correction_evidence_missing",
                                     f"{where}/{missing}", "incomplete"))
            continue
        try:
            request = decode_artifact(read_artifact(attempt / "citation-correction.json"),
                                      artifact="citation-correction.json")
            constraint = decode_artifact(
                read_artifact(attempt / "citation-correction.schema.json"),
                artifact="citation-correction.schema.json")
            prompt = citation_correction_prompt(defects)
            argv = request["argv"] if isinstance(request, dict) else None

            def flag(name: str, argv: Any = argv) -> object:
                return argv[argv.index(name) + 1] if argv.count(name) == 1 else None

            valid = (isinstance(request, dict) and isinstance(argv, list)
                     and request.get("defects") == [defect.to_dict() for defect in defects]
                     and request.get("refusal") == refusal
                     and request.get("prompt") == prompt
                     and request.get("prompt_sha256") == sha256(prompt.encode()).hexdigest()
                     and request.get("max_turns") == 1
                     and argv[1:3] == ["run", prompt]
                     and flag("--tools") == "none" and flag("--max-turns") == "1"
                     and flag("--budget-tokens") == str(outcome.get("invocation_limit"))
                     and isinstance(flag("--resume"), str)
                     and Path(str(flag("--resume"))).name == "session.json"
                     and isinstance(flag("--session"), str)
                     and Path(str(flag("--session"))).name == "correction-session.json"
                     and Path(str(flag("--resume"))).parent == Path(str(flag("--session"))).parent
                     and Path(str(flag("--resume"))).parent.name == attempt.name
                     and constraint == citation_correction_schema(defects))
        except (ReplayError, KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            issues.append(AuditIssue("citation_correction_disagrees",
                                     f"{where}/citation-correction.json", "invalid"))
            continue
        # What the correction stream supports.
        if not os.path.lexists(attempt / "correction.stdout.jsonl"):
            if record["outcome"] != "failed":
                issues.append(AuditIssue("citation_correction_evidence_missing",
                                         f"{where}/correction.stdout.jsonl", "incomplete"))
            continue
        try:
            events, ending = _stream(read_artifact(attempt / "correction.stdout.jsonl"))
        except ReplayError:
            events, ending = [], None
        expected: str = "failed"
        findings: list[object] | None = None
        rejected: str | None = None
        if isinstance(ending, dict):
            answered = ending.get("status") == "done" and not (capped and cut_off_at_max_tokens(
                events, ending.get("status"), ending.get("stop_reason")))
            expected = "no_answer"
            if answered:
                try:
                    corrected = parse(apply_citation_correction(
                        terminal.get("output") if isinstance(terminal, dict) else None, defects,
                        ending.get("output")))
                    expected, findings = "corrected", [finding_wire(f) for f in corrected]
                except CitationValidationError:
                    expected, rejected = "refused", "citation_validation"
                except (ValueError, TypeError, KeyError):
                    expected, rejected = "refused", "protocol"
        status, reason = outcome.get("status"), outcome.get("reason")
        if record["outcome"] == "corrected":
            fields = ("input", "output", "cache_read", "cache_write")
            usage = ending.get("usage") if isinstance(ending, dict) else None
            recorded_usage = outcome.get("usage")
            # The session's usage is cumulative: nare's terminal record of the resumed turn.
            valid = (expected == "corrected" and status in ("complete", "partial")
                     and outcome.get("findings") == findings
                     and isinstance(usage, dict) and isinstance(recorded_usage, dict)
                     and all(recorded_usage.get(field) == usage.get(field) for field in fields))
        elif record["outcome"] == "refused":
            valid = (expected == "refused" and status == "failed" and reason == rejected
                     and outcome.get("findings") == [])
        elif record["outcome"] == "no_answer":
            valid = (expected == "no_answer" and status == "failed"
                     and reason == (refusal or {}).get("reason")
                     and outcome.get("findings") == [])
        else:
            valid = status == "failed" and outcome.get("findings") == []
        if not valid:
            issues.append(AuditIssue("citation_correction_disagrees", f"{where}/result.json",
                                     "invalid"))
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
              + _max_output_tokens_issues(run_dir, config_data)
              + _citation_correction_issues(run_dir, config_data))
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
