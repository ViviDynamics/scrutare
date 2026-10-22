"""Replay verifies that each session recorded the output token cap its rail configured."""

import json

import pytest
from test_replay import bundle
from test_replay_structured_output import ON as CONSTRAINED
from test_replay_structured_output import configure

from scrutare.replay import replay_run

CAP = {"provider": "openai", "base_url": "http://localhost:8000/v1", "model": "m",
       "max_output_tokens": 12000}
OFF = {key: value for key, value in CAP.items() if key != "max_output_tokens"}
WHERE = "sessions/senior-dev/attempt-0001"


def terminal(stop):
    return json.dumps({"type": "result", "status": "error" if stop == "max_tokens" else "done",
                       "stop_reason": stop}) + "\n"


def session(path, rail, *, persona="senior-dev", flag=None, result=True, reached=False,
            stop=None, argv_cap=None):
    directory = path / "sessions" / persona / "attempt-0001"
    directory.mkdir(parents=True)
    cap = rail.get("max_output_tokens")
    argv = ["nare", "run", "prompt", "--max-turns", "6"]
    if flag is not None:
        argv += flag
    elif cap is not None:
        argv += ["--max-tokens", str(argv_cap or cap)]
    (directory / "invocation.json").write_text(json.dumps(
        {"schema_version": 1, "persona": persona, "argv": argv, "rail": rail}))
    if result:
        record = ({"schema_version": 1, "status": "failed", "reason": "output_cap",
                   "output_available": False, "findings": []} if reached else
                  {"schema_version": 1, "status": "complete", "reason": "done"})
        if cap is not None:
            record["max_output_tokens"] = {"limit": cap, "reached": reached}
        (directory / "result.json").write_text(json.dumps(record))
    (directory / "stdout.jsonl").write_text(
        terminal(stop or ("max_tokens" if reached else "end_turn")))
    return directory


def codes(result):
    return [(issue.code, issue.path) for issue in result.issues]


