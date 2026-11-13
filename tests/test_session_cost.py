"""Dollar cost reaches session and review records with its source; unknown is never zero."""

import asyncio
import json
from pathlib import Path

import pytest
from test_nare_session import RUN_BODY, executable, module, setup
from test_review_inputs import capture as capture

from scrutare.config import BudgetSettings, ModelRail
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.session_models import (
    NareCapability,
    NareRuntime,
    SessionCost,
    TokenUsage,
)

UNKNOWN = {"usd": None, "source": "unknown"}


def test_a_cost_is_a_reported_amount_or_unknown_never_a_guess():
    assert SessionCost().to_dict() == UNKNOWN
    assert SessionCost(0.25, "provider_reported").to_dict() == {
        "usd": 0.25, "source": "provider_reported"}
    assert SessionCost(0.0, "provider_reported").to_dict()["usd"] == 0.0
    for usd, source in ((0.0, "unknown"), (None, "provider_reported"), (-1.0, "provider_reported"),
                        (float("nan"), "provider_reported"), (True, "provider_reported"),
                        (1.0, "estimated"), ("1", "provider_reported")):
        with pytest.raises(ValueError):
            SessionCost(usd, source)


def test_costs_add_only_while_every_part_is_reported():
    reported = SessionCost(0.25, "provider_reported")
    assert reported + SessionCost(0.5, "provider_reported") == SessionCost(
        0.75, "provider_reported")
    assert reported + SessionCost() == SessionCost()
    assert SessionCost() + reported == SessionCost()


COST_EVENT = ('print(json.dumps({"type": "cost", "text": "", "detail": USAGE, '
              '"timestamp": "now"}), flush=True)\n')


def costed_executable(tmp_path, *, cost="0.125", estimated="False", status="'done'",
                      stop="'end_turn'", exit_code="0", key=True, used=(4, 2, 3, 1),
                      event=True, extra=""):
    counters = ", ".join(f'"{name}": {count}' for name, count in zip(
        ("input", "output", "cache_read", "cache_write"), used))
    usage = ("USAGE = {" + counters + f', "cost": {cost}'
             + (f', "estimated": {estimated}' if key else "") + "}")
    assert COST_EVENT in RUN_BODY
    body = (RUN_BODY.replace(
        'USAGE = {"input": INPUT, "output": 2, "cache_read": 3, "cache_write": 1, "cost": None}',
        usage).replace(COST_EVENT, COST_EVENT if event else "")
        .replace("STATUS", status).replace("STOP", stop)
        .replace("OUTPUT", "{'findings': []}").replace("EXTRA", extra).replace("EXIT", exit_code)
        .replace('"used_usd": None', '"used_usd": USAGE["cost"]'))
    assert usage in body
    return executable(tmp_path, body=body)


def run(capture, tmp_path, **script):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, ModelRail("openai", None, "m"), lease, ledger=ledger,
        artifact_directory=attempt,
        runtime=NareRuntime(costed_executable(tmp_path, **script), 7, 5),
        capability=NareCapability("2026.10.0", 1)))
    return outcome, ledger, json.loads((attempt / "result.json").read_bytes())


def test_a_reported_cost_reaches_the_outcome_the_result_and_the_ledger(capture, tmp_path):
    outcome, ledger, result = run(capture, tmp_path)
    assert outcome.status == "complete"
    assert outcome.cost == SessionCost(0.125, "provider_reported")
    assert result["cost"] == {"usd": 0.125, "source": "provider_reported"}
    assert outcome.to_dict()["cost"] == result["cost"]
    snapshot = ledger.snapshot()
    assert snapshot["cost"] == result["cost"] == ledger.cost.to_dict()
    assert snapshot["sessions"][0]["cost"] == result["cost"]


@pytest.mark.parametrize("script", [
    {"cost": "None"},                        # the backend reported no cost
    {"cost": "0.125", "estimated": "True"},  # nare calculated it from prices
    {"cost": "0.125", "key": False},         # an older nare that does not say which
    {"cost": "0", "estimated": "True"},
])
def test_an_unreported_or_calculated_cost_is_unknown_and_never_zero(capture, tmp_path, script):
    outcome, ledger, result = run(capture, tmp_path, **script)
    assert outcome.status == "complete" and outcome.usage == TokenUsage(4, 2, 3, 1)
    assert outcome.cost == SessionCost()
    assert result["cost"] == UNKNOWN == ledger.snapshot()["cost"]


def test_a_reported_zero_is_a_known_zero(capture, tmp_path):
    outcome, ledger, result = run(capture, tmp_path, cost="0")
    assert outcome.cost == SessionCost(0, "provider_reported")
    assert result["cost"] == {"usd": 0.0, "source": "provider_reported"}
    assert ledger.snapshot()["cost"] == result["cost"]


