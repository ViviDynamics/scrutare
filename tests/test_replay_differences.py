"""Detailed semantic paths retain ordering and anchored finding identity."""

from copy import deepcopy

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict


def document():
    return derive_verdict(dedupe_findings([
        Finding(Anchor("a.py", 1), "security", "Leak", "impact", "security"),
        Finding(Anchor("a.py", 1), "style", "Leak", "readability", "junior"),
        Finding(Anchor("a.py", 1), "correctness", "Leak", "invalid result", "senior"),
        Finding(Anchor("b.py", 2, "LEFT"), "docs", "Example", "usage", "docs"),
    ]), VerdictSettings()).to_dict()


@pytest.mark.parametrize("field,new", [
    ("file", "changed.py"), ("line", 99), ("side", "LEFT"), ("problem", "Changed"),
    ("blocking", False), ("categories", ["docs"]), ("personas", ["new"]),
    ("reasons", ["new"]), ("blocking_categories", []),
])
def test_each_group_semantic_field_is_named(field, new):
    from scrutare.replay.differences import diff_verdicts

    before = document()
    after = deepcopy(before)
    after["findings"][0][field] = new
    differences = diff_verdicts(before, after)
    assert differences
    assert all(d.path.startswith(f"verdict.findings[0].{field}") for d in differences)
    assert all("a.py" in d.finding and "Leak" in d.finding for d in differences)
    assert before == document()
    assert diff_verdicts(before, after) == differences


@pytest.mark.parametrize("field,new", [
    ("file", "changed.py"), ("line", 99), ("side", "LEFT"), ("category", "docs"),
    ("problem", "Changed"), ("reason", "Changed"), ("persona", "Changed"),
])
def test_each_source_semantic_field_is_named(field, new):
    from scrutare.replay.differences import diff_verdicts

    before = document()
    after = deepcopy(before)
    after["findings"][0]["sources"][0][field] = new
    differences = diff_verdicts(before, after)
    assert len(differences) == 1
    assert differences[0].path == f"verdict.findings[0].sources[0].{field}"
    assert differences[0].kind == "changed"
    assert differences[0].before == before["findings"][0]["sources"][0][field]
    assert differences[0].after == new


@pytest.mark.parametrize("path", [
    ("findings",), ("findings", 0, "sources"), ("findings", 0, "categories"),
    ("findings", 0, "personas"), ("findings", 0, "reasons"),
    ("findings", 0, "blocking_categories"),
    ("config", "blocking_categories"), ("config", "advisory_categories"),
])
def test_array_order_changes_are_visible_with_details(path):
    from scrutare.replay.differences import diff_verdicts

    before = document()
    after = deepcopy(before)
    target = after
    for part in path:
        target = target[part]
    target.reverse()
    differences = diff_verdicts(before, after)
    assert any(d.kind == "reordered" for d in differences)
    assert any(d.kind == "changed" for d in differences)


def test_additions_removals_rule_verdict_and_exhaustion_are_deterministic():
    from scrutare.replay.differences import diff_verdicts

    before = document()
    after = deepcopy(before)
    after.update(verdict="escalated", rule="rounds_exhausted_without_convergence",
                 exhaustion={"strategy": "debate", "rounds_completed": 3,
                             "round_limit": 3, "converged": False})
    after["findings"].pop()
    differences = diff_verdicts(before, after)
    assert [d.path for d in differences] == [
        "verdict.exhaustion", "verdict.findings[1]", "verdict.rule", "verdict.verdict",
    ]
    assert [d.kind for d in differences] == ["added", "removed", "changed", "changed"]
    assert differences[1].finding == "b.py:2:LEFT: Example"
    reverse = diff_verdicts(after, before)
    assert [d.kind for d in reverse] == ["removed", "added", "changed", "changed"]


def test_exhaustion_fields_and_scalar_types_are_not_lost():
    from scrutare.replay.differences import diff_verdicts

    before = {"exhaustion": {"strategy": "debate", "rounds_completed": 3,
                              "round_limit": 3, "converged": False}, "schema_version": 1}
    after = {"exhaustion": {"strategy": "panel", "rounds_completed": 4,
                             "round_limit": 4, "converged": True}, "schema_version": True}
    assert [d.path for d in diff_verdicts(before, after)] == [
        "verdict.exhaustion.converged", "verdict.exhaustion.round_limit",
        "verdict.exhaustion.rounds_completed", "verdict.exhaustion.strategy",
        "verdict.schema_version",
    ]


def test_equal_documents_have_no_semantic_difference():
    from scrutare.replay.differences import diff_verdicts

    assert diff_verdicts(document(), deepcopy(document())) == ()
