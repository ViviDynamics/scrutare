"""Opt-in phase allowances and last-complete-stage fallback on a measured overrun (#74)."""

import json
from dataclasses import replace

import pytest
from test_assessment import configuration
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, parse_config

FOCUSED = ("strategy: debate\ncontext: {enabled: true}\nfindings: {evidence: v2, "
           "assessment: {enabled: true, tokens: 100%s}, discussion: {enabled: true%s}}")


def focused(assessment="", discussion="", extra=""):
    return configuration(extra + FOCUSED % (assessment, discussion))


def test_new_phase_keys_default_off_and_stay_out_of_canonical_config():
    config = focused()
    assert config.findings.assessment.overrun == "void"
    assert config.findings.discussion.overrun == "void"
    assert config.findings.discussion.tokens is None
    assert config.to_dict()["findings"] == {
        "evidence": "v2", "assessment": {"enabled": True, "tokens": 100},
        "discussion": {"enabled": True}}


def test_phase_keys_round_trip_when_set():
    config = focused(", overrun: fallback", ", tokens: 300, overrun: fallback")
    assert config.findings.assessment.overrun == "fallback"
    assert config.findings.discussion.overrun == "fallback"
    assert config.findings.discussion.tokens == 300
    data = config.to_dict()["findings"]
    assert data["assessment"] == {"enabled": True, "tokens": 100, "overrun": "fallback"}
    assert data["discussion"] == {"enabled": True, "tokens": 300, "overrun": "fallback"}
    assert parse_config(json.dumps(config.to_dict())) == config


@pytest.mark.parametrize("text", [
    FOCUSED % (", overrun: drop", ""),
    FOCUSED % ("", ", overrun: true"),
    FOCUSED % ("", ", tokens: 0"),
    FOCUSED % ("", ", tokens: true"),
    FOCUSED % ("", ", tokens: 2.5"),
    FOCUSED % ("", ", unknown: 1"),
    # Assessor and discussion allowances together must leave discovery capacity.
    "budgets: {review_max_tokens: 400}\n" + FOCUSED % ("", ", tokens: 300"),
    # A phase policy needs its phase enabled.
    "context: {enabled: true}\nfindings: {evidence: v2, assessment: {enabled: false, "
    "overrun: fallback}}",
])
def test_invalid_phase_settings_refused(text):
    with pytest.raises(ConfigError):
        configuration(text)


def test_assessment_overrun_policy_needs_no_discussion():
    config = configuration("context: {enabled: true}\nfindings: {evidence: v2, "
                           "assessment: {enabled: true, tokens: 100, overrun: fallback}}")
    assert config.findings.assessment.overrun == "fallback"
    assert replace(config.findings.discussion) == config.findings.discussion


def degradation(phase="assessment", **fields):
    from scrutare.findings.verdict import Degradation
    values = {"phase": phase, "round": None if phase == "assessment" else 1,
              "allowance_tokens": 20000 if phase == "assessment" else None,
              "grant_tokens": 20000, "usage_tokens": 23086, "overrun_tokens": 3086,
              "fallback_stage": ("anchor_validated" if phase == "assessment"
                                 else "pre_round_assessment")} | fields
    return Degradation(**values)


def merged(category):
    from test_assessment import candidate

    from scrutare.findings.dedupe import dedupe_findings
    finding = candidate("one", category)
    citations = tuple(replace(c, validation="valid") for c in finding.evidence.citations)
    return dedupe_findings((replace(finding, evidence=replace(finding.evidence,
                                                               citations=citations)),))


@pytest.mark.parametrize("category,unresolved,want,rule", [
    ("style", (), "escalated", "phase_overrun_fallback"),
    (None, (), "escalated", "phase_overrun_fallback"),
    ("correctness", (), "changes_requested", "any_blocking_finding"),
    ("correctness", ("one",), "escalated", "unresolved_semantic_assessment"),
])
def test_degraded_verdict_is_never_a_clean_approval(category, unresolved, want, rule):
    from scrutare.findings.verdict import derive_verdict
    findings = merged(category) if category else ()
    verdict = derive_verdict(findings, configuration().verdict, evidence_version=2,
                             unresolved_candidates=unresolved, degradation=degradation())
    assert (verdict.verdict, verdict.rule) == (want, rule)
    document = verdict.to_dict()
    assert document["degraded"] == {
        "phase": "assessment", "round": None, "allowance_tokens": 20000, "grant_tokens": 20000,
        "usage_tokens": 23086, "overrun_tokens": 3086, "fallback_stage": "anchor_validated"}
    plain = derive_verdict(findings, configuration().verdict, evidence_version=2,
                           unresolved_candidates=unresolved)
    assert "degraded" not in plain.to_dict()
    assert plain.to_dict() == {k: v for k, v in document.items() if k not in ("degraded",)} | {
        "verdict": plain.verdict, "rule": plain.rule}


@pytest.mark.parametrize("fields", [
    {"phase": "discovery"},
    {"fallback_stage": "pre_round_assessment"},
    {"round": 1},
    {"phase": "discussion", "round": None},
    {"phase": "discussion", "round": 0},
    {"grant_tokens": -1},
    {"usage_tokens": True},
    {"overrun_tokens": -1},
    {"allowance_tokens": 0},
])
def test_degradation_record_is_validated(fields):
    from scrutare.findings.models import FindingError
    phase = fields.pop("phase", "assessment")
    with pytest.raises(FindingError):
        degradation(phase, **fields)


