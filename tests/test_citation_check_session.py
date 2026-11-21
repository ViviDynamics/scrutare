"""The in-session citation check reaches nare only when chosen and advertised, and is recorded."""

import asyncio
import json
import shutil
from dataclasses import replace

import pytest
from test_citation_correction_defects import CALLER, OPENAI_MODELS, answer, citation, finding
from test_nare_session import RUN_BODY, contract3_capability, executable, module
from test_repository_context import contextual
from test_review_inputs import capture as capture
from test_review_inputs import save_json

from scrutare.config import BudgetSettings, FindingSettings
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.persona_inputs import prepare_persona_inputs
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_artifacts import create_attempt_directory
from scrutare.engine.session_models import NareRuntime
from scrutare.findings.citation_check import (
    CITED_PATHS,
    check_argv,
    cited_read_map,
    package_digest,
)
from scrutare.personas import PersonaDefinition

CHECK_FLAGS = {name: {"takes_value": True, "choices": None} for name in (
    "--check-command", "--check-timeout", "--require-cited-reads", "--cited-read-map")}
NEW_FLAGS = ("--check-command", "--check-timeout", "--require-cited-reads", "--cited-read-map")
VALID = answer(finding(citation()))
PAST_END = answer(finding(citation(end_line=4)))


def capability(**fields):
    fields.setdefault("check_command", True)
    fields.setdefault("cited_read_map", True)
    return contract3_capability(extra=CHECK_FLAGS, **fields)


def fake(tmp_path, *, output, status="done", stop="end_turn", extra="", corrections=None,
         error=None):
    body = (RUN_BODY.replace("INPUT", "4").replace("STATUS", repr(status))
            .replace("STOP", repr(stop)).replace("OUTPUT", repr(output))
            .replace("EXTRA", extra).replace("EXIT", "0" if status == "done" else "1")
            .replace('"contract": 1', '"contract": 3').replace('"2026.10.0"', '"2026.11.94"')
            .replace('a = parser.parse_args()',
                     'for flag in ("max-cutoffs", "request-retries", "max-corrections",\n'
                     '             "compaction", "compaction-trigger", "compaction-target",\n'
                     '             "check-command", "check-timeout", "require-cited-reads",\n'
                     '             "cited-read-map"):\n'
                     '    parser.add_argument("--" + flag)\n'
                     'parser.add_argument("--stream", action="store_true")\n'
                     'a = parser.parse_args()')
            .replace('"used_usd": None}, "error": None}',
                     '"used_usd": None}, "error": ' + repr(error) + '}')
            .replace('result = {"type": "result",',
                     'result = {' + ("" if corrections is None else
                                     f'"corrections": {corrections!r}, ') + '"type": "result",'))
    return executable(tmp_path, version="2026.11.94", contract=3, body=body)


def prepared(capture, findings):
    config, _ = contextual(capture, caller=CALLER)
    config = replace(config, findings=findings, budgets=BudgetSettings(100, 100),
                     models=OPENAI_MODELS)
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    inputs = prepare_review_inputs(capture, config)
    descriptor = prepare_persona_inputs(inputs, (PersonaDefinition("security", "Review."),))[0]
    ledger = ReviewBudgetLedger(("security",), BudgetSettings(100, 100))
    lease = ledger.admit("security", "security/attempt-0001")
    attempt = create_attempt_directory(capture, "security", prepared_root=inputs.root)
    return descriptor, ledger, lease, attempt


def run(capture, tmp_path, findings, found=None, **script):
    descriptor, ledger, lease, attempt = prepared(capture, findings)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, OPENAI_MODELS.default, lease, ledger=ledger, artifact_directory=attempt,
        runtime=NareRuntime(fake(tmp_path, **script), 7, 20),
        capability=capability() if found is None else found))
    return outcome, attempt


def load(path):
    return json.loads(path.read_bytes())


def values(argv, flag):
    return [argv[index + 1] for index, item in enumerate(argv) if item == flag]


SESSION = FindingSettings("v2", citation_check="session")


