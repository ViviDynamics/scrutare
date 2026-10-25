"""Opt-in citation correction: off by default, boolean, evidence v2 only, recorded when on."""

import json
from dataclasses import replace

import pytest
from test_review_inputs import CONFIG

from scrutare.config import ConfigError, parse_config

V2 = (b"models: {default: {provider: openai, model: test-model}}\n"
      b"context: {enabled: true}\n")


def test_the_setting_is_off_by_default_and_absent_from_the_record():
    plain = parse_config(V2 + b"findings: {evidence: v2}\n")
    assert plain.findings.citation_correction is False
    assert plain.to_dict()["findings"] == {"evidence": "v2"}
    assert parse_config(json.dumps(plain.to_dict())) == plain
    assert "findings" not in parse_config(CONFIG).to_dict()


def test_the_setting_is_recorded_only_when_chosen_and_re_parses():
    chosen = parse_config(V2 + b"findings: {evidence: v2, citation_correction: true}\n")
    assert chosen.findings.citation_correction is True
    assert chosen.to_dict()["findings"] == {"evidence": "v2", "citation_correction": True}
    assert parse_config(json.dumps(chosen.to_dict())) == chosen
    explicit_off = parse_config(V2 + b"findings: {evidence: v2, citation_correction: false}\n")
    assert explicit_off == parse_config(V2 + b"findings: {evidence: v2}\n")


@pytest.mark.parametrize("value", [b"1", b"yes-please", b"null", b"[true]"])
def test_a_non_boolean_is_refused(value):
    with pytest.raises(ConfigError, match="findings.citation_correction"):
        parse_config(V2 + b"findings: {evidence: v2, citation_correction: " + value + b"}\n")


def test_it_requires_evidence_v2_because_only_v2_findings_carry_citations():
    with pytest.raises(ConfigError, match="findings.citation_correction: requires evidence v2"):
        parse_config(V2.replace(b"context: {enabled: true}\n", b"")
                     + b"findings: {citation_correction: true}\n")
    with pytest.raises(ConfigError, match="requires evidence v2"):
        parse_config(V2 + b"findings: {evidence: legacy, citation_correction: true}\n")


def test_evaluation_arms_with_evidence_inherit_the_setting():
    from scrutare.evaluation.runner import comparison_configs

    config = parse_config(
        V2.replace(b"context: {enabled: true}\n", b"")
        + b"budgets: {per_persona_tokens: 100, review_max_tokens: 100}\n"
        b"context: {enabled: true, line_counts: true}\n"
        b"findings: {evidence: v2, citation_correction: true}\n")
    for comparison_set in ("architectures", "focused"):
        arms = comparison_configs(config, comparison_set=comparison_set)
        for name, arm in arms.items():
            # Arms without evidence v2 have no citations to correct.
            assert arm.findings.citation_correction == (arm.findings.evidence == "v2"), name
            assert arm.context.line_counts
    assert all(arm.findings.citation_correction for arm in comparison_configs(
        config, comparison_set="focused").values())
    plain = replace(config, findings=replace(config.findings, citation_correction=False))
    assert not any(arm.findings.citation_correction
                   for arm in comparison_configs(plain, comparison_set="focused").values())
    # Arms without the opt-in keep their earlier canonical bytes.
    for arm in comparison_configs(plain, comparison_set="focused").values():
        assert "citation_correction" not in json.dumps(arm.to_dict())


@pytest.mark.parametrize("models", [
    b"models: {default: {model: test-model}}\n",
    b"models: {default: {provider: anthropic, model: test-model}}\n",
    b"models: {default: {provider: openai, model: m}, overrides: {senior-dev: {provider: anthropic,"
    b" model: other}}}\n",
], ids=["default-anthropic", "anthropic", "anthropic-override"])
def test_it_requires_openai_rails_because_anthropic_refuses_a_tool_free_resumed_turn(models):
    # A resumed transcript holds tool_use blocks; Anthropic refuses them without tools, and a
    # correction turn offers none (nare's Anthropic transport sends tools=[]).
    with pytest.raises(ConfigError, match="citation_correction: requires provider openai"):
        parse_config(models + b"personas: [senior-dev]\ncontext: {enabled: true}\n"
                     b"findings: {evidence: v2, citation_correction: true}\n")
    assert parse_config(models + b"personas: [senior-dev]\ncontext: {enabled: true}\n"
                        b"findings: {evidence: v2}\n")
