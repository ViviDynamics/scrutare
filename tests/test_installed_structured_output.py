"""Installed nare proofs: a tool turn, a malformed answer, then a constrained correction."""

import asyncio
import json

from test_fanout import configure
from test_nare_cli_integration import (
    FINDING,
    installed,
    load,
    observations,
    offline_runtime,
    sse_choice,
    text,
    tool,
)
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

assert installed  # pytest fixture, imported for its name
MALFORMED = json.dumps(FINDING) + " stray tokens �"


def rail(capture, *, structured):
    config = configure(capture, personas=["senior-dev"], review=200, per=200)
    document = config.to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model", "base_url": "https://offline.invalid/v1",
        **({"structured_output": "json_schema"} if structured else {})}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    return config


def answer(content, usage):
    return [sse_choice({"content": content[:17]}), sse_choice({"content": content[17:]}),
            sse_choice({}, "stop"), {"choices": [], "usage": usage}, "[DONE]"]


def http_scenario():
    read = [sse_choice({"tool_calls": [{"index": 0, "id": "read-1", "function": {
                "name": "read", "arguments": '{"path": "diff.patch"}'}}]}),
            sse_choice({}, "tool_calls"),
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}, "[DONE]"]
    return {"http_sse": {"turns": [
        {"frames": read},
        {"frames": answer(MALFORMED, {"prompt_tokens": 20, "completion_tokens": 9})},
        {"frames": answer(json.dumps(FINDING), {"prompt_tokens": 30, "completion_tokens": 9})},
    ]}}


def http_review(capture, tmp_path, installed, *, structured):
    config = rail(capture, structured=structured)
    runtime = offline_runtime(tmp_path, installed, default=http_scenario())
    spec_path = tmp_path / "offline-spec.json"
    specification = load(spec_path)
    # A public dummy credential lets the actual nare factory initialize. No live key is read.
    specification["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    spec_path.write_text(json.dumps(specification))
    return asyncio.run(run_review(capture, config, runtime=runtime))


def test_the_real_openai_rail_constrains_only_the_correction(capture, tmp_path, installed):
    result = http_review(capture, tmp_path, installed, structured=True)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome, http_sse=True)
    assert result.status == "complete", (outcome.reason, observed["calls"])
    assert result.verdict.verdict == "changes_requested"
    payloads = [call["payload"] for call in observed["calls"]]
    assert ["response_format" in body for body in payloads] == [False, False, True]
    assert ["tools" in body for body in payloads] == [True, True, False]
    schema = load(outcome.artifact_directory / "findings.schema.json")
    assert payloads[2]["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "answer", "schema": schema,
                                               "strict": True}}
    invocation = load(outcome.artifact_directory / "invocation.json")
    assert invocation["argv"][-2:] == ["--structured-output", "json_schema"]
    assert invocation["rail"]["structured_output"] == "json_schema"
    assert load(outcome.artifact_directory / "result.json")["structured_output"] == {
        "mode": "json_schema", "constrained_turns": 1}
    assert load(capture / "fanout.json")["personas"][0]["rail"]["structured_output"] == (
        "json_schema")
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_off_sends_no_constraint_through_the_real_rail(capture, tmp_path, installed):
    result = http_review(capture, tmp_path, installed, structured=False)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome, http_sse=True)
    assert result.status == "complete"
    payloads = [call["payload"] for call in observed["calls"]]
    assert all("response_format" not in body and "tools" in body for body in payloads)
    invocation = load(outcome.artifact_directory / "invocation.json")
    assert "--structured-output" not in invocation["argv"]
    assert "structured_output" not in invocation["rail"]
    assert "structured_output" not in load(outcome.artifact_directory / "result.json")
    assert "structured_output" not in load(capture / "config.json")["models"]["default"]
    replayed = replay_run(capture)
    assert replayed.issues == () and replayed.exit_code == 0


def factory_review(capture, tmp_path, installed, *, supported):
    config = rail(capture, structured=True)
    runtime = offline_runtime(tmp_path, installed, default={
        "structured_output": supported,
        "replies": [tool(), {"content": [{"type": "text", "text": MALFORMED}]}, text()]})
    return asyncio.run(run_review(capture, config, runtime=runtime))


def test_the_factory_seam_receives_the_schema_on_the_tool_free_correction(
        capture, tmp_path, installed):
    result = factory_review(capture, tmp_path, installed, supported=True)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome)
    assert result.status == "complete"
    calls = observed["calls"]
    assert [bool(call["tools"]) for call in calls] == [True, True, False]
    assert ["response_schema" in call for call in calls] == [False, False, True]
    assert calls[2]["response_schema"] == load(
        outcome.artifact_directory / "findings.schema.json")
    events = [json.loads(line) for line in
              (outcome.artifact_directory / "stdout.jsonl").read_text().splitlines()]
    assert [e["detail"]["structured_output"] for e in events
            if "structured_output" in e.get("detail", {})] == [
        {"mode": "json_schema", "tools_withheld": True}]


def test_a_backend_that_cannot_constrain_fails_without_a_model_call(capture, tmp_path, installed):
    result = factory_review(capture, tmp_path, installed, supported=False)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome)
    assert result.status == "failed" and result.verdict is None
    assert outcome.status == "failed" and outcome.reason == "not_started"
    assert observed["calls"] == []
    assert b"does not support structured output" in (
        outcome.artifact_directory / "stderr.txt").read_bytes()
    assert load(outcome.artifact_directory / "result.json")["structured_output"] == {
        "mode": "json_schema", "constrained_turns": 0}
