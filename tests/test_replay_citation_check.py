"""Replay binds a recorded in-session citation check to configuration, argv and streams."""

import json
from dataclasses import replace

import pytest
from test_citation_check_session import (
    SESSION,
    VALID,
    correction_event,
    error_event,
    load,
    run,
)
from test_review_inputs import capture as capture
from test_review_inputs import save_json

from scrutare.config import FindingSettings
from scrutare.replay.audit import _citation_check_issues, _native_recovery_issues


def issues(capture):
    config = load(capture / "config.json")
    return [(issue.code, issue.path) for issue in
            _citation_check_issues(capture, config) + _native_recovery_issues(capture)]


def where(attempt, capture, name):
    return f"{attempt.relative_to(capture).as_posix()}/{name}"


def edit(path, change):
    document = load(path)
    change(document)
    path.write_text(json.dumps(document))


REFUSED = dict(output=None, status="error", stop="schema_violation",
               extra=correction_event(1, "domain", ["line_range_outside_capture"])
               + error_event(["line_range_outside_capture"]),
               corrections=[{"kind": "domain", "charged": True, "codes": []}])
CORRECTED = dict(output=VALID, extra=correction_event(1, "domain", ["path_not_captured"]),
                 corrections=[{"kind": "domain", "charged": True, "codes": []}])


@pytest.mark.parametrize("findings,script", [
    (SESSION, dict(output=VALID)),
    (SESSION, CORRECTED),
    (SESSION, REFUSED),
    (replace(SESSION, max_corrections=3, cited_reads=True), dict(output=VALID)),
    (SESSION, dict(output=None, status="error", stop=None,
                   error="check raised CheckCommandFailed: exit status 3")),
], ids=["passed", "corrected", "refused", "cited-reads", "no-verdict"])
def test_every_recorded_outcome_audits_clean(capture, tmp_path, findings, script):
    run(capture, tmp_path, findings, **script)
    assert issues(capture) == []


def test_runs_that_never_opted_in_are_not_audited(capture, tmp_path):
    run(capture, tmp_path, FindingSettings("v2"), output=VALID)
    assert issues(capture) == []


def test_a_record_in_a_run_that_never_opted_in_is_unconfigured(capture, tmp_path):
    _, attempt = run(capture, tmp_path, SESSION, output=VALID)
    config = load(capture / "config.json")
    del config["findings"]["citation_check"]
    save_json(capture / "config.json", config)
    assert ("citation_check_unconfigured", where(attempt, capture, "invocation.json")) in issues(
        capture)


@pytest.mark.parametrize("change", [
    lambda d: d["citation_check"].update(max_corrections=3),
    lambda d: d["citation_check"].update(check_timeout_seconds=300),
    lambda d: d["citation_check"].update(checker_sha256="0" * 64),
    lambda d: d["citation_check"]["argv"].__setitem__(1, "-E"),
    lambda d: d["citation_check"]["argv"].__setitem__(7, "/elsewhere"),
    lambda d: d["argv"].extend(("--check-timeout", "30")),
    lambda d: d["argv"].__setitem__(d["argv"].index("--check-command") + 1, '["python3"]'),
    lambda d: d.pop("citation_check"),
], ids=["budget", "timeout", "digest", "interpreter-flag", "root", "repeated-flag",
        "other-command", "unrecorded"])
def test_a_check_other_than_the_configured_one_is_unrecorded(capture, tmp_path, change):
    _, attempt = run(capture, tmp_path, SESSION, output=VALID)
    edit(attempt / "invocation.json", change)
    assert ("citation_check_unrecorded", where(attempt, capture, "invocation.json")) in issues(
        capture)


def test_the_recovery_audit_accepts_only_the_recorded_budget(capture, tmp_path):
    _, attempt = run(capture, tmp_path, SESSION, output=VALID)
    edit(attempt / "invocation.json", lambda d: d["argv"].__setitem__(
        d["argv"].index("--max-corrections") + 1, "3"))
    found = issues(capture)
    assert ("native_recovery_policy_invalid", where(attempt, capture, "invocation.json")) in found
    assert ("citation_check_unrecorded", where(attempt, capture, "invocation.json")) in found


def test_a_map_other_than_the_manifests_disagrees(capture, tmp_path):
    _, attempt = run(capture, tmp_path, replace(SESSION, cited_reads=True), output=VALID)
    edit(attempt / "cited-read-map.json", lambda d: d.update({"src/caller.py": ["diff.patch"]}))
    assert issues(capture) == [("citation_check_disagrees",
                                where(attempt, capture, "cited-read-map.json"))]


@pytest.mark.parametrize("change", [
    lambda d: d["citation_check"].update(outcome="passed"),
    lambda d: d["citation_check"].update(corrections=[]),
    lambda d: d["citation_check"].update(refusal=["path_not_captured"]),
    lambda d: d.update(reason="provider"),
], ids=["outcome", "corrections", "refusal", "classification"])
def test_a_result_record_the_stream_does_not_support_disagrees(capture, tmp_path, change):
    _, attempt = run(capture, tmp_path, SESSION, **REFUSED)
    edit(attempt / "result.json", change)
    assert issues(capture) == [("citation_check_result_disagrees",
                                where(attempt, capture, "result.json"))]
