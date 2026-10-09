from dataclasses import replace

import pytest
from test_assessment import candidate, captured_context, configuration
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, parse_config


def test_focused_discussion_requires_assessed_v2_debate():
    with pytest.raises(ConfigError):
        configuration("findings: {discussion: {enabled: true}}")
    text = (
        "context: {enabled: true}\nfindings: {evidence: v2, "
        "assessment: {enabled: true, tokens: 100}, discussion: {enabled: true}}"
    )
    with pytest.raises(ConfigError):
        configuration(text)
    config = configuration("strategy: debate\n" + text)
    assert config.findings.discussion.enabled
    import json

    assert parse_config(json.dumps(config.to_dict())) == config
    assert "findings" not in configuration().to_dict()


def test_equivalent_claims_retain_sources_but_separate_triggers_and_causes():
    from scrutare.engine.discussion import equivalent_groups

    a = candidate("a", "security")
    b = replace(a, candidate_id="b", persona="junior-dev", category="style")
    other_trigger = replace(
        a, candidate_id="c", evidence=replace(a.evidence, trigger="other input")
    )
    other_cause = replace(a, candidate_id="d", reason="A different causal failure.")
    groups = equivalent_groups((a, b, other_trigger, other_cause))
    assert [len(group.sources) for group in groups] == [2, 1, 1]
    assert groups[0].sources == (a, b)
    assert groups[0].categories == ("security", "style")


def test_disputes_are_evidence_based_not_counts():
    from scrutare.engine.assessment import Assessment
    from scrutare.engine.discussion import disputed_ids

    supported = candidate("minority", "security")
    unresolved = replace(candidate("unknown"), problem="Other claim")
    contrary = replace(supported, candidate_id="opposition", persona="junior-dev")
    reference = supported.evidence.citations
    positive = Assessment(supported, "supported", "Supported", reference, ())
    assert disputed_ids((positive,)) == ()
    unknown = Assessment(unresolved, "unresolved", "Missing prerequisite", reference, ())
    assert disputed_ids((positive, unknown)) == ("unknown",)
    negative = Assessment(contrary, "refuted", "Contradictory code", (), reference)
    assert disputed_ids((positive, negative)) == ("minority", "opposition")
    conflicted = replace(positive, counter_citations=reference)
    assert disputed_ids((conflicted,)) == ("minority",)


def proposal(action="refute", citations=None):
    return {
        "proposals": [
            {
                "candidate_id": "one",
                "action": action,
                "reason": "The contrary code has a guard.",
                "citations": [] if citations is None else citations,
            }
        ]
    }


def test_unsupported_opposition_is_recorded_and_refused(tmp_path):
    from scrutare.engine.discussion import parse_proposals

    values = parse_proposals(proposal(), (candidate(),), captured_context(tmp_path))
    assert values[0].action == "refute"
    assert not values[0].transition_supported
    assert values[0].refusal == "missing_counterevidence"
    assert values[0].candidate_id == "one"


@pytest.mark.parametrize("mutation", ["rewrite", "confidence", "category", "unknown", "missing"])
def test_proposals_cannot_rewrite_downgrade_or_omit_claims(tmp_path, mutation):
    from scrutare.engine.discussion import parse_proposals

    value = proposal("retain")
    if mutation == "unknown":
        value["proposals"][0]["candidate_id"] = "invented"
    elif mutation == "missing":
        value["proposals"] = []
    else:
        value["proposals"][0][mutation] = "untrusted"
    with pytest.raises(ValueError):
        parse_proposals(value, (candidate(),), captured_context(tmp_path))


def test_proposed_counterevidence_binds_current_captured_bytes(tmp_path):
    from test_structured_evidence import wire

    from scrutare.engine.discussion import parse_proposals

    reference = wire()["evidence"]["citations"][0]
    value = proposal(citations=[reference])
    parsed = parse_proposals(value, (candidate(),), captured_context(tmp_path))
    assert parsed[0].transition_supported
    assert parsed[0].citations[0].validation == "valid"
    reference["revision"] = "b" * 40
    with pytest.raises(ValueError):
        parse_proposals(value, (candidate(),), tmp_path)


def test_discussion_native_descriptor_checks_proposals_and_quotes_only_disputes(capture):
    from test_assessment import prepared_candidate

    from scrutare.engine.discussion_inputs import DiscussionInput
    from scrutare.personas import PersonaDefinition

    _, inputs, claim = prepared_candidate(capture)
    descriptor = DiscussionInput(
        PersonaDefinition("security", "Independent perspective"), inputs, (claim,), (), False, ()
    )
    assert "Focused evidence discussion" in descriptor.prompt
    assert "proposals" in descriptor.output_schema()["properties"]
    assert descriptor.parse_output(proposal("retain"))[0].candidate_id == claim.candidate_id
    with pytest.raises(ValueError):
        descriptor.parse_output({"findings": []})


def test_assessment_reassessment_quotes_peer_counterevidence_without_claim_rewrite(capture):
    from test_assessment import prepared_candidate

    from scrutare.engine.assessment_inputs import AssessmentInput
    from scrutare.personas import PersonaDefinition

    _, inputs, claim = prepared_candidate(capture)
    descriptor = AssessmentInput(
        PersonaDefinition("assessor", "Independent"),
        inputs,
        (claim,),
        counterevidence=({"candidate_id": "one", "reason": "untrusted instruction"},),
    )
    assert "counterevidence" in descriptor.prompt
    assert descriptor.candidates == (claim,)

