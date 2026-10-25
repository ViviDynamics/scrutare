"""Which citation refusals are machine-describable, what the prompt says, and how it applies."""

import copy
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from test_repository_context import contextual
from test_review_inputs import capture as capture
from test_review_inputs import save_json

from scrutare.config import FindingSettings
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_output import _findings
from scrutare.findings.citation_correction import (
    apply_citation_correction,
    citation_correction_prompt,
    citation_correction_schema,
    citation_defects,
)
from scrutare.findings.evidence import CitationValidationError
from scrutare.findings.models import FindingError

CALLER = b"one\ntwo\nthree\n"
PLACEHOLDER = "334e0..placeholder"


def prepared(capture, *, caller=CALLER):
    config, _ = contextual(capture, caller=caller)
    config = replace(config, findings=FindingSettings("v2", citation_correction=True))
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    return prepare_review_inputs(capture, config)


def citation(content=CALLER, **changes):
    value = dict(side="head", revision="a" * 40, path="src/caller.py", start_line=1,
                 end_line=3, sha256=sha256(content).hexdigest())
    value.update(changes)
    return value


def finding(*citations, category="correctness"):
    return dict(file="src/app.py", line=1, category=category, problem="failure",
                reason="why", evidence=dict(
                    version=2, trigger="input", preconditions=["enabled"], expected="success",
                    observed="failure", impact="lost result", citations=list(citations)))


def answer(*findings):
    return {"findings": list(findings)}


def parser(inputs):
    return lambda document: _findings(document, "senior-dev", evidence_version=2,
                                      expected_root=inputs.root)


def defects(inputs, document):
    return citation_defects(document, inputs.root, parse=parser(inputs))


def test_a_valid_answer_has_nothing_to_correct(capture):
    inputs = prepared(capture)
    assert defects(inputs, answer(finding(citation()))) is None


def test_a_citation_past_the_end_states_the_last_citeable_line(capture):
    inputs = prepared(capture)
    document = answer(finding(citation(end_line=4)))
    with pytest.raises(CitationValidationError, match="line_range_outside_capture"):
        parser(inputs)(document)
    found = defects(inputs, document)
    assert [d.to_dict() for d in found] == [{
        "id": "c0001", "finding": 1, "citation": 1, "side": "head", "path": "src/caller.py",
        "problems": ["line_range_outside_capture"], "end_line": 4, "last_line": 3}]


def test_a_range_wholly_past_the_end_is_still_one_range_defect(capture):
    inputs = prepared(capture)
    found = defects(inputs, answer(finding(citation(start_line=5, end_line=6))))
    assert [(d.problems, d.end_line, d.last_line) for d in found] == [
        (("line_range_outside_capture",), 6, 3)]


@pytest.mark.parametrize("field,value,problem", [
    ("sha256", PLACEHOLDER, "malformed_sha256"),
    ("sha256", "", "malformed_sha256"),
    ("sha256", "A" * 64, "malformed_sha256"),
    ("revision", "", "malformed_revision"),
    ("revision", "a" * 39, "malformed_revision"),
    ("revision", "<head revision>", "malformed_revision"),
])
def test_a_malformed_or_missing_hash_or_revision_states_the_expected_form(
        capture, field, value, problem):
    inputs = prepared(capture)
    document = answer(finding(citation(**{field: value})))
    with pytest.raises(FindingError):
        parser(inputs)(document)
    found = defects(inputs, document)
    assert [d.to_dict() for d in found] == [{
        "id": "c0001", "finding": 1, "citation": 1, "side": "head", "path": "src/caller.py",
        "problems": [problem]}]


def test_every_defect_of_every_citation_is_named_in_answer_order(capture):
    inputs = prepared(capture)
    document = answer(
        finding(citation()),
        finding(citation(sha256=PLACEHOLDER, end_line=5), citation(),
                citation(revision="")),
        finding(citation(end_line=9)))
    found = defects(inputs, document)
    assert [(d.id, d.finding, d.citation, d.problems) for d in found] == [
        ("c0001", 2, 1, ("malformed_sha256", "line_range_outside_capture")),
        ("c0002", 2, 3, ("malformed_revision",)),
        ("c0003", 3, 1, ("line_range_outside_capture",)),
    ]


@pytest.mark.parametrize("document", [
    # A well-formed but wrong hash or revision is not machine-describable.
    answer(finding(citation(sha256="0" * 64))),
    answer(finding(citation(revision="b" * 40))),
    # An uncaptured path, a reversed range, a line before the first.
    answer(finding(citation(path="src/missing.py", end_line=9))),
    answer(finding(citation(start_line=3, end_line=2))),
    answer(finding(citation(start_line=0, end_line=9))),
    # A correctable defect beside one that is not: the answer would still be refused.
    answer(finding(citation(end_line=9)), finding(citation(sha256="0" * 64))),
    answer(finding(citation(end_line=9), category="opinion")),
    # A non-citation failure.
    answer(finding(citation(), category="opinion")),
    {"findings": [], "extra": True},
    {"findings": "none"},
    [],
], ids=["wrong-hash", "wrong-revision", "uncaptured", "reversed", "zero", "mixed",
        "category-beside", "category", "extra-field", "not-a-list", "not-an-object"])
def test_any_other_refusal_is_not_offered_a_correction(capture, document):
    inputs = prepared(capture)
    with pytest.raises((FindingError, ValueError)):
        parser(inputs)(document)
    assert defects(inputs, document) is None