def test_discussion_degradation_may_reuse_grants_without_an_allowance():
    record = degradation("discussion")
    assert record.allowance_tokens is None and record.round == 1


def test_saved_degraded_verdict_parses_and_requires_its_record():
    from scrutare.findings.verdict import derive_verdict
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError
    verdict = derive_verdict((), configuration().verdict, evidence_version=2,
                             degradation=degradation("discussion"))
    saved = parse_saved_verdict(verdict.to_bytes(), evidence_version=2)
    assert saved.degradation == degradation("discussion")
    document = verdict.to_dict()
    for broken in (
        {k: v for k, v in document.items() if k != "degraded"},
        document | {"verdict": "approve"},
        document | {"degraded": document["degraded"] | {"overrun_tokens": "3086"}},
        document | {"degraded": document["degraded"] | {"extra": 1}},
    ):
        with pytest.raises(ReplayError):
            parse_saved_verdict(json.dumps(broken).encode(), evidence_version=2)


def phase_ledger(allowance=60, ceiling=200):
    from scrutare.engine.budgets import ReviewBudgetLedger
    settings = replace(configuration().budgets, per_persona_tokens=100,
                       review_max_tokens=ceiling)
    return ReviewBudgetLedger(("a", "b", "assessor"), settings,
                              reserved_allocations={"assessor": 40},
                              phase_allowances={"discussion": allowance})


def test_phase_allowance_is_carved_before_discovery_is_split():
    ledger = phase_ledger()
    assert dict(ledger.allocations) == {"a": 50, "b": 50, "assessor": 40}
    a, b = ledger.admit("a", "a/1"), ledger.admit("b", "b/1")
    assert (a.allocated_tokens, b.allocated_tokens) == (50, 50)
    from scrutare.engine.session_models import TokenUsage
    # Discovery spends its whole grant; the phase allowance is still admitted in full.
    ledger.settle(a, TokenUsage(input=50), True)
    ledger.settle(b, TokenUsage(input=50), True)
    leases = ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3")))
    assert [lease.allocated_tokens for lease in leases] == [30, 30]
    assert [lease.limit_tokens for lease in leases] == [30, 30]


def test_phase_sessions_split_atomically_and_account_to_the_phase():
    from scrutare.engine.session_models import TokenUsage
    ledger = phase_ledger(allowance=61)
    a, b = ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3")))
    assert (a.allocated_tokens, b.allocated_tokens) == (31, 30)
    ledger.settle(a, TokenUsage(input=40), True)  # nine past its grant
    # The other position's live remainder stays reserved: nothing is left to admit.
    assert ledger.admit_phase("discussion", (("assessor", "assessor/2"),)) is None
    ledger.settle(b, TokenUsage(input=10), True)
    (reassessment,) = ledger.admit_phase("discussion", (("assessor", "assessor/2"),))
    assert reassessment.allocated_tokens == 11
    ledger.settle(reassessment, TokenUsage(input=11), True)
    snapshot = ledger.snapshot()
    assert snapshot["usage"]["total"] == ledger.usage.total == 61
    assert [p["usage"]["total"] for p in snapshot["personas"]] == [0, 0, 0]
    assert snapshot["phases"] == [{
        "phase": "discussion", "allocated_tokens": 61, "usage": TokenUsage(input=61).to_dict(),
        "exhausted": True, "overshoot_tokens": 0}]
    assert {s["session_key"]: s["phase"] for s in snapshot["sessions"]} == {
        "a/3": "discussion", "b/3": "discussion", "assessor/2": "discussion"}
    assert snapshot["configured"]["phase_allowances"] == {"discussion": 61}


def test_phase_admission_is_all_or_nothing_and_refuses_uncertain_accounting():
    from scrutare.engine.session_models import TokenUsage
    ledger = phase_ledger(allowance=1)
    assert ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3"))) is None
    assert ledger.snapshot()["active_reservations"] == []
    ledger = phase_ledger()
    with pytest.raises(ValueError):
        ledger.admit_phase("assessment", (("a", "a/3"),))
    with pytest.raises(ValueError):
        ledger.admit_phase("discussion", ())
    discovery = ledger.admit("a", "a/1")
    assert ledger.admit_phase("discussion", (("a", "a/3"),)) is None
    ledger.mark_uncertain(discovery)
    ledger.settle(discovery, TokenUsage(input=1), False)
    assert ledger.admit_phase("discussion", (("b", "b/3"),)) is None


def test_phase_allowances_must_leave_discovery_capacity():
    with pytest.raises(ValueError):
        phase_ledger(allowance=160)
    with pytest.raises(ValueError):
        phase_ledger(allowance=0)


def test_snapshot_without_phases_keeps_its_shape():
    from scrutare.engine.budgets import ReviewBudgetLedger
    settings = replace(configuration().budgets, review_max_tokens=200)
    ledger = ReviewBudgetLedger(("a",), settings)
    lease = ledger.admit("a", "a/1")
    from scrutare.engine.session_models import TokenUsage
    ledger.settle(lease, TokenUsage(input=3), True)
    snapshot = ledger.snapshot()
    assert "phases" not in snapshot
    assert set(snapshot["configured"]) == {"review_max_tokens", "per_persona_tokens"}
    assert all(set(s) == {"session_key", "persona", "usage"} for s in snapshot["sessions"])
