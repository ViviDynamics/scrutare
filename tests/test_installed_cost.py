"""Installed nare proof: reported dollars reach session and review records, never guessed."""

import asyncio
import json
from pathlib import Path

from test_fanout import configure
from test_installed_request_retries import FINAL, READ, http_review
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import load, offline_runtime, single, text, tool
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.review import ReviewRunResult
from scrutare.engine.session_models import SessionCost, TokenUsage
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

UNKNOWN = {"usd": None, "source": "unknown"}


def test_reported_dollars_are_summed_into_the_session_record(capture, tmp_path, installed):
    scenario = {"replies": [dict(tool(), cost=0.125), dict(text(), cost=0.25)]}
    outcome, ledger, _ = single(capture, tmp_path, installed, scenario)
    assert outcome.status == "complete"
    # nare says the backend reported both figures and calculated none.
    terminal = json.loads((outcome.artifact_directory / "stdout.jsonl").read_text()
                          .splitlines()[-1])
    assert (terminal["usage"]["cost"], terminal["usage"]["estimated"]) == (0.375, False)
    assert outcome.cost == SessionCost(0.375, "provider_reported") == ledger.cost
    assert load(outcome.artifact_directory / "result.json")["cost"] == {
        "usd": 0.375, "source": "provider_reported"}


def test_a_reply_without_a_reported_cost_makes_the_session_unknown(
        capture, tmp_path, installed):
    scenario = {"replies": [dict(tool(), cost=0.125), text()]}
    outcome, ledger, _ = single(capture, tmp_path, installed, scenario)
    assert outcome.status == "complete" and outcome.usage.total > 0
    assert outcome.cost == SessionCost() == ledger.cost
    assert load(outcome.artifact_directory / "result.json")["cost"] == UNKNOWN


def test_a_review_records_the_sum_of_its_sessions_and_replays(capture, tmp_path, installed):
    config = configure(capture, personas=["senior-dev", "security"], review=400, per=200)
    runtime = offline_runtime(tmp_path, installed, default={
        "replies": [dict(tool(), cost=0.125), dict(text(), cost=0.25)]})
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    assert result.status == "complete"
    assert [outcome.cost.usd for outcome in result.initial.outcomes] == [0.375, 0.375]
    assert result.cost == SessionCost(0.75, "provider_reported")
    reported = {"usd": 0.75, "source": "provider_reported"}
    for name in ("fanout.json", "panel.json"):
        ledger = load(capture / name)["ledger"]
        assert ledger["cost"] == reported
        assert [session["cost"]["usd"] for session in ledger["sessions"]] == [0.375, 0.375]
    assert [outcome["cost"]["usd"] for outcome in
            load(capture / "fanout.json")["result"]["outcomes"]] == [0.375, 0.375]
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_a_streamed_session_with_no_cost_and_no_prices_is_unknown(
        capture, tmp_path, installed):
    # The streamed replies report no cost and the gateway lists no prices.
    result, outcome, _ = http_review(
        capture, tmp_path, installed, [{"frames": READ}, {"frames": FINAL}])
    assert result.status == "complete"
    terminal = json.loads((outcome.artifact_directory / "stdout.jsonl").read_text()
                          .splitlines()[-1])
    assert terminal["usage"]["cost"] is None
    assert outcome.cost == SessionCost() and result.cost == SessionCost()


def test_a_calculated_estimate_stays_unknown_through_the_real_transport(
        capture, tmp_path, installed):
    # The gateway lists prices and the streamed replies report no cost: nare estimates one.
    document = configure(capture, personas=["senior-dev"], review=200, per=200).to_dict()
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model",
        "base_url": "https://offline.invalid/v1"}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    runtime = offline_runtime(tmp_path, installed, default={"http_sse": {
        "turns": [{"frames": READ}, {"frames": FINAL}],
        "model_info": {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}}})
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    spec["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    outcome = result.initial.outcomes[0]
    assert result.status == "complete"
    terminal = json.loads((outcome.artifact_directory / "stdout.jsonl").read_text()
                          .splitlines()[-1])
    # nare has a figure and marks it as its own calculation.
    assert terminal["usage"]["estimated"] is True and terminal["usage"]["cost"] > 0
    assert outcome.cost == SessionCost() == result.cost
    assert load(outcome.artifact_directory / "result.json")["cost"] == UNKNOWN
    assert load(capture / "panel.json")["ledger"]["cost"] == UNKNOWN


def test_the_review_receipt_carries_the_cost_and_its_source():
    fields = (Path("/run"), "abc123", "approve", "no_blocking_findings", "complete",
              TokenUsage(3, 2), True, None)
    assert ReviewRunResult(*fields).to_dict()["cost"] == UNKNOWN
    receipt = ReviewRunResult(*fields, cost=SessionCost(0.5, "provider_reported")).to_dict()
    assert receipt["cost"] == {"usd": 0.5, "source": "provider_reported"}
    assert receipt["usage"]["total"] == 5
