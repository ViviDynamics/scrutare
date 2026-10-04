"""Budget admission and accounting contracts with hand-derived token values."""

import importlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from scrutare.config import BudgetSettings
from scrutare.findings.models import Anchor, Finding


def models():
    return importlib.import_module("scrutare.engine.session_models")


def budgets():
    return importlib.import_module("scrutare.engine.budgets")


def ledger(names=("senior-dev", "security"), *, persona=100, review=200):
    return budgets().ReviewBudgetLedger(names, BudgetSettings(persona, review))


@pytest.mark.parametrize(
    ("review", "persona", "expected"),
    [(10, 100, [3, 3, 2, 2]), (2, 100, [1, 1, 0, 0]), (100, 3, [3, 3, 3, 3])],
)
def test_configured_order_gets_fair_fixed_allocations(review, persona, expected):
    value = ledger(("one", "two", "three", "four"), persona=persona, review=review)
    assert list(value.allocations.values()) == expected
    with pytest.raises(TypeError):
        value.allocations["one"] = 100


@pytest.mark.parametrize("names", [(), ("a", "a"), ("../a",), ("A",), ("a_b",), ("",)])
def test_ledger_rejects_empty_duplicate_and_unsafe_personas(names):
    with pytest.raises(ValueError):
        ledger(names)


@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.5])
@pytest.mark.parametrize("field", ["persona", "review"])
def test_ledger_revalidates_direct_budget_settings(bad, field):
    with pytest.raises(ValueError):
        ledger(**{field: bad})


def test_disjoint_usage_counters_and_serialization_are_immutable():
    usage = models().TokenUsage(3, 5, 7, 11)
    assert usage.total == 26
    assert usage.to_dict() == {
        "input": 3,
        "output": 5,
        "cache_read": 7,
        "cache_write": 11,
        "total": 26,
    }
    document = usage.to_dict()
    document["input"] = 900
    assert usage.input == 3
    with pytest.raises(FrozenInstanceError):
        usage.input = 9


@pytest.mark.parametrize("bad", [-1, True, False, 1.5, "1"])
@pytest.mark.parametrize("field", ["input", "output", "cache_read", "cache_write"])
def test_usage_rejects_invalid_direct_counters(field, bad):
    with pytest.raises(ValueError):
        models().TokenUsage(**{field: bad})


def test_active_reservations_prevent_double_allocation():
    value = ledger(persona=100, review=100)
    first = value.admit("senior-dev", "first")
    second = value.admit("security", "second")
    assert (first.allocated_tokens, second.allocated_tokens) == (50, 50)
    assert value.admit("senior-dev", "third") is None
    value.observe(first, models().TokenUsage(30))
    assert value.snapshot()["active_reservations"][0]["remaining_tokens"] == 20
    assert value.snapshot()["active_reservations"][1]["remaining_tokens"] == 50
    value.settle(first, models().TokenUsage(30), True)
    again = value.admit("senior-dev", "retry")
    assert again.allocated_tokens == 20
    assert again.limit_tokens == 20


def test_known_overshoot_reduces_later_grants_without_clipping_usage():
    value = ledger(persona=100, review=100)
    first = value.admit("senior-dev", "first")
    value.settle(first, models().TokenUsage(60), True)
    second = value.admit("security", "second")
    assert second.allocated_tokens == 40
    assert value.usage.total == 60
    assert value.admit("senior-dev", "retry") is None
    assert value.snapshot()["personas"][0]["overshoot_tokens"] == 10


def test_overshoot_with_outstanding_reservation_closes_new_work():
    value = ledger(("one", "two", "three"), persona=100, review=90)
    first = value.admit("one", "first")
    second = value.admit("two", "second")
    value.observe(first, models().TokenUsage(70))
    assert value.admit("three", "third") is None
    value.settle(second, models().TokenUsage(10), True)
    third = value.admit("three", "third")
    assert third.allocated_tokens == 10


