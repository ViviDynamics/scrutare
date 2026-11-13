"""Installed nare proof: queued capacity refusals never reach the ledger, and are recorded.

The real console queues only when Scrutare asks it to. Each scripted refusal names a
Retry-After of a second or less, except in the timeout proof, so these tests take a few
seconds each.
"""

import asyncio
import json

import pytest
from test_fanout import configure
from test_installed_citation_correction import first_answer, fixed, review
from test_installed_request_retries import FINAL, READ, events, http_review
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import load, observations, offline_runtime
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.session_models import CapacityUsage
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

FIXED = {"mode": "fixed", "wait_seconds": 30, "refusals": 5}
ADAPTIVE = {**FIXED, "mode": "adaptive", "ceiling": 4, "floor": 1, "quiet_seconds": 60}
# A proxy's refusal before it dispatches anything: HTTP 429 naming its parallel limit.
REFUSED = {"status": 429, "headers": {"retry-after": "1"}, "body": {"error": {
    "message": "Max parallel request limit reached, max_parallel_requests: 8",
    "type": "rate_limit_error", "code": "429"}}}


def waiting(seconds):
    return {**REFUSED, "headers": {"retry-after": str(seconds)}}


def queue_records(stream):
    return [e["detail"]["capacity"] for e in stream
            if e["type"] == "progress" and "capacity" in e["detail"]]


def limit_records(stream):
    return [e["detail"]["capacity_limit"] for e in stream
            if e["type"] == "progress" and "capacity_limit" in e["detail"]]


def statuses(observed):
    return [call.get("status") for call in observed["calls"]]


@pytest.mark.parametrize("capacity", [FIXED, ADAPTIVE])
def test_refusals_are_waited_out_resent_unchanged_and_accounted_apart(
        capture, tmp_path, installed, capacity):
    turns = [REFUSED, {"frames": READ}, REFUSED, REFUSED, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns,
                                            capacity=capacity)
    assert result.status == "complete" and result.verdict.verdict == "changes_requested"
    attempt = outcome.artifact_directory
    stream = events(attempt)
    assert [(r["attempt"], r["wait"], r["refusals"], r["status"], r["reason"])
            for r in queue_records(stream)] == [(1, 1.0, 1, 429, "max_parallel_requests"),
                                                (1, 1.0, 2, 429, "max_parallel_requests"),
                                                (2, 1.0, 3, 429, "max_parallel_requests")]
    # Five requests reached the endpoint and two replies completed. Only those two carry a
    # cost event, and their sum is the result, the outcome and the shared ledger.
    assert statuses(observed) == [429, None, 429, 429, None]
    payloads = [call["payload"] for call in observed["calls"]]
    costs = [e["detail"] for e in stream if e["type"] == "cost"]
    assert [(c["input"], c["output"]) for c in costs] == [(12, 3), (20, 9)]
    terminal = stream[-1]
    assert terminal["type"] == "result" and terminal["turns"] == 2
    # A queued refusal is not a resend: the retry count stays zero.
    assert (terminal["capacity_refusals"], terminal["retries"]) == (3, 0)
    assert terminal["capacity_wait_seconds"] == 3.0
    assert terminal["usage"]["total"] == 44 == terminal["budget"]["used_tokens"]
    assert outcome.usage.to_dict() == {"input": 32, "output": 12, "cache_read": 0,
                                       "cache_write": 0, "total": 44}
    assert outcome.accounting_complete and result.accounting_complete
    assert result.usage == outcome.usage
    # The request sent again is the request that was refused, byte for byte.
    assert payloads[0] == payloads[1] and payloads[2] == payloads[3] == payloads[4]
    argv = load(attempt / "invocation.json")["argv"]
    index = argv.index("--capacity-queue")
    assert argv[index:index + 5] == ["--capacity-queue", "--capacity-wait-seconds", "30",
                                     "--capacity-refusals", "5"]
    assert argv[argv.index("--request-retries") + 1] == "0"
    assert outcome.capacity is not None and outcome.capacity.source == "result"
    assert (outcome.capacity.refusals, outcome.capacity.wait_seconds) == (3, 3.0)
    record = load(attempt / "result.json")
    assert record["capacity"] == outcome.capacity.to_dict()
    assert load(attempt / "session.json")["capacity_refusals"] == 3
    fanout = load(capture / "fanout.json")
    assert fanout["result"]["capacity"] == {
        "sessions": 1, "refusals": 3, "wait_seconds": 3.0,
        "slot_wait_seconds": outcome.capacity.slot_wait_seconds}
    assert fanout["ledger"]["usage"] == record["usage"] and fanout["ledger"]["usage"][
        "total"] == 44
    if capacity["mode"] == "adaptive":
        assert argv[index + 5:index + 14] == [
            "--capacity-adaptive", "--capacity-ceiling", "4", "--capacity-floor", "1",
            "--capacity-quiet-seconds", "60", "--capacity-shared-dir",
            str(capture / "capacity")]
        # Three refusals in two bursts cut the limit from the ceiling toward the floor.
        assert outcome.capacity.limit is not None and 1 <= outcome.capacity.limit < 4
        assert terminal["capacity_limit"] == outcome.capacity.limit
        assert any(r["reason"] == "refusal" for r in limit_records(stream))
        assert (capture / "capacity").is_dir()
        assert len(list((capture / "capacity").iterdir())) == 1
    else:
        assert "--capacity-adaptive" not in argv
        assert outcome.capacity.limit is None and terminal["capacity_limit"] is None
        assert outcome.capacity.slot_wait_seconds == 0.0 and limit_records(stream) == []
        assert not (capture / "capacity").exists()
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_off_sends_and_records_nothing(capture, tmp_path, installed):
    result, outcome, observed = http_review(
        capture, tmp_path, installed, [{"frames": READ}, {"frames": FINAL}])
    assert result.status == "complete"
    attempt = outcome.artifact_directory
    assert not [item for item in load(attempt / "invocation.json")["argv"]
                if item.startswith("--capacity")]
    assert outcome.capacity is None and "capacity" not in load(attempt / "result.json")
    assert "capacity" not in load(capture / "fanout.json")["result"]
    assert "capacity" not in load(capture / "config.json")["models"]
    assert not (capture / "capacity").exists()
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_off_refusal_is_an_ordinary_provider_failure(capture, tmp_path, installed):
    turns = [REFUSED, {"frames": READ}, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns)
    assert result.status == "failed" and (outcome.status, outcome.reason) == (
        "failed", "provider")
    # Request retries stay 0: the refused request is never resent.
    assert statuses(observed) == [429] and outcome.usage.total == 0
    assert outcome.capacity is None
    stream = events(outcome.artifact_directory)
    assert queue_records(stream) == [] and stream[-1]["capacity_refusals"] == 0


