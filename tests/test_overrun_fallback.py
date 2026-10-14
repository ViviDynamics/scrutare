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