def test_resume_charges_only_high_water_delta_and_retry_charges_fresh_usage():
    value = ledger(persona=100, review=200)
    initial = value.admit("senior-dev", "same")
    value.observe(initial, models().TokenUsage(20, 10))
    value.settle(initial, models().TokenUsage(20, 10), True)
    resumed = value.admit("senior-dev", "same", models().TokenUsage(20, 10))
    assert resumed.limit_tokens == 100
    assert resumed.allocated_tokens == 70
    value.settle(resumed, models().TokenUsage(30, 15), True)
    assert value.usage.total == 45
    assert value.snapshot()["sessions"][0]["usage"]["total"] == 45
    retry = value.admit("senior-dev", "fresh")
    assert retry.allocated_tokens == 55
    value.settle(retry, models().TokenUsage(12), True)
    assert value.usage.total == 57


def test_fresh_retry_keeps_persona_allowance_and_charges_30_plus_12():
    value = ledger(persona=40, review=80)
    first = value.admit("senior-dev", "one")
    value.settle(first, models().TokenUsage(30), True)
    retry = value.admit("senior-dev", "two")
    assert retry.allocated_tokens == 10
    value.settle(retry, models().TokenUsage(12), True)
    assert value.usage.total == 42
    assert value.admit("senior-dev", "three") is None
    assert value.snapshot()["personas"][0]["overshoot_tokens"] == 2


def test_resume_baseline_requires_exact_vector_and_fresh_keys_require_zero():
    value = ledger()
    first = value.admit("senior-dev", "same")
    value.settle(first, models().TokenUsage(20, 10), True)
    for key, baseline in [
        ("same", models().TokenUsage(10, 20)),
        ("same", models().TokenUsage()),
        ("new", models().TokenUsage(1)),
    ]:
        with pytest.raises(ValueError):
            value.admit("senior-dev", key, baseline)
    assert value.usage.total == 30


def test_session_key_cannot_be_reassigned_to_another_persona():
    value = ledger()
    first = value.admit("senior-dev", "same")
    value.settle(first, models().TokenUsage(10), True)
    with pytest.raises(ValueError):
        value.admit("security", "same", models().TokenUsage(10))


@pytest.mark.parametrize("update", [(19, 12, 3, 4), (20, 9, 8, 4), (20, 10, 2, 8), (20, 10, 3, 3)])
def test_component_decreases_are_rejected_even_if_total_increases(update):
    value = ledger()
    lease = value.admit("senior-dev", "same")
    value.observe(lease, models().TokenUsage(20, 10, 3, 4))
    with pytest.raises(ValueError):
        value.observe(lease, models().TokenUsage(*update))
    with pytest.raises(ValueError):
        value.settle(lease, models().TokenUsage(*update), True)
    assert value.usage.total == 37


def test_component_monotone_updates_are_charged_once():
    value = ledger()
    lease = value.admit("senior-dev", "same")
    value.observe(lease, models().TokenUsage(20, 10, 3, 4))
    value.observe(lease, models().TokenUsage(20, 10, 3, 4))
    value.settle(lease, models().TokenUsage(21, 12, 6, 8), True)
    assert value.usage.to_dict() == {
        "input": 21,
        "output": 12,
        "cache_read": 6,
        "cache_write": 8,
        "total": 47,
    }


def test_forged_stale_and_duplicate_leases_cannot_mutate_accounting():
    value = ledger()
    lease = value.admit("senior-dev", "same")
    forged = replace(lease)
    altered = replace(lease, session_key="other")
    for bad in (forged, altered):
        with pytest.raises(ValueError):
            value.observe(bad, models().TokenUsage(5))
        with pytest.raises(ValueError):
            value.settle(bad, models().TokenUsage(5), True)
    value.settle(lease, models().TokenUsage(5), True)
    for operation in (
        lambda: value.observe(lease, models().TokenUsage(10)),
        lambda: value.settle(lease, models().TokenUsage(10), True),
    ):
        with pytest.raises(ValueError):
            operation()
    assert value.usage.total == 5


def test_unknown_accounting_seals_admission_and_keeps_observed_lower_bound():
    value = ledger()
    first = value.admit("senior-dev", "first")
    second = value.admit("security", "second")
    value.observe(first, models().TokenUsage(20, 10))
    value.settle(first, models().TokenUsage(20, 10), False)
    assert value.accounting_complete is False
    assert value.exhausted is False
    assert value.admit("senior-dev", "retry") is None
    value.settle(second, models().TokenUsage(5), True)
    assert value.usage.total == 35
    assert value.accounting_complete is False


