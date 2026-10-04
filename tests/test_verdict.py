"""Verdicts derive solely from configured classification and lossless evidence."""

import json
from dataclasses import FrozenInstanceError

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, FindingError, dedupe_findings


def finding(category="correctness", **changes):
    return Finding(**{
        "anchor": Anchor("src/main.py", 7), "category": category,
        "problem": "Wrong result", "reason": "Fails for empty input",
        "persona": "senior-dev", **changes,
    })


@pytest.mark.parametrize("categories", [(), ("style",), ("consistency",), ("docs",),
                                        ("docs", "style", "consistency")])
def test_empty_and_advisory_only_evidence_approves(categories):
    from scrutare.findings import derive_verdict

    verdict = derive_verdict(dedupe_findings(finding(c) for c in categories), VerdictSettings())
    assert verdict.verdict == "approve"
    assert verdict.rule == "no_blocking_findings"
    assert all(item["blocking"] is False for item in verdict.to_dict()["findings"])


@pytest.mark.parametrize("category", ["correctness", "security", "regression"])
def test_each_blocking_category_requests_changes(category):
    from scrutare.findings import derive_verdict

    verdict = derive_verdict(dedupe_findings([finding(category)]), VerdictSettings())
    assert verdict.verdict == "changes_requested"
    assert verdict.rule == "any_blocking_finding"
    assert verdict.to_dict()["findings"][0]["blocking"] is True
    assert verdict.to_dict()["findings"][0]["blocking_categories"] == [category]


@pytest.mark.parametrize("blocking_first", [True, False])
def test_mixed_findings_preserve_advisory_evidence_and_block(blocking_first):
    from scrutare.findings import derive_verdict

    advisory = finding("style", problem="Use consistent names")
    blocking = finding("security")
    sources = [blocking, advisory] if blocking_first else [advisory, blocking]
    verdict = derive_verdict(dedupe_findings(sources), VerdictSettings())
    artifact = verdict.to_dict()
    assert verdict.verdict == "changes_requested"
    assert [item["problem"] for item in artifact["findings"]] == [s.problem for s in sources]
    assert [item["blocking"] for item in artifact["findings"]] == (
        [True, False] if blocking_first else [False, True]
    )


def test_mixed_category_duplicate_group_keeps_all_sources_and_blocking_categories():
    from scrutare.findings import derive_verdict

    anchor = Anchor("src/main.py", 7, "LEFT")
    sources = [
        finding("style", anchor=anchor, persona="junior-dev", problem=" Wrong\tresult "),
        finding("security", anchor=anchor, persona="security", reason="Exposes private data"),
        finding("correctness", anchor=anchor, reason="Breaks callers"),
    ]
    sources.append(sources[0])
    verdict = derive_verdict(dedupe_findings(sources), VerdictSettings())
    assert verdict.to_dict() == {
        "schema_version": 1,
        "verdict": "changes_requested",
        "rule": "any_blocking_finding",
        "config": {
            "blocking_categories": ["correctness", "security", "regression"],
            "advisory_categories": ["style", "consistency", "docs"],
        },
        "findings": [{
            "file": "src/main.py", "line": 7, "side": "LEFT", "problem": " Wrong\tresult ",
            "categories": ["correctness", "security", "style"],
            "personas": ["junior-dev", "security", "senior-dev"],
            "reasons": ["Fails for empty input", "Exposes private data", "Breaks callers"],
            "sources": [
                {"file": "src/main.py", "line": 7, "side": "LEFT", "category": "style",
                 "problem": " Wrong\tresult ", "reason": "Fails for empty input",
                 "persona": "junior-dev"},
                {"file": "src/main.py", "line": 7, "side": "LEFT", "category": "security",
                 "problem": "Wrong result", "reason": "Exposes private data",
                 "persona": "security"},
                {"file": "src/main.py", "line": 7, "side": "LEFT", "category": "correctness",
                 "problem": "Wrong result", "reason": "Breaks callers", "persona": "senior-dev"},
                {"file": "src/main.py", "line": 7, "side": "LEFT", "category": "style",
                 "problem": " Wrong\tresult ", "reason": "Fails for empty input",
                 "persona": "junior-dev"},
            ],
            "blocking": True, "blocking_categories": ["correctness", "security"],
        }],
    }


@pytest.mark.parametrize("category,expected,blocking", [
    ("style", "changes_requested", ["style"]), ("correctness", "approve", []),
    ("security", "approve", []), ("regression", "approve", []),
])
def test_reclassification_follows_config_instead_of_category_names(category, expected, blocking):
    from scrutare.findings import derive_verdict

    settings = VerdictSettings(("style",), ("correctness", "security", "regression", "consistency",
                                          "docs"))
    verdict = derive_verdict(dedupe_findings([finding(category)]), settings)
    assert verdict.verdict == expected
    assert verdict.to_dict()["findings"][0]["blocking_categories"] == blocking


