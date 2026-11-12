"""Dollar cost reaches session and review records with its source; unknown is never zero."""

import asyncio
import json

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
