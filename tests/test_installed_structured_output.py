"""Installed nare proofs: a tool turn, a malformed answer, then a constrained correction."""

import asyncio
import json
import shutil

import pytest
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
# Nare now accepts one JSON document surrounded by prose. Use a genuinely
# schema-invalid document to exercise the constrained correction request.
MALFORMED = json.dumps({"findings": "not an array"})


def rail(capture, *, structured):
    config = configure(capture, personas=["senior-dev"], review=200, per=200)
    document = config.to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model", "base_url": "https://offline.invalid/v1",
        **({"structured_output": structured if isinstance(structured, str) else "json_schema"}
           if structured else {})}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    return config


def answer(content, usage):
    return [sse_choice({"content": content[:17]}), sse_choice({"content": content[17:]}),
            sse_choice({}, "stop"), {"choices": [], "usage": usage}, "[DONE]"]


def http_scenario(correction=None):
    read = [sse_choice({"tool_calls": [{"index": 0, "id": "read-1", "function": {
                "name": "read", "arguments": '{"path": "diff.patch"}'}}]}),
            sse_choice({}, "tool_calls"),
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}, "[DONE]"]
    return {"http_sse": {"turns": [
        {"frames": read},
        {"frames": answer(MALFORMED, {"prompt_tokens": 20, "completion_tokens": 9})},
        {"frames": correction or answer(json.dumps(FINDING),
                                        {"prompt_tokens": 30, "completion_tokens": 9})},
    ]}}


def http_review(capture, tmp_path, installed, *, structured, correction=None):
    config = rail(capture, structured=structured)
    runtime = offline_runtime(tmp_path, installed, default=http_scenario(correction))
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


def answer_call(arguments, usage):
    """The forced answer function, streamed as an OpenAI-compatible backend sends it."""
    return [sse_choice({"tool_calls": [{"index": 0, "id": "answer-1", "function": {
                "name": "nare_final_answer", "arguments": arguments[:17]}}]}),
            sse_choice({"tool_calls": [{"index": 0, "function": {"arguments": arguments[17:]}}]}),
            sse_choice({}, "tool_calls"), {"choices": [], "usage": usage}, "[DONE]"]


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_the_real_openai_rail_sends_each_answer_encoding_on_the_correction(
        capture, tmp_path, installed, mode):
    usage = {"prompt_tokens": 30, "completion_tokens": 9}
    correction = (answer_call(json.dumps(FINDING), usage) if mode == "tool_call"
                  else answer(json.dumps(FINDING), usage))
    result = http_review(capture, tmp_path, installed, structured=mode, correction=correction)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome, http_sse=True)
    assert result.status == "complete", (outcome.reason, observed["calls"])
    assert result.verdict.verdict == "changes_requested"
    invocation = load(outcome.artifact_directory / "invocation.json")
    # The argv nare receives: the same as json_schema but for the encoding it names.
    assert invocation["argv"][-2:] == ["--structured-output", mode]
    assert invocation["argv"].count("--structured-output") == 1
    assert invocation["rail"]["structured_output"] == mode
    payloads = [call["payload"] for call in observed["calls"]]
    schema = load(outcome.artifact_directory / "findings.schema.json")
    read_tools = payloads[0]["tools"]
    assert payloads[1]["tools"] == read_tools
    assert all("response_format" not in body for body in payloads[:2])
    if mode == "json_object":
        assert payloads[2]["response_format"] == {"type": "json_object"}
        assert "tools" not in payloads[2] and "tool_choice" not in payloads[2]
    else:
        assert "response_format" not in payloads[2]
        assert payloads[2]["tools"] == [{"type": "function", "function": {
            "name": "nare_final_answer", "description": "Return the final structured answer.",
            "parameters": schema}}]
        assert payloads[2]["tool_choice"] == {"type": "function",
                                              "function": {"name": "nare_final_answer"}}
        assert payloads[2]["parallel_tool_calls"] is False
    events = [json.loads(line) for line in
              (outcome.artifact_directory / "stdout.jsonl").read_text().splitlines()]
    assert [e["detail"]["structured_output"] for e in events
            if "structured_output" in e.get("detail", {})] == [
        {"mode": mode, "tools_withheld": True}]
    assert load(outcome.artifact_directory / "result.json")["structured_output"] == {
        "mode": mode, "constrained_turns": 1}
    assert load(capture / "fanout.json")["personas"][0]["rail"]["structured_output"] == mode
    assert load(capture / "config.json")["models"]["default"]["structured_output"] == mode
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "tool_call"])
def test_each_encoding_changes_only_the_flag_it_sends(capture, tmp_path, installed, mode):
    # The argv of an encoding differs from the unconstrained argv by its one option pair.
    def argv(structured):
        directory = tmp_path / str(structured)
        directory.mkdir()
        target = directory / "capture"
        shutil.copytree(capture, target)
        outcome = http_review(target, directory, installed, structured=structured).initial
        sent = load(outcome.outcomes[0].artifact_directory / "invocation.json")["argv"]
        return [item.replace(str(directory), "<run>") for item in sent]

    off, on = argv(False), argv(mode)
    assert on == [*off, "--structured-output", mode]
