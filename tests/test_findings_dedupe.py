"""Dedupe retains source evidence and merges only exact anchored problems."""

import json
from dataclasses import FrozenInstanceError

import pytest

from scrutare.findings import Anchor, Finding, FindingError


def finding(**changes):
    return Finding(**{
        "anchor": Anchor("src/main.py", 7), "category": "correctness",
        "problem": "Wrong result", "reason": "Fails for empty input",
        "persona": "senior-dev", **changes,
    })


def test_cross_category_duplicates_retain_all_classifications_and_evidence():
    from scrutare.findings import dedupe_findings

    advisory = finding(category="style", problem="  Wrong\tresult\n", persona="junior-dev")
    blocking = finding(category="security", reason="Exposes private data", persona="security")
    correctness = finding(reason="Breaks existing callers")
    merged, = dedupe_findings([advisory, blocking, correctness, advisory])
    assert merged.anchor == Anchor("src/main.py", 7)
    assert merged.problem == "  Wrong\tresult\n"
    assert merged.categories == ("correctness", "security", "style")
    assert merged.personas == ("junior-dev", "security", "senior-dev")
    assert merged.reasons == (
        "Fails for empty input", "Exposes private data", "Breaks existing callers",
    )
    assert merged.sources == (advisory, blocking, correctness, advisory)


def test_category_order_comes_from_config_and_all_categories_survive():
    from scrutare.findings import dedupe_findings

    sources = [finding(category=category) for category in (
        "docs", "consistency", "style", "regression", "security", "correctness",
    )]
    merged, = dedupe_findings(sources)
    assert merged.categories == (
        "correctness", "security", "regression", "style", "consistency", "docs",
    )
    assert merged.sources == tuple(sources)


@pytest.mark.parametrize("changes", [
    {"anchor": Anchor("src/other.py", 7)},
    {"anchor": Anchor("src/main.py", 8)},
    {"anchor": Anchor("src/main.py", 7, "LEFT")},
    {"problem": "wrong result"}, {"problem": "Wrong result."},
    {"problem": "Incorrect result"}, {"problem": "Wrongresult"},
])
def test_different_anchors_case_punctuation_and_meaning_remain_distinct(changes):
    from scrutare.findings import dedupe_findings

    original = finding()
    other = finding(**changes)
    result = dedupe_findings([original, other])
    assert len(result) == 2
    assert result[0].sources == (original,)
    assert result[1].sources == (other,)


@pytest.mark.parametrize("problem", [
    " Wrong result ", "Wrong\n\tresult", "Wrong\u00a0result", "Wrong\u2003result",
])
def test_whitespace_normalization_collapses_only_whitespace(problem):
    from scrutare.findings import dedupe_findings

    original = finding()
    duplicate = finding(problem=problem)
    merged, = dedupe_findings([original, duplicate])
    assert merged.problem == "Wrong result"
    assert merged.sources == (original, duplicate)


def test_group_and_source_order_survive_a_generator():
    from scrutare.findings import dedupe_findings

    first = finding(problem="First problem")
    second = finding(problem="Second problem")
    duplicate = finding(problem="First   problem", reason="Another reason")
    result = dedupe_findings(item for item in [first, second, duplicate])
    assert tuple(group.problem for group in result) == ("First problem", "Second problem")
    assert result[0].sources == (first, duplicate)
    assert result[1].sources == (second,)


def test_empty_iterables_produce_empty_immutable_results():
    from scrutare.findings import dedupe_findings

    assert dedupe_findings([]) == ()
    assert dedupe_findings(iter(())) == ()


def test_output_cannot_change_or_capture_a_mutable_input_list():
    from scrutare.findings import dedupe_findings

    original = finding()
    sources = [original]
    result = dedupe_findings(sources)
    sources.append(finding(problem="Another problem"))
    assert result[0].sources == (original,)
    assert isinstance(result, tuple)
    assert hash(result[0])
    with pytest.raises(FrozenInstanceError):
        result[0].problem = "Changed"
    with pytest.raises(FrozenInstanceError):
        result[0].sources = ()


@pytest.mark.parametrize("invalid", [None, {}, "sensitive-secret"])
def test_untyped_input_is_rejected_with_safe_diagnostics(invalid):
    from scrutare.findings import dedupe_findings

    with pytest.raises(FindingError, match="findings") as error:
        dedupe_findings([finding(), invalid])
    assert "sensitive-secret" not in str(error.value)


@pytest.mark.parametrize("changes,field", [
    ({"anchor": "sensitive-secret"}, "anchor"),
    ({"problem": ""}, "problem"), ({"problem": " \n"}, "problem"),
    ({"problem": None}, "problem"),
    ({"sources": []}, "sources"), ({"sources": ()}, "sources"),
    ({"sources": ("sensitive-secret",)}, "sources"),
    ({"sources": (finding(anchor=Anchor("other.py", 7)),)}, "sources"),
    ({"sources": (finding(problem="Other problem"),)}, "sources"),
])
def test_direct_merged_constructor_rejects_invalid_or_mismatched_sources(changes, field):
    from scrutare.findings import MergedFinding

    with pytest.raises(FindingError, match=field) as error:
        MergedFinding(**{
            "anchor": Anchor("src/main.py", 7), "problem": "Wrong result",
            "sources": (finding(),), **changes,
        })
    assert "sensitive-secret" not in str(error.value)


def test_direct_constructor_accepts_matching_normalized_evidence():
    from scrutare.findings import MergedFinding

    merged = MergedFinding(Anchor("src/main.py", 7), " Wrong\nresult ", (finding(),))
    assert merged.problem == " Wrong\nresult "
    assert merged.categories == ("correctness",)


def test_serialization_is_deterministic_json_with_complete_fresh_source_evidence():
    from scrutare.findings import dedupe_findings

    first = finding(anchor=Anchor("src/main.py", 7, "LEFT"), category="style")
    second = finding(
        anchor=first.anchor, category="regression", problem="Wrong\tresult",
        reason="Existing behavior breaks", persona="devops",
    )
    merged, = dedupe_findings([first, second])
    expected = {
        "file": "src/main.py", "line": 7, "side": "LEFT", "problem": "Wrong result",
        "categories": ["regression", "style"], "personas": ["senior-dev", "devops"],
        "reasons": ["Fails for empty input", "Existing behavior breaks"],
        "sources": [
            {"file": "src/main.py", "line": 7, "side": "LEFT", "category": "style",
             "problem": "Wrong result", "reason": "Fails for empty input", "persona": "senior-dev"},
            {"file": "src/main.py", "line": 7, "side": "LEFT", "category": "regression",
             "problem": "Wrong\tresult", "reason": "Existing behavior breaks", "persona": "devops"},
        ],
    }
    actual = merged.to_dict()
    assert actual == expected
    assert json.loads(json.dumps(actual)) == expected
    assert json.dumps(actual) == json.dumps(merged.to_dict())
    actual["sources"][0]["reason"] = "Changed"
    actual["sources"].append({})
    actual["categories"].clear()
    actual["personas"].clear()
    actual["reasons"].clear()
    assert merged.to_dict() == expected