@pytest.mark.parametrize("blocking,advisory,expected", [
    ((), ("correctness", "security", "regression", "style", "consistency", "docs"), "approve"),
    (("correctness", "security", "regression", "style", "consistency", "docs"), (),
     "changes_requested"),
])
def test_partition_can_classify_every_category_on_one_side(blocking, advisory, expected):
    from scrutare.findings import derive_verdict

    verdict = derive_verdict(
        dedupe_findings([finding("docs")]), VerdictSettings(blocking, advisory)
    )
    assert verdict.verdict == expected


def test_derive_snapshots_generator_and_caller_list_as_immutable_evidence():
    from scrutare.findings import Verdict, derive_verdict

    groups = list(dedupe_findings([finding("style")]))
    verdict = derive_verdict(groups, VerdictSettings())
    independent = derive_verdict(iter(groups), VerdictSettings())
    groups.extend(dedupe_findings([finding("security")]))
    assert isinstance(verdict, Verdict)
    assert isinstance(verdict.findings, tuple)
    assert len(verdict.findings) == 1
    assert verdict == independent
    assert verdict.verdict == "approve"
    assert hash(verdict)
    with pytest.raises(FrozenInstanceError):
        verdict.findings = ()
    with pytest.raises(FrozenInstanceError):
        verdict.config = VerdictSettings()
    with pytest.raises(FrozenInstanceError):
        verdict.verdict = "changes_requested"


def test_direct_constructor_derives_same_artifact_without_arbitrary_status():
    from scrutare.findings import Verdict, derive_verdict

    findings = dedupe_findings([finding("security")])
    settings = VerdictSettings()
    direct = Verdict(findings, settings)
    assert direct.to_bytes() == derive_verdict(findings, settings).to_bytes()
    assert direct.verdict == "changes_requested"
    with pytest.raises(TypeError):
        Verdict(findings, settings, verdict="approve")


def test_fresh_artifact_mutation_cannot_change_evidence_policy_or_later_bytes():
    from scrutare.findings import derive_verdict

    verdict = derive_verdict(dedupe_findings([finding(), finding("style")]), VerdictSettings())
    original = verdict.to_bytes()
    artifact = verdict.to_dict()
    artifact["config"]["blocking_categories"].clear()
    artifact["config"]["advisory_categories"].append("security")
    item = artifact["findings"][0]
    item["sources"][0]["reason"] = "Mutated"
    for key in ("sources", "categories", "personas", "reasons", "blocking_categories"):
        item[key].clear()
    item["blocking"] = False
    artifact["findings"].clear()
    artifact["verdict"] = "approve"
    assert verdict.to_bytes() == original


def test_empty_artifact_bytes_have_canonical_sorted_json_and_trailing_newline():
    from scrutare.findings import derive_verdict

    verdict = derive_verdict([], VerdictSettings())
    assert verdict.to_bytes() == b'''{
  "config": {
    "advisory_categories": [
      "style",
      "consistency",
      "docs"
    ],
    "blocking_categories": [
      "correctness",
      "security",
      "regression"
    ]
  },
  "findings": [],
  "rule": "no_blocking_findings",
  "schema_version": 1,
  "verdict": "approve"
}
'''


def test_independently_derived_unicode_evidence_serializes_to_repeatable_utf8():
    from scrutare.findings import derive_verdict

    first = derive_verdict(dedupe_findings([finding(reason="Café 🔒")]), VerdictSettings())
    second = derive_verdict(dedupe_findings([finding(reason="Café 🔒")]), VerdictSettings())
    data = first.to_bytes()
    assert data == first.to_bytes() == second.to_bytes()
    assert "Café 🔒".encode() in data
    assert json.loads(data)["findings"][0]["sources"][0]["reason"] == "Café 🔒"


@pytest.mark.parametrize("invalid", [None, 1, True, "", "secret-finding", b"", {}, {"secret": 1}])
def test_derive_rejects_invalid_findings_containers_with_safe_diagnostics(invalid):
    from scrutare.findings import derive_verdict

    with pytest.raises(FindingError, match="findings") as error:
        derive_verdict(invalid, VerdictSettings())
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("invalid", [None, {}, "secret-finding", finding()])
def test_derive_rejects_unmerged_members_including_after_valid_evidence(invalid):
    from scrutare.findings import derive_verdict

    groups = dedupe_findings([finding()])
    with pytest.raises(FindingError, match="findings") as error:
        derive_verdict(iter([*groups, invalid]), VerdictSettings())
    assert "secret-finding" not in str(error.value)


@pytest.mark.parametrize("invalid", [None, [], {}, "secret-finding", (finding(),), (None,)])
def test_direct_constructor_requires_tuple_of_merged_findings(invalid):
    from scrutare.findings import Verdict

    with pytest.raises(FindingError, match="findings") as error:
        Verdict(invalid, VerdictSettings())
    assert "secret-finding" not in str(error.value)


@pytest.mark.parametrize("invalid", [None, {}, "secret-policy", True])
def test_constructor_and_deriver_require_typed_policy(invalid):
    from scrutare.findings import Verdict, derive_verdict

    for construct in (Verdict, derive_verdict):
        with pytest.raises(FindingError, match="config") as error:
            construct((), invalid)
        assert "secret-policy" not in str(error.value)