def test_zero_quotas_and_exact_cap_close_admission_without_persona_borrowing():
    value = ledger(("one", "two", "three", "four"), review=2)
    assert value.admit("three", "third") is None
    first = value.admit("one", "first")
    value.settle(first, models().TokenUsage(1), True)
    assert value.admit("one", "retry") is None
    assert value.exhausted is False
    second = value.admit("two", "second")
    value.settle(second, models().TokenUsage(1), True)
    assert value.exhausted is True
    assert value.admit("two", "retry") is None


def test_smaller_persona_quotas_do_not_claim_review_exhaustion():
    value = ledger(persona=2, review=100)
    for name in ("senior-dev", "security"):
        lease = value.admit(name, name)
        value.settle(lease, models().TokenUsage(2), True)
    assert value.exhausted is False
    assert value.admit("security", "retry") is None


def test_snapshot_is_deterministic_fresh_and_reports_actual_overshoot():
    value = ledger(persona=10, review=20)
    lease = value.admit("security", "s")
    value.settle(lease, models().TokenUsage(7, 8, 4, 5), True)
    first = value.snapshot()
    assert first == value.snapshot()
    assert json.loads(json.dumps(first)) == first
    assert first["configured"] == {"review_max_tokens": 20, "per_persona_tokens": 10}
    assert first["usage"]["total"] == 24
    assert first["overshoot_tokens"] == 4
    assert first["personas"][1]["overshoot_tokens"] == 14
    assert first["sessions"][0]["usage"]["cache_read"] == 4
    first["personas"][1]["usage"]["input"] = 900
    first["allocations"]["security"] = 900
    assert value.snapshot()["personas"][1]["usage"]["input"] == 7
    assert value.allocations["security"] == 10


def outcome(**overrides):
    values = dict(
        persona="security",
        status="complete",
        reason="done",
        findings=(),
        output_available=True,
        usage=models().TokenUsage(10),
        accounting_complete=True,
        allocated_tokens=10,
        overshoot_tokens=0,
        session_id="session",
        artifact_directory=Path("artifacts/security"),
        invocation_limit=10,
    )
    values.update(overrides)
    return models().SessionOutcome(**values)


def test_complete_exact_cap_candidate_output_and_fanout_serialize_fresh():
    finding = Finding(Anchor("file.py", 4), "security", "problem", "reason", "security")
    complete = outcome(findings=(finding,))
    result = models().FanOutResult((complete,), False, False, models().TokenUsage(10), True, 0)
    document = result.to_dict()
    assert document["outcomes"][0]["status"] == "complete"
    assert document["outcomes"][0]["findings"] == [
        {
            "file": "file.py",
            "line": 4,
            "side": "RIGHT",
            "category": "security",
            "problem": "problem",
            "reason": "reason",
            "persona": "security",
        }
    ]
    assert document["outcomes"][0]["artifact_directory"] == "artifacts/security"
    assert "verdict" not in document
    document["outcomes"][0]["findings"].clear()
    assert len(result.to_dict()["outcomes"][0]["findings"]) == 1
    json.dumps(result.to_dict())


@pytest.mark.parametrize(
    "changes",
    [
        {"persona": "../security"},
        {"status": "approved"},
        {"reason": ""},
        {"findings": []},
        {"findings": ("bad",)},
        {"output_available": 1},
        {"usage": {}},
        {"accounting_complete": 1},
        {"allocated_tokens": True},
        {"allocated_tokens": -1},
        {"overshoot_tokens": 1},
        {"session_id": ""},
        {"artifact_directory": "artifacts"},
        {"invocation_limit": True},
        {"invocation_limit": -1},
        {"nare_status": ""},
        {"stop_reason": ""},
        {"exit_code": True},
    ],
)
def test_outcome_rejects_invalid_direct_values(changes):
    with pytest.raises(ValueError):
        outcome(**changes)


def test_outcome_rejects_finding_attribution_mismatch():
    finding = Finding(Anchor("file.py", 4), "security", "problem", "reason", "other")
    with pytest.raises(ValueError):
        outcome(findings=(finding,))


