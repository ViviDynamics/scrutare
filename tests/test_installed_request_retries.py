"""Installed nare proof: resent model requests are counted once, recorded and replayed.

nare waits two seconds before a first resend and four before a second, so each scripted
transient failure costs these tests that long.
"""

import asyncio
import json
from dataclasses import replace

import pytest
from test_fanout import configure
from test_installed_citation_correction import first_answer, fixed, review
from test_installed_review import wheel_cli as wheel_cli
from test_installed_structured_output import answer
from test_nare_cli_integration import FINDING, load, observations, offline_runtime, sse_choice
from test_nare_cli_integration import installed as installed
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

READ = [sse_choice({"tool_calls": [{"index": 0, "id": "read-1", "function": {
            "name": "read", "arguments": '{"path": "diff.patch"}'}}]}),
        sse_choice({}, "tool_calls"),
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}, "[DONE]"]
FINAL = answer(json.dumps(FINDING), {"prompt_tokens": 20, "completion_tokens": 9})
# A reply the provider had fully generated and metered, cut off before its completion marker.
DROPPED = {"frames": answer(json.dumps(FINDING),
                            {"prompt_tokens": 20, "completion_tokens": 9})[:-1],
           "interrupt": True}


def http_review(capture, tmp_path, installed, turns, *, timeout=None, capacity=None, **rail):
    document = configure(capture, personas=["senior-dev"], review=200, per=200).to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model",
        "base_url": "https://offline.invalid/v1", **rail}
    if capacity is not None:
        document["models"]["capacity"] = capacity
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    runtime = offline_runtime(tmp_path, installed, default={"http_sse": {"turns": turns}})
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    spec["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))
    if timeout is not None:
        runtime = replace(runtime, timeout_seconds=timeout)
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    outcome = result.initial.outcomes[0]
    return result, outcome, observations(outcome, http_sse=True)


def events(attempt, name="stdout.jsonl"):
    return [json.loads(line) for line in (attempt / name).read_text().splitlines()
            if line.strip()]


def test_resent_requests_are_counted_once_through_the_real_transport(
        capture, tmp_path, installed):
    turns = [{"status": 503}, {"frames": READ}, DROPPED, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns,
                                            request_retries=2)
    assert result.status == "complete" and result.verdict.verdict == "changes_requested"
    attempt = outcome.artifact_directory
    stream = events(attempt)
    retried = [e["detail"]["retry"] for e in stream
               if e["type"] == "progress" and "retry" in e["detail"]]
    assert [(r["attempt"], r["retries"], r["reason"], r["status"]) for r in retried] == [
        (1, 2, "status", 503), (1, 2, "connection", None)]
    # Four attempts reached the endpoint and two replies completed. Only those two carry a
    # cost event, and their sum is the result, the outcome and the shared ledger.
    payloads = [call["payload"] for call in observed["calls"]]
    assert len(payloads) == 4
    costs = [e["detail"] for e in stream if e["type"] == "cost"]
    assert [(c["input"], c["output"]) for c in costs] == [(12, 3), (20, 9)]
    terminal = stream[-1]
    assert terminal["type"] == "result" and terminal["retries"] == 2 and terminal["turns"] == 2
    assert terminal["usage"]["total"] == 44 == terminal["budget"]["used_tokens"]
    assert outcome.usage.to_dict() == {"input": 32, "output": 12, "cache_read": 0,
                                       "cache_write": 0, "total": 44}
    assert outcome.accounting_complete and result.accounting_complete
    assert result.usage == outcome.usage
    assert load(capture / "fanout.json")["ledger"]["usage"]["total"] == 44
    # A resend carries the request it replaces, byte for byte.
    assert payloads[0] == payloads[1] and payloads[2] == payloads[3]
    assert all(body["num_retries"] is None for body in payloads if "num_retries" in body)
    invocation, recorded = load(attempt / "invocation.json"), load(attempt / "result.json")
    argv = invocation["argv"]
    assert argv[argv.index("--request-retries") + 1] == "2"
    assert argv[argv.index("--request-retry-seconds") + 1] == "60"
    assert invocation["rail"]["request_retries"] == 2
    assert recorded["request_retries"] == {"limit": 2, "retried": 2}
    assert load(attempt / "session.json")["retries"] == 2
    assert load(capture / "fanout.json")["personas"][0]["rail"]["request_retries"] == 2
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_a_request_that_outlives_its_resends_fails_as_a_provider_failure(
        capture, tmp_path, installed):
    turns = [{"frames": READ}, {"status": 503}, {"status": 503}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns,
                                            request_retries=1, request_retry_seconds=30)
    assert result.status == "failed" and result.verdict is None
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    # The completed first turn is still counted; the failed request reported nothing.
    assert outcome.usage.total == 15 and not outcome.accounting_complete
    assert len(observed["calls"]) == 3
    attempt = outcome.artifact_directory
    assert load(attempt / "result.json")["request_retries"] == {"limit": 1, "retried": 1}
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[argv.index("--request-retry-seconds") + 1] == "30"
    terminal = events(attempt)[-1]
    assert (terminal["status"], terminal["stop_reason"], terminal["retries"]) == (
        "error", None, 1)


@pytest.mark.parametrize("rail", [{}, {"request_retries": 0}])
def test_without_the_option_the_first_transient_failure_still_ends_the_session(
        capture, tmp_path, installed, rail):
    turns = [{"status": 503}, {"frames": READ}, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns, **rail)
    assert result.status == "failed" and (outcome.status, outcome.reason) == (
        "failed", "provider")
    assert len(observed["calls"]) == 1 and outcome.usage.total == 0
    attempt = outcome.artifact_directory
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[argv.index("--request-retries") + 1] == "0"
    assert "--request-retry-seconds" not in argv
    recorded = load(attempt / "result.json")
    assert recorded.get("request_retries") == (
        {"limit": 0, "retried": 0} if rail else None)
    assert ("request_retries" in load(attempt / "invocation.json")["rail"]) is bool(rail)


def test_resends_on_a_resumed_correction_turn_stay_cumulative_and_replay(
        tmp_path, installed, wheel_cli):
    result, run, attempt, replay, _ = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: fixed(p, end_line=1), rail_options=", request_retries: 2",
        first_failures=(429,), correction_failures=(503,))
    envelope = json.loads(result.stdout)
    assert (envelope["verdict"], envelope["panel_status"]) == ("changes_requested", "complete")
    outcome = load(attempt / "result.json")
    assert outcome["citation_correction"]["outcome"] == "corrected"
    # One resend in each invocation of the same session: nare's count is over its life.
    assert outcome["request_retries"] == {"limit": 2, "retried": 2}
    first, second = events(attempt)[-1], events(attempt, "correction.stdout.jsonl")[-1]
    assert (first["retries"], second["retries"]) == (1, 2)
    assert load(attempt / "correction-session.json")["retries"] == 2
    # Every completed reply is counted exactly once across both invocations.
    costs = [e["detail"]["total"] for name in ("stdout.jsonl", "correction.stdout.jsonl")
             for e in events(attempt, name) if e["type"] == "cost"]
    assert len(costs) == 4 and sum(costs) == outcome["usage"]["total"] == second["usage"]["total"]
    assert envelope["usage"]["total"] == outcome["usage"]["total"]
    assert outcome["accounting_complete"] is True
    assert replay.returncode == 0, replay.stderr.decode()
    replayed = json.loads(replay.stdout)
    assert replayed["saved_verdict"]["byte_identical"] is True and replayed["issues"] == []
