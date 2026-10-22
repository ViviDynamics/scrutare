"""Installed nare proofs: the output cap reaches every OpenAI request, and a cut-off fails."""

import asyncio
import json

import pytest
from test_fanout import configure
from test_installed_structured_output import answer
from test_nare_cli_integration import (
    FINDING,
    installed,
    load,
    observations,
    offline_runtime,
    sse_choice,
)
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run
from scrutare.replay.audit import _max_output_tokens_issues

assert installed  # pytest fixture, imported for its name
CAP = 64


def rail(capture, *, cap):
    config = configure(capture, personas=["senior-dev"], review=200, per=200)
    document = config.to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model", "base_url": "https://offline.invalid/v1",
        **({"max_output_tokens": cap} if cap is not None else {})}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    return config


def read_turn(finish="tool_calls", content=None):
    return [*([sse_choice({"content": content})] if content else []),
            sse_choice({"tool_calls": [{"index": 0, "id": "read-1", "function": {
                "name": "read", "arguments": '{"path": "diff.patch"}'}}]}),
            sse_choice({}, finish),
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}, "[DONE]"]


def cut_off(content, usage):
    # vLLM's length stop: the text so far, then finish_reason "length".
    return [sse_choice({"content": content}), sse_choice({}, "length"),
            {"choices": [], "usage": usage}, "[DONE]"]


def review(capture, tmp_path, installed, turns, *, cap):
    config = rail(capture, cap=cap)
    runtime = offline_runtime(tmp_path, installed, default={"http_sse": {"turns": [
        {"frames": frames} for frames in turns]}})
    spec_path = tmp_path / "offline-spec.json"
    specification = load(spec_path)
    # A public dummy credential lets the actual nare factory initialize. No live key is read.
    specification["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    spec_path.write_text(json.dumps(specification))
    return asyncio.run(run_review(capture, config, runtime=runtime))


COMPLETE = [read_turn(), answer(json.dumps(FINDING), {"prompt_tokens": 30,
                                                      "completion_tokens": 9})]


def test_the_cap_reaches_every_request_of_the_real_openai_rail(capture, tmp_path, installed):
    result = review(capture, tmp_path, installed, COMPLETE, cap=CAP)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome, http_sse=True)
    assert result.status == "complete", (outcome.reason, observed["calls"])
    assert result.verdict.verdict == "changes_requested"
    payloads = [call["payload"] for call in observed["calls"]]
    assert [body.get("max_tokens") for body in payloads] == [CAP, CAP]
    invocation = load(outcome.artifact_directory / "invocation.json")
    assert invocation["argv"][-2:] == ["--max-tokens", str(CAP)]
    assert invocation["rail"]["max_output_tokens"] == CAP
    assert load(outcome.artifact_directory / "result.json")["max_output_tokens"] == {
        "limit": CAP, "reached": False}
    assert load(capture / "fanout.json")["personas"][0]["rail"]["max_output_tokens"] == CAP
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_off_sends_no_cap_through_the_real_rail(capture, tmp_path, installed):
    result = review(capture, tmp_path, installed, COMPLETE, cap=None)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome, http_sse=True)
    assert result.status == "complete"
    assert all("max_tokens" not in call["payload"] for call in observed["calls"])
    invocation = load(outcome.artifact_directory / "invocation.json")
    assert "--max-tokens" not in invocation["argv"]
    assert "max_output_tokens" not in invocation["rail"]
    assert "max_output_tokens" not in load(outcome.artifact_directory / "result.json")
    assert "max_output_tokens" not in load(capture / "config.json")["models"]["default"]
    assert "max_output_tokens" not in load(capture / "fanout.json")["personas"][0]["rail"]
    replayed = replay_run(capture)
    assert replayed.issues == () and replayed.exit_code == 0


@pytest.mark.parametrize("turns", [
    # The answer is cut off at the cap, after a tool turn carried an already valid document.
    [read_turn(content=json.dumps(FINDING)),
     cut_off(json.dumps(FINDING) + " and more", {"prompt_tokens": 30, "completion_tokens": CAP})],
    # A tool call is cut off at the cap: nare does not run it and stops the session.
    [read_turn("length")],
], ids=["answer", "tool-call"])
def test_a_request_cut_off_at_the_cap_fails_the_review_with_its_usage(
        capture, tmp_path, installed, turns):
    result = review(capture, tmp_path, installed, turns, cap=CAP)
    outcome = result.initial.outcomes[0]
    observed = observations(outcome, http_sse=True)
    assert [call["payload"]["max_tokens"] for call in observed["calls"]] == [CAP] * len(turns)
    assert (outcome.status, outcome.reason) == ("failed", "output_cap")
    assert (outcome.nare_status, outcome.stop_reason) == ("error", "max_tokens")
    assert outcome.findings == () and not outcome.output_available
    if len(turns) == 2:  # nare kept the tool turn's valid document; it is not an answer.
        assert load(outcome.artifact_directory / "session.json")["output"] == FINDING
    assert outcome.accounting_complete
    reported = sum(turn[-2]["usage"]["prompt_tokens"] + turn[-2]["usage"]["completion_tokens"]
                   for turn in turns)
    assert outcome.usage.total == reported and result.usage.total == reported
    assert result.status == "failed" and result.verdict is None
    events = [json.loads(line) for line in
              (outcome.artifact_directory / "stdout.jsonl").read_text().splitlines()]
    assert events[-1]["error"] == "the model's turn was cut off at max_tokens"
    assert load(outcome.artifact_directory / "result.json")["max_output_tokens"] == {
        "limit": CAP, "reached": True}
    # A failed review has no verdict to replay; its retained sessions still audit clean.
    assert _max_output_tokens_issues(capture, load(capture / "config.json")) == ()
