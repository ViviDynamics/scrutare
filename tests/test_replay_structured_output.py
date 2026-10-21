"""Replay verifies that constrained-answer requests were recorded as configured."""

import json

import pytest
from test_replay import bundle

from scrutare.config import parse_config
from scrutare.replay import replay_run

ON = {"provider": "openai", "base_url": "http://localhost:8000/v1", "model": "m",
      "structured_output": "json_schema"}
OFF = {key: value for key, value in ON.items() if key != "structured_output"}


def configure(path, models):
    config = parse_config(json.dumps({"strategy": "debate", "models": models}))
    (path / "config.json").write_text(json.dumps(config.to_dict()))
    return config


def session(path, rail, *, persona="senior-dev", attempt="attempt-0001", flag=None,
            result=True, constrained=1):
    directory = path / "sessions" / persona / attempt
    directory.mkdir(parents=True)
    mode = rail.get("structured_output") if flag is None else flag
    argv = ["nare", "run", "prompt", "--max-turns", "6"]
    if mode:
        argv += ["--structured-output", mode]
    (directory / "invocation.json").write_text(json.dumps(
        {"schema_version": 1, "persona": persona, "argv": argv, "rail": rail}))
    if result:
        record = {"schema_version": 1, "status": "complete"}
        if rail.get("structured_output"):
            record["structured_output"] = {"mode": rail["structured_output"],
                                           "constrained_turns": constrained}
        (directory / "result.json").write_text(json.dumps(record))
    markers = constrained if type(constrained) is int else 0
    (directory / "stdout.jsonl").write_text("".join(marker_line() + "\n" for _ in range(markers)))
    return directory


def marker_line():
    return json.dumps({"type": "progress", "text": "constrained", "timestamp": "now", "detail": {
        "structured_output": {"mode": "json_schema", "tools_withheld": True}}})


def codes(result):
    return [(issue.code, issue.path) for issue in result.issues]