def test_the_default_sends_the_pinned_invocation_and_records_nothing(capture, tmp_path):
    second = tmp_path / "second" / capture.name
    shutil.copytree(capture, second)
    outcome, attempt = run(capture, tmp_path, FindingSettings("v2"), output=VALID)
    assert outcome.status == "complete"
    invocation, result = load(attempt / "invocation.json"), load(attempt / "result.json")
    argv = invocation["argv"]
    assert values(argv, "--max-corrections") == ["1"]
    assert not set(NEW_FLAGS) & set(argv)
    assert "citation_check" not in invocation and "citation_check" not in result
    assert sorted(path.name for path in attempt.iterdir()) == [
        "cwd", "findings.schema.json", "home", "invocation.json", "result.json",
        "session.json", "stderr.txt", "stdout.jsonl", "timing.json", "tmp"]
    # The session check differs from it only by the budget and the check flags, appended.
    _, checked = run(second, second.parent, SESSION, output=VALID)
    with_check = load(checked / "invocation.json")["argv"]
    index = with_check.index("--check-command")
    assert with_check[index:] == ["--check-command", with_check[index + 1],
                                  "--check-timeout", "30"]
    expected = [item.replace(f"{tmp_path}/", f"{second.parent}/") for item in argv]
    expected[expected.index("--max-corrections") + 1] = "2"
    assert with_check[:index] == expected


def test_the_check_argv_digest_and_budget_are_recorded(capture, tmp_path):
    outcome, attempt = run(capture, tmp_path, replace(SESSION, max_corrections=3),
                           output=VALID, corrections=[])
    assert (outcome.status, outcome.reason) == ("complete", "done")
    invocation = load(attempt / "invocation.json")
    root = attempt.parent.parent.parent / "review-inputs"
    checker = check_argv(root)
    assert invocation["citation_check"] == {
        "mode": "session", "argv": checker, "checker_sha256": package_digest(),
        "check_timeout_seconds": 30, "max_corrections": 3}
    argv = invocation["argv"]
    assert values(argv, "--check-command") == [json.dumps(checker)]
    assert values(argv, "--max-corrections") == ["3"]
    assert load(attempt / "cwd/observed.json")["argv"] == argv[1:]
    assert load(attempt / "result.json")["citation_check"] == {
        "outcome": "passed", "corrections": [], "refusal": None}


def test_cited_reads_send_the_pointer_and_the_attempts_own_map(capture, tmp_path):
    outcome, attempt = run(capture, tmp_path, replace(SESSION, cited_reads=True), output=VALID)
    assert outcome.status == "complete"
    invocation = load(attempt / "invocation.json")
    argv = invocation["argv"]
    assert values(argv, "--require-cited-reads") == [CITED_PATHS]
    assert values(argv, "--cited-read-map") == [str(attempt / "cited-read-map.json")]
    root = attempt.parent.parent.parent / "review-inputs"
    mapped = load(attempt / "cited-read-map.json")
    assert mapped == cited_read_map(root) and mapped["src/caller.py"]
    assert all((root / name).is_file() for names in mapped.values() for name in names)
    from hashlib import sha256
    assert invocation["citation_check"]["cited_read_map_sha256"] == sha256(
        (attempt / "cited-read-map.json").read_bytes()).hexdigest()
    assert invocation["citation_check"]["cited_reads"] == CITED_PATHS


def correction_event(number, kind, codes, charged=True):
    detail = {"schema_correction": {"number": number, "charged": charged, "codes": [],
                                    "earlier_corrections": [], "limit": 2, "kind": kind}}
    if codes:
        detail["errors"] = [{"code": code, "location": [], "message": code, "guidance": None}
                            for code in codes]
    return (f'print(json.dumps({{"type": "progress", "text": "schema correction", "detail": '
            f'{detail!r}, "timestamp": "now"}}), flush=True)\n')


def error_event(codes):
    detail = {"errors": [{"code": code, "location": [], "message": code, "guidance": None}
                         for code in codes]}
    return (f'print(json.dumps({{"type": "error", "text": "answer does not satisfy", '
            f'"detail": {detail!r}, "timestamp": "now"}}), flush=True)\n')


def test_each_correction_the_check_caused_is_recorded(capture, tmp_path):
    events = (correction_event(1, "schema", None)
              + correction_event(2, "domain", ["line_range_outside_capture"]))
    outcome, attempt = run(capture, tmp_path, SESSION, output=VALID, extra=events,
                           corrections=[{"kind": "schema", "charged": True, "codes": ["x"]},
                                        {"kind": "domain", "charged": True, "codes": ["y"]}])
    assert outcome.status == "complete"
    assert load(attempt / "result.json")["citation_check"] == {
        "outcome": "corrected", "refusal": None, "corrections": [
            {"kind": "schema", "charged": True},
            {"kind": "domain", "charged": True, "codes": ["line_range_outside_capture"]}]}


