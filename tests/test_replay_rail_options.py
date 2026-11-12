"""Replay binds opt-in request retries, cache breakpoints and effort to their evidence."""

import json

import pytest
from test_rail_options_session import RETRY, capability, load, run
from test_review_inputs import capture as capture

from scrutare.config import ModelRail, parse_config
from scrutare.replay.audit import _native_recovery_issues, _rail_option_issues

OPTIONS = {"request_retries": 2, "request_retry_seconds": 45, "cache_breakpoints": True,
           "effort": "high"}


def config(**options):
    return parse_config(json.dumps({"models": {"default": {
        "provider": "openai", "model": "m", **options}}})).to_dict()


def codes(capture, document):
    return [(issue.code, issue.path.rsplit("/", 1)[-1], issue.severity)
            for issue in _rail_option_issues(capture, document)]


def session(capture, tmp_path, *, events=RETRY, retries=1, **options):
    options = options or OPTIONS
    outcome, attempt, _ = run(capture, tmp_path, ModelRail("openai", None, "m", **options),
                              extra=events, retries=retries)
    assert outcome.status == "complete"
    return attempt, config(**options)


def edit(path, change):
    document = load(path)
    change(document)
    path.write_text(json.dumps(document))


def test_a_session_with_every_option_audits_clean(capture, tmp_path):
    attempt, document = session(capture, tmp_path)
    assert codes(capture, document) == [] and _native_recovery_issues(capture) == ()
    assert load(attempt / "result.json")["request_retries"] == {"limit": 2, "retried": 1}


def test_runs_that_never_set_an_option_are_not_audited(capture, tmp_path):
    outcome, _, _ = run(capture, tmp_path, ModelRail("openai", None, "m"))
    assert outcome.status == "complete"
    assert codes(capture, config()) == [] and _native_recovery_issues(capture) == ()
    # Not even with an unreadable configuration: nothing here depends on it.
    assert codes(capture, {"models": 3}) == []


def test_an_opted_in_run_without_any_session_is_incomplete(tmp_path):
    assert codes(tmp_path, config(effort="low")) == [
        ("rail_options_evidence_missing", "sessions", "incomplete")]


@pytest.mark.parametrize("change", [
    lambda argv: argv.__setitem__(argv.index("--request-retries") + 1, "5"),
    lambda argv: argv.__setitem__(argv.index("--request-retry-seconds") + 1, "60"),
    lambda argv: argv.remove("--cache-breakpoints"),
    lambda argv: argv.append("--cache-breakpoints"),
    lambda argv: argv.__setitem__(argv.index("--cache-breakpoints"), "--cache-breakpoints=1"),
    lambda argv: argv.__setitem__(argv.index("--effort") + 1, "max"),
    lambda argv: argv.extend(("--effort", "high")),
    lambda argv: argv.__delitem__(slice(argv.index("--effort"), argv.index("--effort") + 2)),
    lambda argv: argv.__delitem__(slice(argv.index("--request-retry-seconds"),
                                        argv.index("--request-retry-seconds") + 2)),
])
def test_a_changed_lost_or_repeated_flag_is_unrecorded(capture, tmp_path, change):
    attempt, document = session(capture, tmp_path)
    edit(attempt / "invocation.json", lambda d: change(d["argv"]))
    assert codes(capture, document) == [
        ("rail_options_unrecorded", "invocation.json", "invalid")]


@pytest.mark.parametrize("change", [
    lambda rail: rail.pop("effort"),
    lambda rail: rail.update(request_retries=3),
    lambda rail: rail.update(request_retry_seconds=45.0),
    lambda rail: rail.update(cache_breakpoints=1),
    lambda rail: rail.update(model="other"),
])
def test_a_recorded_rail_that_differs_from_the_configured_rail_is_unrecorded(
        capture, tmp_path, change):
    attempt, document = session(capture, tmp_path)
    edit(attempt / "invocation.json", lambda d: change(d["rail"]))
    assert ("rail_options_unrecorded", "invocation.json", "invalid") in codes(capture, document)


def test_a_session_that_recorded_an_option_the_run_never_configured_is_unrecorded(
        capture, tmp_path):
    attempt, _ = session(capture, tmp_path)
    assert codes(capture, config()) == [
        ("rail_options_unrecorded", "invocation.json", "invalid")]
    assert codes(capture, {"models": 3}) == [
        ("rail_options_config_invalid", "config.json", "invalid")]


@pytest.mark.parametrize("change", [
    lambda result: result.pop("request_retries"),
    lambda result: result["request_retries"].update(retried=0),
    lambda result: result["request_retries"].update(retried=2),
    lambda result: result["request_retries"].update(limit=3),
    lambda result: result["request_retries"].update(retried=None),
    lambda result: result["request_retries"].update(extra=True),
    lambda result: result.update(cache_breakpoints={"enabled": False}),
    lambda result: result.pop("cache_breakpoints"),
    lambda result: result.update(effort={"level": "low"}),
    lambda result: result.pop("effort"),
])
def test_a_result_record_the_evidence_does_not_support_disagrees(capture, tmp_path, change):
    attempt, document = session(capture, tmp_path)
    edit(attempt / "result.json", change)
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]


def test_the_recorded_resends_must_match_the_retained_stream(capture, tmp_path):
    attempt, document = session(capture, tmp_path)
    stream = attempt / "stdout.jsonl"
    stream.write_bytes(stream.read_bytes().replace(b'"retry"', b'"other"'))
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]
    stream.unlink()
    assert codes(capture, document) == [
        ("rail_options_evidence_missing", "stdout.jsonl", "incomplete")]
    (attempt / "result.json").unlink()
    assert codes(capture, document) == [
        ("rail_options_evidence_missing", "result.json", "incomplete")]


def test_a_saved_session_that_records_another_effort_disagrees(capture, tmp_path):
    attempt, document = session(capture, tmp_path)
    assert load(attempt / "session.json")["effort"] == "high"
    edit(attempt / "session.json", lambda saved: saved.update(effort="low"))
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]


def test_an_unset_option_must_leave_no_record(capture, tmp_path):
    attempt, document = session(capture, tmp_path, events="", retries=0, effort="low")
    assert codes(capture, document) == []
    edit(attempt / "result.json",
         lambda result: result.update(request_retries={"limit": 0, "retried": 0}))
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]


@pytest.mark.parametrize(("recorded", "argv", "valid"), [
    (2, "2", True), (2, "0", False), (2, "3", False), (None, "0", True), (None, "2", False),
    (0, "0", True), (True, "1", False), (-1, "-1", False),
])
def test_the_recovery_policy_follows_the_recorded_resend_limit(tmp_path, recorded, argv, valid):
    attempt = tmp_path / "sessions/security/attempt-0001"
    attempt.mkdir(parents=True)
    rail = {} if recorded is None else {"request_retries": recorded}
    (attempt / "invocation.json").write_text(json.dumps({
        "contract": 3, "nare_version": "2026.11.94", "rail": rail,
        "argv": ["nare", "run", "review", "--contract", "3", "--max-cutoffs", "0",
                 "--request-retries", argv, "--max-corrections", "1"]}))
    assert (_native_recovery_issues(tmp_path) == ()) is valid


def test_the_capability_fixture_lists_every_option_flag():
    assert all(capability().flag(flag) is not None
               for flag in ("--request-retry-seconds", "--cache-breakpoints", "--effort"))
