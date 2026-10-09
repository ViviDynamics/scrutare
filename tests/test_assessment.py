from dataclasses import replace

import pytest

from scrutare.config import ConfigError, parse_config
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.session_models import TokenUsage


def configuration(extra=""):
    return parse_config("models: {default: {model: test}}\n" + extra)


def test_assessment_requires_structured_evidence_and_discovery_capacity():
    with pytest.raises(ConfigError):
        configuration("findings: {assessment: {enabled: true, tokens: 100}}")
    config = configuration("context: {enabled: true}\nfindings: {evidence: v2, "
                           "assessment: {enabled: true, tokens: 100}}")
    assert config.findings.assessment.tokens == 100
    with pytest.raises(ConfigError):
        configuration("context: {enabled: true}\nbudgets: {review_max_tokens: 100}\n"
                      "findings: {evidence: v2, assessment: {enabled: true, tokens: 100}}")
    assert "findings" not in configuration().to_dict()


def test_discovery_cannot_spend_reserved_assessment_allocation():
    settings = replace(configuration().budgets, per_persona_tokens=100, review_max_tokens=180)
    ledger = ReviewBudgetLedger(("a", "b", "assessor"), settings,
                               reserved_allocations={"assessor": 80})
    a = ledger.admit("a", "a/one")
    b = ledger.admit("b", "b/one")
    assert (a.allocated_tokens, b.allocated_tokens) == (50, 50)
    ledger.settle(a, TokenUsage(input=50), True)
    ledger.settle(b, TokenUsage(input=50), True)
    assessor = ledger.admit("assessor", "assessor/one")
    assert assessor.allocated_tokens == 80
    ledger.settle(assessor, TokenUsage(input=80), True)
    assert ledger.usage.total == 180
    assert ledger.admit("a", "a/two") is None


@pytest.mark.parametrize("reservation", [{"missing": 10}, {"a": 0}, {"a": 100}, {"a": True}])
def test_invalid_reservations_refused(reservation):
    with pytest.raises(ValueError):
        ReviewBudgetLedger(("a", "b"), replace(configuration().budgets,
                          review_max_tokens=100), reserved_allocations=reservation)