@pytest.mark.parametrize(("codes", "reason", "citation_reason"), [
    (["line_range_outside_capture", "finding_refused"], "citation_validation",
     "line_range_outside_capture"),
    (["finding_refused"], "protocol", None),
])
def test_a_budget_spent_on_the_check_fails_as_the_post_session_validator(
        capture, tmp_path, codes, reason, citation_reason):
    events = correction_event(1, "domain", codes) + error_event(codes)
    outcome, attempt = run(capture, tmp_path, SESSION, output=None, status="error",
                           stop="schema_violation", extra=events,
                           corrections=[{"kind": "domain", "charged": True, "codes": []}])
    assert (outcome.status, outcome.reason) == ("failed", reason)
    result = load(attempt / "result.json")
    assert result["citation_check"]["outcome"] == "refused"
    assert result["citation_check"]["refusal"] == codes
    if citation_reason is None:
        assert not (attempt / "citation-validation.json").exists()
    else:
        assert load(attempt / "citation-validation.json")["reason"] == citation_reason


def test_a_schema_violation_is_not_the_checks(capture, tmp_path):
    events = error_event(["type"])
    outcome, attempt = run(capture, tmp_path, SESSION, output=None, status="error",
                           stop="schema_violation", extra=events)
    # As without the check: nare refused the answer for its schema.
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    assert load(attempt / "result.json")["citation_check"] == {
        "outcome": None, "corrections": [], "refusal": None}


def test_a_check_with_no_verdict_is_a_protocol_failure(capture, tmp_path):
    outcome, attempt = run(capture, tmp_path, SESSION, output=None, status="error", stop=None,
                           error="check raised CheckCommandFailed: exit status 3: no verdict")
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert load(attempt / "result.json")["citation_check"]["outcome"] == "no_verdict"


@pytest.mark.parametrize("events,corrections", [
    # A check correction naming codes the checker never gives.
    (correction_event(1, "domain", ["unexpected_property"]),
     [{"kind": "domain", "charged": True, "codes": []}]),
    # nare's result lists a correction the stream does not show.
    ("", [{"kind": "domain", "charged": True, "codes": []}]),
])
def test_check_evidence_that_disagrees_is_a_protocol_failure(capture, tmp_path, events,
                                                             corrections):
    outcome, attempt = run(capture, tmp_path, SESSION, output=VALID, extra=events,
                           corrections=corrections)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert load(attempt / "result.json")["citation_check"]["outcome"] is None


@pytest.mark.parametrize(("findings", "found", "missing"), [
    (SESSION, capability(check_command=False), "check_command"),
    (replace(SESSION, cited_reads=True), capability(cited_read_map=False), "cited_reads.map"),
])
def test_a_runtime_that_does_not_advertise_it_is_refused_before_any_session(
        capture, tmp_path, findings, found, missing):
    with pytest.raises(module().SessionRuntimeError, match=missing):
        module().require_findings_options(findings, found)
    outcome, attempt = run(capture, tmp_path, findings, found, output=VALID)
    assert (outcome.status, outcome.reason) == ("failed", "invocation")
    assert not (attempt / "cwd/observed.json").exists()
    module().require_findings_options(FindingSettings("v2"), capability(check_command=False))


CHECK_ENTRY = {"flag": "--check-command", "timeout_flag": "--check-timeout",
               "credentials": "withheld", "stdin": {"answer": "a", "reads": "r"}}
MAP_ENTRY = {"flag": "--require-cited-reads",
             "map": {"flag": "--cited-read-map", "requires": "--require-cited-reads"}}


@pytest.mark.parametrize("change,expected", [
    ({}, (True, True)),
    ({"check_command": CHECK_ENTRY | {"credentials": "passed"}}, (False, True)),
    ({"check_command": CHECK_ENTRY | {"stdin": {"answer": "a"}}}, (False, True)),
    ({"check_command": None}, (False, True)),
    ({"cited_reads": {"flag": "--require-cited-reads"}}, (True, False)),
    ({"cited_reads": MAP_ENTRY | {"map": {"flag": "--read-map"}}}, (True, False)),
])
def test_capability_follows_the_contract_entries(change, expected):
    contract = {"check_command": CHECK_ENTRY, "cited_reads": MAP_ENTRY, **change}
    flags = capability().flags
    found = (module()._check_command(contract, flags), module()._cited_read_map(contract, flags))
    assert found == expected
    # Without the flags themselves, or on contracts 1 and 2, nothing is advertised.
    bare = contract3_capability().flags
    assert module()._check_command(contract, bare) is False
    assert module()._cited_read_map(contract, None) is False
