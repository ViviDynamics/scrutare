"""Opt-in captured line counts: the citation bound is shown, never loosened."""

import json
from dataclasses import replace
from hashlib import sha256

import pytest
from test_repository_context import Objects, contextual
from test_review_inputs import CONFIG, read_json, save_json
from test_review_inputs import capture as capture
from test_structured_evidence import wire

from scrutare.config import ConfigError, parse_config
from scrutare.findings.models import FindingError, parse_finding

GUIDANCE = "end_line may never exceed line_count"


def test_line_counts_are_opt_in_and_absent_from_default_configuration():
    plain = parse_config(CONFIG + b"context: {enabled: true}\n")
    assert plain.context.line_counts is False
    assert "line_counts" not in plain.to_dict()["context"]
    chosen = parse_config(CONFIG + b"context: {enabled: true, line_counts: true}\n")
    assert chosen.context.line_counts is True
    assert chosen.to_dict()["context"]["line_counts"] is True
    assert parse_config(json.dumps(chosen.to_dict())) == chosen
    assert parse_config(json.dumps(plain.to_dict())) == plain
    with pytest.raises(ConfigError):
        parse_config(CONFIG + b"context: {enabled: true, line_counts: 1}\n")


def test_default_manifest_and_prompts_are_unchanged(capture):
    from scrutare.engine.assessment_inputs import AssessmentInput, assessor_persona
    from scrutare.engine.persona_inputs import PersonaReviewInput
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.personas import resolve_personas

    config, _ = contextual(capture)
    inputs = prepare_review_inputs(capture, config)
    manifest = read_json(inputs.root / "repository-context.json")
    assert "line_counts" not in manifest["settings"]
    assert all("line_count" not in entry for entry in manifest["entries"])
    persona = resolve_personas(("senior-dev",))[0]
    assert "line_count" not in PersonaReviewInput(persona, inputs).prompt
    assert "line_count" not in AssessmentInput(assessor_persona("evidence-assessor"), inputs,
                                               ()).prompt


def test_chosen_manifest_records_the_bound_the_validator_enforces(capture):
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.findings.evidence import validate_evidence

    config, _ = contextual(capture, caller=b"one\ntwo\nthree", line_counts=True)
    inputs = prepare_review_inputs(capture, config)
    manifest = read_json(inputs.root / "repository-context.json")
    assert manifest["settings"]["line_counts"] is True
    head = {entry["path"]: entry for entry in manifest["entries"] if entry["side"] == "head"}
    assert head["src/app.py"]["line_count"] == 1          # b"new\n": the newline starts no line
    assert head["src/caller.py"]["line_count"] == 3       # no final newline: last line counts
    assert head["missing.py"]["line_count"] is None
    assert all("line_count" in entry for entry in manifest["entries"])
    assert all((entry["line_count"] is not None) == (entry["status"] == "captured")
               for entry in manifest["entries"])
    # The unchanged validator accepts exactly line_count and refuses one line more.
    for entry in (head["src/app.py"], head["src/caller.py"]):
        data = wire()
        data.update(file="src/app.py")
        citation = data["evidence"]["citations"][0]
        citation.update(side="head", revision=entry["revision"], path=entry["path"],
                        sha256=entry["sha256"], start_line=1, end_line=entry["line_count"])
        validate_evidence(parse_finding(data, persona="tester", evidence_version=2,
                                        candidate_id="candidate-1"), inputs.root)
        citation.update(end_line=entry["line_count"] + 1)
        with pytest.raises(FindingError, match="line_range_outside_capture"):
            validate_evidence(parse_finding(data, persona="tester", evidence_version=2,
                                            candidate_id="candidate-1"), inputs.root)


def test_empty_captured_file_has_no_citeable_line(capture):
    from scrutare.engine.repository_context import captured_line_count

    assert captured_line_count(b"") == 0
    assert captured_line_count(b"\n") == 1
    assert captured_line_count(b"a\nb\n") == 2
    assert captured_line_count(b"a\nb") == 2
    # Only LF separates lines for citation purposes, exactly as the validator counts.
    assert captured_line_count(b"first\x0bsecond\r\n") == 1