@pytest.mark.parametrize(
    "changes",
    [
        {"executable": "nare"},
        {"max_turns": True},
        {"max_turns": 0},
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
    ],
)
def test_runtime_rejects_invalid_direct_values(changes):
    values = {"executable": Path("nare")}
    values.update(changes)
    with pytest.raises(ValueError):
        models().NareRuntime(**values)


@pytest.mark.parametrize("version,contract", [("", 1), ("v1", True), ("v1", 0)])
def test_capability_rejects_invalid_direct_values(version, contract):
    with pytest.raises(ValueError):
        models().NareCapability(version, contract)


@pytest.mark.parametrize(
    "changes",
    [
        {"persona": "../one"},
        {"session_key": ""},
        {"baseline": {}},
        {"limit_tokens": True},
        {"limit_tokens": 0},
        {"allocated_tokens": True},
        {"allocated_tokens": 0},
        {"allocated_tokens": 11},
    ],
)
def test_lease_rejects_invalid_direct_values(changes):
    values = dict(
        persona="one",
        session_key="s",
        baseline=models().TokenUsage(),
        limit_tokens=10,
        allocated_tokens=10,
    )
    values.update(changes)
    with pytest.raises(ValueError):
        budgets().BudgetLease(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"outcomes": []},
        {"outcomes": ("bad",)},
        {"failed": 1},
        {"partial": 1},
        {"usage": {}},
        {"review_exhausted": 1},
        {"review_overshoot_tokens": True},
        {"review_overshoot_tokens": -1},
    ],
)
def test_fanout_rejects_invalid_direct_values(changes):
    values = dict(
        outcomes=(),
        failed=False,
        partial=False,
        usage=models().TokenUsage(),
        review_exhausted=False,
        review_overshoot_tokens=0,
    )
    values.update(changes)
    with pytest.raises(ValueError):
        models().FanOutResult(**values)


def test_public_operations_reject_invalid_values_without_mutating():
    value = ledger()
    with pytest.raises(ValueError):
        value.admit("missing", "s")
    with pytest.raises(ValueError):
        value.admit("security", "")
    with pytest.raises(ValueError):
        value.admit("security", "s", {})
    lease = value.admit("security", "s")
    with pytest.raises(ValueError):
        value.observe(lease, {})
    with pytest.raises(ValueError):
        value.settle(lease, models().TokenUsage(), 1)
    assert value.usage.total == 0


def test_live_uncertainty_seals_new_work_without_invalidating_admitted_accounting():
    value = ledger(("one", "two", "three"), persona=100, review=300)
    first = value.admit("one", "first")
    second = value.admit("two", "second")
    value.observe(first, models().TokenUsage(15, 5))
    before = value.snapshot()
    value.mark_uncertain(first)
    assert value.admit("three", "later") is None
    assert not value.accounting_complete and not value.exhausted
    assert value.usage == models().TokenUsage(15, 5)
    assert value.snapshot()["active_reservations"] == before["active_reservations"]
    value.mark_uncertain(first)
    value.observe(first, models().TokenUsage(20, 5))
    value.observe(second, models().TokenUsage(cache_read=10))
    value.settle(first, models().TokenUsage(20, 5), True)
    value.settle(second, models().TokenUsage(cache_read=10), True)
    assert value.usage == models().TokenUsage(20, 5, 10)
    assert not value.accounting_complete
    assert value.admit("three", "still-denied") is None
    assert value.snapshot()["active_reservations"] == []


def test_live_uncertainty_rejects_forged_foreign_and_stale_leases_without_sealing():
    value = ledger()
    active = value.admit("security", "active")
    foreign = ledger().admit("security", "active")
    for bad in (replace(active), replace(active, session_key="other"), foreign, None):
        with pytest.raises(ValueError):
            value.mark_uncertain(bad)
        assert value.accounting_complete and value.usage == models().TokenUsage()
    value.settle(active, models().TokenUsage(5), True)
    with pytest.raises(ValueError):
        value.mark_uncertain(active)
    assert value.accounting_complete and value.usage == models().TokenUsage(5)
    assert value.admit("senior-dev", "valid-after-rejections") is not None