def test_refusals_past_the_configured_limit_fall_back_to_failure(capture, tmp_path, installed):
    turns = [{"frames": READ}, REFUSED, REFUSED, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns,
                                            capacity={**FIXED, "refusals": 1})
    assert result.status == "failed" and result.verdict is None
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    # The completed first turn is still counted; the refused requests reported nothing.
    assert outcome.usage.total == 15 and not outcome.accounting_complete
    assert statuses(observed) == [None, 429, 429]
    assert outcome.capacity == CapacityUsage(1, 1.0, None, 0.0, "result")
    terminal = events(outcome.artifact_directory)[-1]
    assert (terminal["status"], terminal["capacity_refusals"], terminal["retries"]) == (
        "error", 1, 0)


def test_a_request_refused_past_its_wait_bound_ends_the_session(capture, tmp_path, installed):
    # A two second bound per request: nare queues only the waits that fit it.
    turns = [waiting(2), {"frames": READ}, waiting(2), waiting(2), {"frames": FINAL}]
    result, outcome, _ = http_review(capture, tmp_path, installed, turns, timeout=8,
                                     capacity={**FIXED, "wait_seconds": 2})
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    assert (outcome.capacity.refusals, outcome.capacity.wait_seconds) == (2, 4.0)


def test_a_429_without_the_capacity_marker_is_not_queued(capture, tmp_path, installed):
    turns = [{"status": 429}, {"frames": READ}, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns,
                                            capacity=FIXED)
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    assert statuses(observed) == [429] and outcome.capacity.refusals == 0


def test_a_queued_refusal_and_a_resend_are_counted_apart(capture, tmp_path, installed):
    turns = [REFUSED, {"status": 503}, {"frames": READ}, {"frames": FINAL}]
    result, outcome, observed = http_review(capture, tmp_path, installed, turns,
                                            capacity=FIXED, request_retries=1)
    assert result.status == "complete" and outcome.accounting_complete
    assert statuses(observed) == [429, 503, None, None] and outcome.usage.total == 44
    recorded = load(outcome.artifact_directory / "result.json")
    assert recorded["capacity"]["refusals"] == 1
    assert recorded["request_retries"] == {"limit": 1, "retried": 1}
    terminal = events(outcome.artifact_directory)[-1]
    assert (terminal["capacity_refusals"], terminal["retries"]) == (1, 1)
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_queue_time_the_real_stream_announces_is_credited_against_the_timeout(
        capture, tmp_path, installed):
    # Twelve seconds queued under an eight second timeout. The streamed session writes
    # each record before it waits, so the deadline has moved before it is reached.
    turns = [waiting(4), waiting(4), {"frames": READ}, waiting(4), {"frames": FINAL}]
    result, outcome, _ = http_review(capture, tmp_path, installed, turns, timeout=8,
                                     capacity=FIXED)
    assert result.status == "complete" and outcome.accounting_complete
    attempt = outcome.artifact_directory
    assert outcome.capacity == CapacityUsage(3, 12.0, None, 0.0, "result")
    assert load(attempt / "invocation.json")["timeout_seconds"] == 8
    assert [r["wait"] for r in queue_records(events(attempt))] == [4.0, 4.0, 4.0]
    assert outcome.usage.total == 44