def test_a_session_whose_request_failed_has_an_unknown_cost(capture, tmp_path):
    # The failing request may have been billed: what the stream reported is a lower bound.
    outcome, ledger, result = run(capture, tmp_path, status="'error'", stop="None", exit_code="1")
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    assert outcome.usage.total == 10 and outcome.accounting_complete is False
    assert outcome.cost == SessionCost()
    assert result["cost"] == UNKNOWN == ledger.snapshot()["cost"]


def test_a_budget_stop_keeps_its_reported_cost(capture, tmp_path):
    # A budget stop is a complete account: every request that ran reported its cost.
    outcome, ledger, result = run(capture, tmp_path, status="'error'", stop="'budget'",
                                  exit_code="1")
    assert outcome.accounting_complete is True
    assert result["cost"] == {"usd": 0.125, "source": "provider_reported"}
    assert ledger.snapshot()["cost"] == result["cost"]


def test_a_session_with_incomplete_accounting_has_an_unknown_cost(capture, tmp_path):
    # The stream reported 0.125 and then became unreadable: that amount is a lower bound.
    outcome, ledger, result = run(capture, tmp_path, extra='print("not an event", flush=True)')
    assert outcome.status == "failed" and outcome.accounting_complete is False
    assert outcome.usage.total == 10
    assert outcome.cost == SessionCost()
    assert result["cost"] == UNKNOWN == ledger.snapshot()["cost"]


def test_a_session_that_completed_no_request_has_an_unknown_cost(capture, tmp_path):
    # nare's empty usage carries cost 0.0 and estimated false: nothing was reported for it.
    outcome, ledger, result = run(capture, tmp_path, cost="0.0", used=(0, 0, 0, 0), event=False,
                                  status="'error'", stop="None", exit_code="1")
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    assert outcome.usage.total == 0 and outcome.accounting_complete is False
    assert outcome.cost == SessionCost()
    assert result["cost"] == UNKNOWN == ledger.snapshot()["cost"]


def ledger_of(*names):
    return ReviewBudgetLedger(names, BudgetSettings(100, 1000))


def test_a_review_cost_is_the_sum_of_its_sessions_only_when_all_are_reported():
    ledger = ledger_of("a", "b", "c")
    for name, usage, cost in (("a", TokenUsage(5), SessionCost(0.25, "provider_reported")),
                              ("b", TokenUsage(7), SessionCost(0.5, "provider_reported"))):
        ledger.settle(ledger.admit(name, name + "/attempt-0001"), usage, True, cost)
    assert ledger.cost == SessionCost(0.75, "provider_reported")
    snapshot = ledger.snapshot()
    assert snapshot["cost"] == {"usd": 0.75, "source": "provider_reported"}
    assert [session["cost"]["usd"] for session in snapshot["sessions"]] == [0.25, 0.5]
    # One session with usage and no reported cost makes the whole review unknown.
    ledger.settle(ledger.admit("c", "c/attempt-0001"), TokenUsage(3), True)
    assert ledger.cost == SessionCost() and ledger.snapshot()["cost"] == UNKNOWN


def test_a_session_that_used_nothing_does_not_hide_the_reported_total():
    ledger = ledger_of("a", "b")
    ledger.settle(ledger.admit("a", "a/attempt-0001"), TokenUsage(5), True,
                  SessionCost(0.25, "provider_reported"))
    ledger.settle(ledger.admit("b", "b/attempt-0001"), TokenUsage(), True)
    assert ledger.cost == SessionCost(0.25, "provider_reported")


def test_a_review_without_any_usage_has_no_known_cost():
    assert ledger_of("a").cost == SessionCost()
    assert ledger_of("a").snapshot()["cost"] == UNKNOWN


def test_a_later_attempt_of_one_session_replaces_its_cost():
    ledger = ledger_of("a")
    first = ledger.admit("a", "a/session")
    ledger.settle(first, TokenUsage(5), True, SessionCost(0.25, "provider_reported"))
    # A resumed session reports its cumulative cost, as it reports cumulative usage.
    second = ledger.admit("a", "a/session", TokenUsage(5))
    ledger.settle(second, TokenUsage(9), True, SessionCost(0.4, "provider_reported"))
    assert ledger.cost == SessionCost(0.4, "provider_reported")


# One streamed openai-rail session as the installed nare 2026.11.100 console wrote it behind
# a proxy that reports its charge on the final usage chunk (see the fixture's README).
STREAMED = Path(__file__).parent / "fixtures/nare-streamed-cost"
STREAMED_ROOT = Path("/fixture/review-inputs")
REPORTED = SessionCost(0.375, "provider_reported")