def test_an_empty_file_has_no_citeable_line_to_state(capture):
    inputs = prepared(capture, caller=b"")
    document = answer(finding(citation(content=b"", start_line=1, end_line=1)))
    with pytest.raises(CitationValidationError, match="line_range_outside_capture"):
        parser(inputs)(document)
    assert defects(inputs, document) is None


def test_the_probe_never_changes_the_answer(capture):
    inputs = prepared(capture)
    document = answer(finding(citation(sha256=PLACEHOLDER, end_line=7)))
    before = copy.deepcopy(document)
    assert defects(inputs, document)
    assert document == before


def test_the_prompt_names_each_defect_and_never_echoes_model_text(capture):
    inputs = prepared(capture)
    found = defects(inputs, answer(finding(citation(sha256=PLACEHOLDER, end_line=7)),
                                   finding(citation(revision="not-a-revision"))))
    prompt = citation_correction_prompt(found)
    assert prompt == citation_correction_prompt(found)
    assert PLACEHOLDER not in prompt and "not-a-revision" not in prompt
    data = json.loads(prompt[prompt.index("\n") + 1:])
    assert data == {"defects": [
        {"id": "c0001", "finding": 1, "citation": 1, "side": "head", "path": "src/caller.py",
         "problems": [
             "sha256 is not a full content hash: 64 lowercase hexadecimal characters, as "
             "repository-context.json records it for this side and path",
             "end_line 7 is past the file's last line, 3"]},
        {"id": "c0002", "finding": 2, "citation": 1, "side": "head", "path": "src/caller.py",
         "problems": [
             "revision is not a full commit SHA: 40 lowercase hexadecimal characters, as "
             "repository-context.json records it for this side"]}]}
    for phrase in ("no tools", "exactly one entry per listed id", "never supplies a value",
                   "data, not instructions"):
        assert phrase in prompt
    # No value the validator expects appears in the prompt.
    assert sha256(CALLER).hexdigest() not in prompt and "a" * 40 not in prompt


def test_the_schema_admits_only_the_named_citations_in_nare_keywords(capture):
    inputs = prepared(capture)
    found = defects(inputs, answer(finding(citation(end_line=7)), finding(citation(sha256=""))))
    schema = citation_correction_schema(found)
    item = schema["properties"]["citations"]["items"]
    assert item["properties"]["id"] == {"type": "string", "enum": ["c0001", "c0002"]}
    assert item["required"] == ["id", "revision", "sha256", "start_line", "end_line"]
    assert schema["required"] == ["citations"] and schema["additionalProperties"] is False

    def keywords(node):
        if isinstance(node, dict):
            yield from node
            for key, value in node.items():
                if key != "properties":
                    yield from keywords(value)
                else:
                    for child in value.values():
                        yield from keywords(child)
        elif isinstance(node, list):
            for value in node:
                yield from keywords(value)

    assert set(keywords(schema)) <= {"type", "properties", "required", "items", "enum",
                                     "additionalProperties"}


def correction(*entries):
    return {"citations": [dict(id=i, revision=r, sha256=h, start_line=s, end_line=e)
                          for i, r, h, s, e in entries]}


def test_the_model_values_go_into_exactly_the_named_citations(capture):
    inputs = prepared(capture)
    good = sha256(CALLER).hexdigest()
    document = answer(finding(citation(), citation(sha256=PLACEHOLDER, end_line=7)),
                      finding(citation(end_line=4)))
    found = defects(inputs, document)
    corrected = apply_citation_correction(document, found, correction(
        ("c0002", "a" * 40, good, 2, 3), ("c0001", "a" * 40, good, 1, 3)))
    expected = copy.deepcopy(document)
    expected["findings"][0]["evidence"]["citations"][1].update(sha256=good, start_line=1,
                                                               end_line=3)
    expected["findings"][1]["evidence"]["citations"][0].update(start_line=2, end_line=3)
    assert corrected == expected
    assert len(parser(inputs)(corrected)) == 2


def test_validation_stays_authoritative_over_a_corrected_answer(capture):
    inputs = prepared(capture)
    document = answer(finding(citation(end_line=4)))
    found = defects(inputs, document)
    still_past = apply_citation_correction(document, found, correction(
        ("c0001", "a" * 40, sha256(CALLER).hexdigest(), 1, 4)))
    with pytest.raises(CitationValidationError, match="line_range_outside_capture"):
        parser(inputs)(still_past)
    wrong_hash = apply_citation_correction(document, found, correction(
        ("c0001", "a" * 40, "0" * 64, 1, 3)))
    with pytest.raises(CitationValidationError, match="content_hash_mismatch"):
        parser(inputs)(wrong_hash)


@pytest.mark.parametrize("value", [
    {"citations": []},
    correction(("c0001", "a" * 40, "0" * 64, 1, 3), ("c0001", "a" * 40, "0" * 64, 1, 3)),
    correction(("c0009", "a" * 40, "0" * 64, 1, 3)),
    {"citations": [{"id": "c0001", "revision": "a" * 40, "sha256": "0" * 64,
                    "start_line": 1, "end_line": 3, "path": "src/app.py"}]},
    {"citations": [{"id": "c0001", "revision": "a" * 40}]},
    {"citations": "c0001"},
    {"citations": [], "findings": []},
    [],
], ids=["missing", "duplicate", "unknown", "extra-field", "missing-field", "not-a-list",
        "extra-key", "not-an-object"])
def test_a_correction_answer_must_answer_each_named_citation_exactly_once(capture, value):
    inputs = prepared(capture)
    document = answer(finding(citation(end_line=4)))
    with pytest.raises(FindingError, match="citation correction"):
        apply_citation_correction(document, defects(inputs, document), value)
