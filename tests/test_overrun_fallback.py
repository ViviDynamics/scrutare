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
