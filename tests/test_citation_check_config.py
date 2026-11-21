"""Opt-in in-session citation check: off by default, evidence v2 only, recorded when on."""

import json

import pytest
from test_review_inputs import CONFIG

from scrutare.config import ConfigError, parse_config

V2 = (b"models: {default: {provider: openai, model: test-model}}\n"
      b"context: {enabled: true}\n")


def findings(fields):
    return parse_config(V2 + b"findings: {evidence: v2" + fields + b"}\n")


def test_the_setting_is_off_by_default_and_absent_from_the_record():
    plain = findings(b"")
    assert plain.findings.citation_check == "post_session"
    assert plain.findings.max_corrections is None
    assert plain.findings.cited_reads is False
    assert plain.findings.session_corrections == 1
    assert plain.to_dict()["findings"] == {"evidence": "v2"}
    assert "findings" not in parse_config(CONFIG).to_dict()
    # The default spelled out is the default.
    assert findings(b", citation_check: post_session, cited_reads: false") == plain


def test_the_setting_is_recorded_only_when_chosen_and_re_parses():
    chosen = findings(b", citation_check: session")
    assert chosen.findings.citation_check == "session"
    assert chosen.findings.session_corrections == 2
    assert chosen.to_dict()["findings"] == {"evidence": "v2", "citation_check": "session"}
    assert parse_config(json.dumps(chosen.to_dict())) == chosen
    full = findings(b", citation_check: session, max_corrections: 3, cited_reads: true")
    assert full.findings.session_corrections == 3
    assert full.to_dict()["findings"] == {"evidence": "v2", "citation_check": "session",
                                          "max_corrections": 3, "cited_reads": True}
    assert parse_config(json.dumps(full.to_dict())) == full


@pytest.mark.parametrize("value", [b"inline", b"true", b"null", b"[session]", b"Session"])
def test_an_unknown_mode_is_refused(value):
    with pytest.raises(ConfigError, match="findings.citation_check"):
        findings(b", citation_check: " + value)


@pytest.mark.parametrize("value", [b"0", b"4", b"-1", b"true", b"1.5", b'"2"'])
def test_the_correction_budget_is_a_small_positive_integer(value):
    with pytest.raises(ConfigError, match="findings.max_corrections"):
        findings(b", citation_check: session, max_corrections: " + value)


@pytest.mark.parametrize("fields", [b", max_corrections: 2", b", cited_reads: true"])
def test_the_session_options_require_the_session_check(fields):
    with pytest.raises(ConfigError, match="requires citation_check session"):
        findings(fields)


@pytest.mark.parametrize("value", [b"1", b"yes-please", b"null"])
def test_cited_reads_is_a_boolean(value):
    with pytest.raises(ConfigError, match="findings.cited_reads"):
        findings(b", citation_check: session, cited_reads: " + value)


def test_it_requires_evidence_v2():
    with pytest.raises(ConfigError, match="findings.citation_check: requires evidence v2"):
        parse_config(V2 + b"findings: {citation_check: session}\n")


@pytest.mark.parametrize("other", [b"citation_correction", b"protocol_correction"])
def test_it_replaces_the_correction_turns_rather_than_chaining_them(other):
    with pytest.raises(ConfigError, match="findings.citation_check: session excludes"):
        findings(b", citation_check: session, " + other + b": true")


def test_any_provider_may_use_it():
    # The correction is a turn of the same session with tools offered, so no rail is excluded.
    config = parse_config(b"models: {default: {provider: anthropic, model: m}}\n"
                          b"context: {enabled: true}\n"
                          b"findings: {evidence: v2, citation_check: session}\n")
    assert config.findings.citation_check == "session"


def test_evaluation_arms_with_evidence_inherit_the_setting():
    from scrutare.evaluation.runner import comparison_configs

    config = parse_config(
        V2 + b"budgets: {per_persona_tokens: 100, review_max_tokens: 100}\n"
        b"findings: {evidence: v2, citation_check: session, max_corrections: 3}\n")
    for comparison_set in ("architectures", "focused"):
        for name, arm in comparison_configs(config, comparison_set=comparison_set).items():
            v2 = arm.findings.evidence == "v2"
            assert arm.findings.citation_check == ("session" if v2 else "post_session"), name
            assert arm.findings.max_corrections == (3 if v2 else None), name
