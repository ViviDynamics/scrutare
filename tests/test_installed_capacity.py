"""The external nare console queues capacity refusals only when Scrutare asks it to."""

import asyncio
import json

import pytest
from test_fanout import configure
from test_installed_structured_output import http_scenario
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import load, observations, offline_runtime
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

FIXED = {"mode": "fixed", "wait_seconds": 30, "refusals": 5}
ADAPTIVE = {**FIXED, "mode": "adaptive", "ceiling": 4, "floor": 1, "quiet_seconds": 60}


def review(capture, tmp_path, installed, capacity, refusals, **rail):
    document = configure(capture, personas=["senior-dev"], review=200, per=200).to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model",
        "base_url": "https://offline.invalid/v1", "structured_output": "json_schema", **rail}
    if capacity is not None:
        document["models"]["capacity"] = capacity
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    scenario = http_scenario()
    scenario["http_sse"]["capacity_refusals"] = refusals
    runtime = offline_runtime(tmp_path, installed, default=scenario)
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    spec["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    outcome = result.initial.outcomes[0]
    return result, outcome, observations(outcome, http_sse=True)["calls"]


@pytest.mark.parametrize("capacity", [FIXED, ADAPTIVE])
def test_refusals_are_waited_out_resent_unchanged_and_accounted_apart(
        capture, tmp_path, installed, capacity):
    result, outcome, calls = review(capture, tmp_path, installed, capacity, 2)
    assert result.status == "complete", (outcome.reason, calls)
    assert [call.get("refused", False) for call in calls] == [True, True, False, False, False]
    # A queued refusal is resent byte for byte: it is admission, not a second attempt.
    assert calls[0]["payload"] == calls[1]["payload"] == calls[2]["payload"]
    assert outcome.capacity is not None and outcome.capacity.source == "result"
    assert outcome.capacity.refusals == 2
    # Only the three answered completions are usage; the refusals cost nothing.
    assert outcome.usage.input == 12 + 20 + 30
    result_record = load(outcome.artifact_directory / "result.json")
    assert result_record["capacity"]["refusals"] == 2
    fanout = load(capture / "fanout.json")
    assert fanout["result"]["capacity"]["refusals"] == 2
    assert fanout["ledger"]["usage"] == result_record["usage"]
    if capacity["mode"] == "adaptive":
        assert outcome.capacity.limit is not None and 1 <= outcome.capacity.limit <= 4
        assert (capture / "capacity").is_dir()
    else:
        assert outcome.capacity.limit is None
        assert not (capture / "capacity").exists()
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_off_records_nothing_and_a_refusal_fails_the_session(capture, tmp_path, installed):
    result, outcome, calls = review(capture, tmp_path, installed, None, 0)
    assert result.status == "complete"
    invocation = load(outcome.artifact_directory / "invocation.json")
    assert not [item for item in invocation["argv"] if "capacity" in item]
    assert "capacity" not in load(outcome.artifact_directory / "result.json")
    assert "capacity" not in load(capture / "fanout.json")["result"]
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_off_refusal_is_an_ordinary_provider_failure(capture, tmp_path, installed):
    result, outcome, calls = review(capture, tmp_path, installed, None, 1)
    assert outcome.status == "failed" and outcome.reason == "provider"
    # Request retries stay 0: the refused request is never resent.
    assert [call.get("refused", False) for call in calls] == [True]
    assert outcome.capacity is None


def test_refusals_past_the_configured_limit_fall_back_to_failure(capture, tmp_path, installed):
    result, outcome, calls = review(capture, tmp_path, installed, {**FIXED, "refusals": 1}, 2)
    assert outcome.status == "failed" and outcome.reason == "provider"
    assert [call.get("refused", False) for call in calls] == [True, True]
    assert outcome.capacity is not None and outcome.capacity.refusals == 1


def test_a_refusal_past_the_queue_falls_to_opted_in_request_retries(
        capture, tmp_path, installed):
    # One refusal is queued; the second is past the queue's limit, so the retry policy takes
    # it as a transient failure and resends it once. Each is counted in its own record.
    result, outcome, calls = review(capture, tmp_path, installed, {**FIXED, "refusals": 1}, 2,
                                    request_retries=1, request_retry_seconds=30)
    assert result.status == "complete", (outcome.reason, calls)
    assert [call.get("refused", False) for call in calls] == [True, True, False, False, False]
    assert calls[0]["payload"] == calls[1]["payload"] == calls[2]["payload"]
    assert outcome.capacity is not None and outcome.capacity.refusals == 1
    assert outcome.usage.input == 12 + 20 + 30
    result_record = load(outcome.artifact_directory / "result.json")
    assert result_record["capacity"]["refusals"] == 1
    assert result_record["request_retries"] == {"limit": 1, "retried": 1}
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0
