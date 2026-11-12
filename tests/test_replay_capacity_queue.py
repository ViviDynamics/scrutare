"""Replay binds opt-in capacity queueing to its rail, argv, result record and streams."""

import json

import pytest
from test_capacity_queue_session import load, refusal, run
from test_review_inputs import capture as capture

from scrutare.config import ModelRail, parse_config
from scrutare.replay.audit import _native_recovery_issues, _rail_option_issues

OPTIONS = {"capacity_queue": True, "capacity_wait_seconds": 45, "capacity_refusals": 4}
EVENTS = refusal(0.25) + refusal(0.5, 2)


def config(**options):
    return parse_config(json.dumps({"models": {"default": {
        "provider": "openai", "model": "m", **options}}})).to_dict()


def codes(capture, document):
    return [(issue.code, issue.path.rsplit("/", 1)[-1], issue.severity)
            for issue in _rail_option_issues(capture, document)]


def session(capture, tmp_path, *, events=EVENTS, refusals=2, **options):
    options = options or OPTIONS
    outcome, attempt, _ = run(capture, tmp_path, ModelRail("openai", None, "m", **options),
                              extra=events, refusals=refusals)
    assert outcome.status == "complete"
    return attempt, config(**options)


def edit(path, change):
    document = load(path)
    change(document)
    path.write_text(json.dumps(document))


def test_a_queued_session_audits_clean(capture, tmp_path):
    attempt, document = session(capture, tmp_path)
    assert codes(capture, document) == [] and _native_recovery_issues(capture) == ()
    assert load(attempt / "result.json")["capacity_queue"] == {
        "enabled": True, "wait_seconds": 45, "refusals_limit": 4, "queued": 2,
        "waited_seconds": 0.75}


def test_the_default_limits_and_a_queue_turned_off_audit_clean(capture, tmp_path):
    attempt, document = session(capture, tmp_path, events="", refusals=0, capacity_queue=True)
    assert codes(capture, document) == []
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[argv.index("--capacity-wait-seconds") + 1] == "1800"
    assert argv[argv.index("--capacity-refusals") + 1] == "50"


def test_a_queue_turned_off_audits_clean(capture, tmp_path):
    attempt, document = session(capture, tmp_path, events="", refusals=0, capacity_queue=False)
    assert codes(capture, document) == []
    assert load(attempt / "result.json")["capacity_queue"] == {"enabled": False}
    edit(attempt / "invocation.json", lambda record: record["argv"].append("--capacity-queue"))
    assert codes(capture, document) == [
        ("rail_options_unrecorded", "invocation.json", "invalid")]


def test_a_run_that_never_queued_is_not_audited(capture, tmp_path):
    outcome, _, _ = run(capture, tmp_path, ModelRail("openai", None, "m"))
    assert outcome.status == "complete"
    assert codes(capture, config()) == []


def remove(argv, flag, values=1):
    index = argv.index(flag)
    del argv[index:index + 1 + values]


@pytest.mark.parametrize("change", [
    lambda argv: remove(argv, "--capacity-queue", 0),
    lambda argv: argv.append("--capacity-queue"),
    lambda argv: remove(argv, "--capacity-wait-seconds"),
    lambda argv: argv.__setitem__(argv.index("--capacity-wait-seconds") + 1, "1800"),
    lambda argv: argv.extend(("--capacity-wait-seconds", "45")),
    lambda argv: remove(argv, "--capacity-refusals"),
    lambda argv: argv.__setitem__(argv.index("--capacity-refusals") + 1, "50"),
    lambda argv: argv.extend(("--capacity-refusals", "4")),
    lambda argv: argv.append("--capacity-refusals=4"),
])
def test_an_argv_that_does_not_carry_the_configured_queue_is_unrecorded(
        capture, tmp_path, change):
    attempt, document = session(capture, tmp_path)
    edit(attempt / "invocation.json", lambda record: change(record["argv"]))
    assert codes(capture, document) == [
        ("rail_options_unrecorded", "invocation.json", "invalid")]


@pytest.mark.parametrize("change", [
    lambda rail: rail.pop("capacity_queue"),
    lambda rail: rail.update(capacity_queue=False),
    lambda rail: rail.update(capacity_wait_seconds=46),
    lambda rail: rail.pop("capacity_refusals"),
])
def test_a_recorded_rail_that_differs_from_the_configuration_is_unrecorded(
        capture, tmp_path, change):
    attempt, document = session(capture, tmp_path)
    edit(attempt / "invocation.json", lambda record: change(record["rail"]))
    assert codes(capture, document) == [
        ("rail_options_unrecorded", "invocation.json", "invalid")]


def test_a_session_that_queued_without_the_configuration_asking_is_unrecorded(
        capture, tmp_path):
    attempt, _ = session(capture, tmp_path)
    assert codes(capture, config()) == [
        ("rail_options_unrecorded", "invocation.json", "invalid")]


@pytest.mark.parametrize("change", [
    lambda result: result.pop("capacity_queue"),
    lambda result: result["capacity_queue"].update(enabled=False),
    lambda result: result["capacity_queue"].update(queued=1),
    lambda result: result["capacity_queue"].update(queued=3),
    lambda result: result["capacity_queue"].update(queued=None),
    lambda result: result["capacity_queue"].update(queued=True),
    lambda result: result["capacity_queue"].update(waited_seconds=0.5),
    lambda result: result["capacity_queue"].update(waited_seconds=None),
    lambda result: result["capacity_queue"].update(waited_seconds="0.75"),
    lambda result: result["capacity_queue"].update(wait_seconds=1800),
    lambda result: result["capacity_queue"].update(refusals_limit=50),
    lambda result: result["capacity_queue"].update(extra=True),
    lambda result: result["capacity_queue"].pop("waited_seconds"),
    lambda result: result.update(capacity_queue={"enabled": True}),
])
def test_a_result_record_the_evidence_does_not_support_disagrees(capture, tmp_path, change):
    attempt, document = session(capture, tmp_path)
    edit(attempt / "result.json", change)
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]


def test_the_recorded_queue_must_match_the_retained_stream(capture, tmp_path):
    attempt, document = session(capture, tmp_path)
    stream = attempt / "stdout.jsonl"
    original = stream.read_bytes()
    stream.write_bytes(original.replace(b'"capacity"', b'"other"'))
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]
    # The same number of refusals with another announced wait.
    stream.write_bytes(original.replace(b'"wait": 0.25', b'"wait": 0.5'))
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]
    stream.unlink()
    assert codes(capture, document) == [
        ("rail_options_evidence_missing", "stdout.jsonl", "incomplete")]
    (attempt / "result.json").unlink()
    assert codes(capture, document) == [
        ("rail_options_evidence_missing", "result.json", "incomplete")]


def test_an_unset_queue_must_leave_no_record(capture, tmp_path):
    # Another rail option is set, so the session is audited; the queue is not.
    attempt, document = session(capture, tmp_path, events="", refusals=0, request_retries=0)
    assert codes(capture, document) == []
    edit(attempt / "result.json",
         lambda result: result.update(capacity_queue={"enabled": False}))
    assert codes(capture, document) == [
        ("rail_options_result_disagrees", "result.json", "invalid")]