def test_a_capped_run_whose_sessions_recorded_the_cap_replays_clean(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    session(tmp_path, CAP)
    session(tmp_path, CAP, persona="assessment")
    result = replay_run(tmp_path)
    assert codes(result) == []
    assert result.exit_code == 0


def test_a_session_cut_off_at_the_cap_replays_clean_as_recorded(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    session(tmp_path, CAP, reached=True)
    assert codes(replay_run(tmp_path)) == []


def test_an_uncapped_run_is_not_audited_and_replays_as_before(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": OFF})
    session(tmp_path, OFF)
    result = replay_run(tmp_path)
    assert codes(result) == []
    assert result.exit_code == 0


def test_the_cap_and_the_constraint_are_audited_independently(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": {**CONSTRAINED, "max_output_tokens": 12000}})
    directory = session(tmp_path, {**CONSTRAINED, "max_output_tokens": 12000},
                        flag=["--structured-output", "json_schema", "--max-tokens", "12000"])
    record = json.loads((directory / "result.json").read_bytes())
    record["structured_output"] = {"mode": "json_schema", "constrained_turns": 0}
    (directory / "result.json").write_text(json.dumps(record))
    assert codes(replay_run(tmp_path)) == []


def test_an_override_that_clears_the_cap_is_a_configured_rail(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP, "overrides": {"security": {"max_output_tokens": None}}})
    session(tmp_path, CAP)
    session(tmp_path, OFF, persona="security")
    assert codes(replay_run(tmp_path)) == []


@pytest.mark.parametrize(("models", "rail", "flag", "argv_cap"), [
    ({"default": CAP}, OFF, None, None),
    ({"default": OFF}, CAP, None, None),
    ({"default": CAP}, CAP, [], None),
    ({"default": CAP}, CAP, None, 999),
    ({"default": CAP}, {**CAP, "max_output_tokens": 999}, None, None),
    ({"default": CAP}, CAP, ["--max-tokens", "12000", "--max-tokens", "12000"], None),
    ({"default": OFF}, OFF, ["--max-tokens", "12000"], None),
    ({"default": CAP}, {**CAP, "max_output_tokens": True}, ["--max-tokens", "True"], None),
])
def test_a_session_that_did_not_record_its_configured_cap_is_invalid(
        tmp_path, models, rail, flag, argv_cap):
    bundle(tmp_path)
    configure(tmp_path, models)
    session(tmp_path, rail, flag=flag, argv_cap=argv_cap)
    result = replay_run(tmp_path)
    assert codes(result) == [("max_output_tokens_unrecorded", f"{WHERE}/invocation.json")]
    assert result.exit_code == 2


@pytest.mark.parametrize("change", ["missing", "limit", "reached", "extra", "answer"])
def test_a_result_that_disagrees_with_its_invocation_is_invalid(tmp_path, change):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    directory = session(tmp_path, CAP, reached=change == "answer")
    record = json.loads((directory / "result.json").read_bytes())
    if change == "missing":
        del record["max_output_tokens"]
    elif change == "limit":
        record["max_output_tokens"]["limit"] = 11999
    elif change == "reached":
        record["max_output_tokens"]["reached"] = True
    elif change == "extra":
        record["max_output_tokens"]["note"] = 1
    else:
        record["findings"] = [{"file": "a.py"}]
    (directory / "result.json").write_text(json.dumps(record))
    result = replay_run(tmp_path)
    assert codes(result) == [("max_output_tokens_result_disagrees", f"{WHERE}/result.json")]
    assert result.exit_code == 2


@pytest.mark.parametrize(("reached", "stop"), [(False, "max_tokens"), (True, "end_turn")])
def test_a_result_that_disagrees_with_the_retained_stream_is_invalid(tmp_path, reached, stop):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    session(tmp_path, CAP, reached=reached, stop=stop)
    assert codes(replay_run(tmp_path)) == [
        ("max_output_tokens_result_disagrees", f"{WHERE}/result.json")]


def test_a_cut_off_session_without_its_stream_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    (session(tmp_path, CAP, reached=True) / "stdout.jsonl").unlink()
    result = replay_run(tmp_path)
    assert codes(result) == [("max_output_tokens_evidence_missing", f"{WHERE}/stdout.jsonl")]
    assert result.exit_code == 2


def test_a_launched_session_without_its_result_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    session(tmp_path, CAP, result=False)
    assert codes(replay_run(tmp_path)) == [
        ("max_output_tokens_evidence_missing", f"{WHERE}/result.json")]


def test_a_capped_configuration_without_session_evidence_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    result = replay_run(tmp_path)
    assert codes(result) == [("max_output_tokens_evidence_missing", "sessions")]
    assert result.exit_code == 2


def test_a_started_session_without_its_stream_is_incomplete(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    directory = session(tmp_path, CAP)
    record = json.loads((directory / "result.json").read_bytes())
    record.update(status="failed", reason="provider", nare_status="error")
    (directory / "result.json").write_text(json.dumps(record))
    (directory / "stdout.jsonl").unlink()
    assert codes(replay_run(tmp_path)) == [
        ("max_output_tokens_evidence_missing", f"{WHERE}/stdout.jsonl")]


def test_a_deeply_nested_stream_line_is_an_audit_issue_not_a_crash(tmp_path):
    bundle(tmp_path)
    configure(tmp_path, {"default": CAP})
    directory = session(tmp_path, CAP)
    (directory / "stdout.jsonl").write_text("[" * 100000 + "]" * 100000 + "\n")
    # No readable result record, as after a timeout: nothing contradicts reached=false.
    assert codes(replay_run(tmp_path)) == []
    record = json.loads((directory / "result.json").read_bytes())
    record.update(status="failed", reason="output_cap", output_available=False, findings=[])
    record["max_output_tokens"]["reached"] = True
    (directory / "result.json").write_text(json.dumps(record))
    assert codes(replay_run(tmp_path)) == [
        ("max_output_tokens_result_disagrees", f"{WHERE}/result.json")]