@pytest.mark.parametrize("mutation", ["wrong", "dropped", "invented", "uncaptured"])
def test_line_count_tampering_fails_before_the_read_root(capture, mutation):
    from scrutare.engine.review_inputs import ReviewInputError, prepare_review_inputs

    chosen = mutation != "invented"
    config, _ = contextual(capture, **({"line_counts": True} if chosen else {}))
    path = capture / "repository-context/repository-context.json"
    manifest = read_json(path)
    captured = next(e for e in manifest["entries"] if e["status"] == "captured")
    omitted = next(e for e in manifest["entries"] if e["status"] != "captured")
    if mutation == "wrong":
        captured["line_count"] += 1
    elif mutation == "dropped":
        del captured["line_count"]
    elif mutation == "invented":
        for entry in manifest["entries"]:
            entry["line_count"] = 1 if entry["status"] == "captured" else None
    else:
        omitted["line_count"] = 7
    save_json(path, manifest)
    with pytest.raises(ReviewInputError):
        prepare_review_inputs(capture, config)


def test_manifest_from_other_setting_is_refused(capture):
    from scrutare.engine.review_inputs import ReviewInputError, prepare_review_inputs

    config, _ = contextual(capture, line_counts=True)
    plain = replace(config, context=replace(config.context, line_counts=False))
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, plain.to_dict())
    with pytest.raises(ReviewInputError):
        prepare_review_inputs(capture, plain)


def test_chosen_prompts_state_the_bound_for_reviewers_assessor_and_discussion(capture):
    from scrutare.engine.assessment_inputs import AssessmentInput, assessor_persona
    from scrutare.engine.discussion_inputs import DiscussionInput
    from scrutare.engine.persona_inputs import PersonaReviewInput
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.personas import resolve_personas

    config = parse_config(
        CONFIG + b"context: {enabled: true, line_counts: true}\nfindings: {evidence: v2}\n")
    metadata = read_json(capture / "metadata.json")
    metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = "a" * 40
    metadata["pull_request"]["head"]["repo"] = {"full_name": "fork/repo"}
    metadata["pull_request"]["base"]["sha"] = "b" * 40
    save_json(capture / "metadata.json", metadata)
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    from scrutare.engine.repository_context import capture_repository_context

    capture_repository_context(Objects(), capture, config)
    inputs = prepare_review_inputs(capture, config)
    persona = resolve_personas(("senior-dev",))[0]
    assessor = assessor_persona("evidence-assessor")
    prompts = [PersonaReviewInput(persona, inputs).prompt,
               AssessmentInput(assessor, inputs, ()).prompt,
               DiscussionInput(persona, inputs, (), (), False, ()).prompt]
    assert all(GUIDANCE in prompt for prompt in prompts)
    # Guidance precedes any quoted untrusted claim data.
    assert prompts[1].index(GUIDANCE) < prompts[1].index("untrusted quoted claims")

    plain = replace(config, context=replace(config.context, line_counts=False))
    assert sha256(json.dumps(plain.to_dict(), sort_keys=True).encode()).hexdigest() != sha256(
        json.dumps(config.to_dict(), sort_keys=True).encode()).hexdigest()


def test_evaluation_arms_inherit_the_setting_unchanged():
    from scrutare.evaluation.runner import comparison_configs

    config = parse_config(
        CONFIG + b"budgets: {per_persona_tokens: 100, review_max_tokens: 100}\n"
        b"context: {enabled: true, line_counts: true}\n")
    for comparison_set in ("architectures", "focused"):
        arms = comparison_configs(config, comparison_set=comparison_set)
        assert all(arm.context.line_counts for arm in arms.values())
    plain = replace(config, context=replace(config.context, line_counts=False))
    assert not any(arm.context.line_counts
                   for arm in comparison_configs(plain, comparison_set="focused").values())