def test_a_constrained_run_whose_sessions_recorded_it_replays_clean(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    session(tmp_path, ON)
    session(tmp_path, ON, persona="assessment", attempt="attempt-0001", constrained=0)
    result = replay_run(tmp_path)
    assert codes(result) == []
    assert result.exit_code == 0


def test_an_unconstrained_run_is_not_audited_and_replays_as_before(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": OFF})
    session(tmp_path, OFF)
    assert codes(replay_run(tmp_path)) == []
    assert replay_run(tmp_path).exit_code == 0


@pytest.mark.parametrize(("models", "rail", "flag", "path"), [
    ({"default": ON}, OFF, None, "sessions/senior-dev/attempt-0001/invocation.json"),
    ({"default": OFF}, ON, None, "sessions/senior-dev/attempt-0001/invocation.json"),
    ({"default": ON}, ON, "", "sessions/senior-dev/attempt-0001/invocation.json"),
    ({"default": OFF}, OFF, "json_schema", "sessions/senior-dev/attempt-0001/invocation.json"),
])
def test_a_session_that_did_not_record_its_configured_constraint_is_invalid(
        tmp_path, models, rail, flag, path):
    bundle(tmp_path)
    configure(tmp_path, models)
    session(tmp_path, rail, flag=flag)
    result = replay_run(tmp_path)
    assert codes(result) == [("structured_output_unrecorded", path)]
    assert result.exit_code == 2


def test_an_override_that_clears_the_constraint_is_a_configured_rail(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON, "overrides": {"security": {"structured_output": None}}})
    session(tmp_path, ON)
    session(tmp_path, OFF, persona="security")
    assert codes(replay_run(tmp_path)) == []


@pytest.mark.parametrize("change", ["missing", "mode", "count"])
def test_a_result_that_disagrees_with_its_invocation_is_invalid(tmp_path, change):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    directory = session(tmp_path, ON)
    record = json.loads((directory / "result.json").read_bytes())
    if change == "missing":
        del record["structured_output"]
    elif change == "mode":
        record["structured_output"]["mode"] = "grammar"
    else:
        record["structured_output"]["constrained_turns"] = -1
    (directory / "result.json").write_text(json.dumps(record))
    result = replay_run(tmp_path)
    assert codes(result) == [("structured_output_result_disagrees",
                              "sessions/senior-dev/attempt-0001/result.json")]
    assert result.exit_code == 2


def test_a_constrained_configuration_without_session_evidence_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    result = replay_run(tmp_path)
    assert codes(result) == [("structured_output_evidence_missing", "sessions")]
    assert result.exit_code == 2


def test_nested_round_sessions_are_audited_too(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    session(tmp_path, ON)
    session(tmp_path / "discussion" / "round-1", OFF)
    assert codes(replay_run(tmp_path)) == [
        ("structured_output_unrecorded",
         "discussion/round-1/sessions/senior-dev/attempt-0001/invocation.json")]


def test_attempts_that_were_never_launched_are_not_audited(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    session(tmp_path, ON)
    refused = tmp_path / "sessions" / "senior-dev" / "attempt-0002"
    refused.mkdir()
    (refused / "result.json").write_text(json.dumps({"status": "not_started",
                                                    "reason": "review_budget"}))
    (tmp_path / "sessions" / "debate-chair" / "attempt-0001").mkdir(parents=True)
    result = replay_run(tmp_path)
    assert codes(result) == [] and result.exit_code == 0


def test_a_configured_persona_must_use_its_own_rail(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON, "overrides": {"security": {"structured_output": None}}})
    session(tmp_path, ON, persona="security")
    assert codes(replay_run(tmp_path)) == [
        ("structured_output_unrecorded", "sessions/security/attempt-0001/invocation.json")]


def test_a_reserved_session_may_use_any_configured_rail(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON, "overrides": {"security": {"structured_output": None}}})
    session(tmp_path, ON)
    session(tmp_path, ON, persona="assessment-reserved")
    assert codes(replay_run(tmp_path)) == []


def test_a_completed_session_must_record_an_integer_count(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    session(tmp_path, ON, constrained=None)
    assert codes(replay_run(tmp_path)) == [
        ("structured_output_result_disagrees", "sessions/senior-dev/attempt-0001/result.json")]


@pytest.mark.parametrize(("markers", "clean"), [(1, True), (0, False), (2, False)])
def test_the_recorded_count_matches_the_retained_event_stream(tmp_path, markers, clean):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    directory = session(tmp_path, ON, constrained=1)
    (directory / "stdout.jsonl").write_text("".join(marker_line() + "\n" for _ in range(markers)))
    expected = [] if clean else [
        ("structured_output_result_disagrees", "sessions/senior-dev/attempt-0001/result.json")]
    assert codes(replay_run(tmp_path)) == expected


def test_an_opted_in_run_whose_attempts_never_launched_sent_nothing_to_audit(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    refused = tmp_path / "sessions" / "senior-dev" / "attempt-0001"
    refused.mkdir(parents=True)
    (refused / "result.json").write_text(json.dumps({"status": "not_started",
                                                    "reason": "review_budget"}))
    result = replay_run(tmp_path)
    assert codes(result) == [] and result.exit_code == 0


def test_a_session_that_ran_without_its_invocation_record_is_invalid(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    directory = session(tmp_path, ON)
    (directory / "invocation.json").unlink()
    assert codes(replay_run(tmp_path)) == [
        ("structured_output_unrecorded", "sessions/senior-dev/attempt-0001/invocation.json")]


def test_a_launched_session_without_its_result_record_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    directory = session(tmp_path, ON)
    (directory / "result.json").unlink()
    result = replay_run(tmp_path)
    assert codes(result) == [
        ("structured_output_evidence_missing", "sessions/senior-dev/attempt-0001/result.json")]
    assert result.exit_code == 2


def test_a_counted_session_without_its_event_stream_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    directory = session(tmp_path, ON)
    (directory / "stdout.jsonl").unlink()
    assert codes(replay_run(tmp_path)) == [
        ("structured_output_evidence_missing", "sessions/senior-dev/attempt-0001/stdout.jsonl")]


def test_a_never_started_session_cannot_have_constrained_turns_in_its_stream(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": ON})
    directory = session(tmp_path, ON, constrained=None)
    record = json.loads((directory / "result.json").read_bytes())
    record["status"] = "failed"
    (directory / "result.json").write_text(json.dumps(record))
    (directory / "stdout.jsonl").write_text(marker_line() + "\n")
    assert codes(replay_run(tmp_path)) == [
        ("structured_output_result_disagrees", "sessions/senior-dev/attempt-0001/result.json")]