def streamed():
    events = [json.loads(line) for line in
              (STREAMED / "stdout.jsonl").read_text().splitlines()]
    return events, json.loads((STREAMED / "session.json").read_text())


def decode_streamed(events, saved):
    from scrutare.engine.session_output import decode_session

    stdout = "".join(json.dumps(event) + "\n" for event in events).encode()
    return decode_session(stdout, json.dumps(saved).encode(), persona="senior-dev",
                          exit_code=0, expected_limit=200, expected_root=STREAMED_ROOT)


def live_cost(events):
    """The cost a session sums from its stream as it runs, before any result."""
    from scrutare.engine.session_output import decode_live_event

    total = None
    for event in events:
        cost = decode_live_event(json.dumps(event).encode())[2]
        if cost is not None:
            total = cost if total is None else total + cost
    return total


def test_the_streamed_fixture_is_the_shape_the_pinned_console_writes():
    events, saved = streamed()
    costs = [event["detail"] for event in events if event["type"] == "cost"]
    assert [(c["cost"], c["estimated"], c["total"]) for c in costs] == [
        (0.125, False, 15), (0.25, False, 29)]
    result = events[-1]
    assert (result["type"], result["nare"], result["contract"]) == (
        "result", "2026.11.100", 3)
    assert result["usage"] == {"input": 32, "output": 12, "cache_read": 0, "cache_write": 0,
                               "cost": 0.375, "estimated": False, "total": 44}
    assert result["budget"]["used_usd"] == 0.375
    assert saved["usage"] == {key: value for key, value in result["usage"].items()
                              if key != "total"}


def test_a_streamed_reported_cost_is_decoded_as_provider_reported():
    events, saved = streamed()
    # The bytes exactly as nare wrote them, then the same documents re-encoded.
    from scrutare.engine.session_output import decode_session
    verbatim = decode_session(
        (STREAMED / "stdout.jsonl").read_bytes(), (STREAMED / "session.json").read_bytes(),
        persona="senior-dev", exit_code=0, expected_limit=200, expected_root=STREAMED_ROOT)
    decoded = decode_streamed(events, saved)
    assert verbatim == decoded
    assert decoded.cost == REPORTED == live_cost(events)
    assert decoded.usage == TokenUsage(32, 12, 0, 0) and decoded.status == "done"
    assert len(decoded.findings) == 1


def estimate(usage):
    usage["estimated"] = True


def unreported(usage):
    usage["cost"] = None
    usage["estimated"] = False


@pytest.mark.parametrize(("turn", "change"), [(0, estimate), (1, estimate), (1, unreported)])
def test_a_streamed_session_with_a_calculated_or_missing_turn_cost_is_unknown(turn, change):
    events, saved = streamed()
    costs = [event for event in events if event["type"] == "cost"]
    change(costs[turn]["detail"])
    result = events[-1]
    if costs[turn]["detail"]["cost"] is None:
        # nare has no total once one turn has none.
        for usage in (result["usage"], saved["usage"]):
            usage["cost"] = None
        result["budget"]["used_usd"] = None
    else:
        for usage in (result["usage"], saved["usage"]):
            usage["estimated"] = True
    decoded = decode_streamed(events, saved)
    assert decoded.cost == SessionCost() == live_cost(events)
    assert decoded.usage == TokenUsage(32, 12, 0, 0)


def test_a_streamed_result_without_the_estimated_flag_is_unknown():
    # An older nare does not say whether it calculated the figure.
    events, saved = streamed()
    for usage in (events[-1]["usage"], saved["usage"]):
        del usage["estimated"]
    assert decode_streamed(events, saved).cost == SessionCost()


@pytest.mark.parametrize("change", [
    lambda events, saved: events[-1]["usage"].update(cost=0.5),
    lambda events, saved: saved["usage"].update(cost=0.5),
    lambda events, saved: events[-1]["budget"].update(used_usd=0.5),
    lambda events, saved: [e for e in events if e["type"] == "cost"][0]["detail"].update(
        cost=0.25),
    lambda events, saved: [e for e in events if e["type"] == "cost"][1]["detail"].update(
        cost=None),
    lambda events, saved: events[-1]["usage"].update(cost=None),
    lambda events, saved: events[-1]["usage"].update(cost=-0.375),
    lambda events, saved: events[-1]["usage"].update(cost="0.375"),
])
def test_streamed_costs_that_do_not_reconcile_are_a_protocol_failure(change):
    from scrutare.engine.session_output import SessionProtocolError

    events, saved = streamed()
    change(events, saved)
    with pytest.raises(SessionProtocolError):
        decode_streamed(events, saved)
