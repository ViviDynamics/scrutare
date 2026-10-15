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
    return directory


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