def test_personas_of_one_review_share_one_adaptive_limit(capture, tmp_path, installed):
    document = configure(capture, personas=["senior-dev", "security"], review=400,
                         per=200).to_dict()
    document["models"]["default"] = {"provider": "openai", "model": "offline-model",
                                     "base_url": "https://offline.invalid/v1"}
    document["models"]["capacity"] = ADAPTIVE
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    runtime = offline_runtime(tmp_path, installed, default={"http_sse": {"turns": [
        REFUSED, {"frames": READ}, {"frames": FINAL}]}})
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    spec["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    assert result.status == "complete"
    outcomes = result.initial.outcomes
    assert [outcome.persona for outcome in outcomes] == ["senior-dev", "security"]
    shared = capture / "capacity"
    for outcome in outcomes:
        observed = observations(outcome, http_sse=True)
        assert statuses(observed) == [429, None, None]
        argv = load(outcome.artifact_directory / "invocation.json")["argv"]
        # Both processes name the review run's one directory.
        assert argv[argv.index("--capacity-shared-dir") + 1] == str(shared)
        assert outcome.capacity.source == "result" and outcome.capacity.refusals == 1
        assert 1 <= outcome.capacity.limit <= 4
        assert outcome.usage.total == 44 and outcome.accounting_complete
    # One route, so one limit: a single state directory holds both sessions' slots.
    route, = shared.iterdir()
    assert json.loads((route / "state.json").read_text())["limit"] < 4
    fanout = load(capture / "fanout.json")["result"]["capacity"]
    assert (fanout["sessions"], fanout["refusals"]) == (2, 2)
    # Nothing of the shared directory is inside a persona's read root.
    assert not shared.is_relative_to(capture / "review-inputs")
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_queueing_on_a_resumed_correction_turn_stays_cumulative_and_replays(
        tmp_path, installed, wheel_cli):
    result, run, attempt, replay, _ = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: fixed(p, end_line=1),
        capacity=", capacity: {mode: fixed, wait_seconds: 30, refusals: 5}",
        first_refusals=(0.5,), correction_refusals=(0.25, 0.25))
    envelope = json.loads(result.stdout)
    assert (envelope["verdict"], envelope["panel_status"]) == ("changes_requested", "complete")
    outcome = load(attempt / "result.json")
    assert outcome["citation_correction"]["outcome"] == "corrected"
    # One refusal in the first invocation and two in the resumed one, over one session.
    assert outcome["capacity"] == {"refusals": 3, "wait_seconds": 1.0, "limit": None,
                                   "slot_wait_seconds": 0.0, "source": "result"}
    first, second = events(attempt)[-1], events(attempt, "correction.stdout.jsonl")[-1]
    assert (first["capacity_refusals"], second["capacity_refusals"]) == (1, 3)
    assert load(attempt / "correction-session.json")["capacity_refusals"] == 3
    correction_argv = load(attempt / "citation-correction.json")["argv"]
    index = correction_argv.index("--capacity-queue")
    assert correction_argv[index:index + 5] == [
        "--capacity-queue", "--capacity-wait-seconds", "30", "--capacity-refusals", "5"]
    # Every completed reply is counted exactly once across both invocations.
    costs = [e["detail"]["total"] for name in ("stdout.jsonl", "correction.stdout.jsonl")
             for e in events(attempt, name) if e["type"] == "cost"]
    assert len(costs) == 4 and sum(costs) == outcome["usage"]["total"] == second["usage"]["total"]
    assert envelope["usage"]["total"] == outcome["usage"]["total"]
    assert outcome["accounting_complete"] is True
    assert replay.returncode == 0, replay.stderr.decode()
    replayed = json.loads(replay.stdout)
    assert replayed["saved_verdict"]["byte_identical"] is True and replayed["issues"] == []
