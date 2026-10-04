"""Exhaustion is captured code evidence that always escalates and can be replayed."""

import json
from dataclasses import FrozenInstanceError

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, FindingError, dedupe_findings, derive_verdict


@pytest.mark.parametrize("category", [None, "style", "correctness"])
@pytest.mark.parametrize("strategy", ["panel", "iterative", "debate"])
def test_exhaustion_overrides_blocking_advisory_and_empty_to_escalated(category, strategy):
    from scrutare.findings import Exhaustion, Verdict

    groups = dedupe_findings([] if category is None else [
        Finding(Anchor("main.py", 2), category, "Wrong result", "Breaks callers", "security"),
    ])
    exhaustion = Exhaustion(strategy, 3, 3)
    verdict = derive_verdict(groups, VerdictSettings(), exhaustion=exhaustion)
    assert verdict.verdict == "escalated"
    assert verdict.rule == "rounds_exhausted_without_convergence"
    assert verdict.findings == groups
    assert verdict.to_dict()["exhaustion"] == {
        "strategy": strategy, "rounds_completed": 3, "round_limit": 3, "converged": False,
    }
    assert verdict.to_dict()["schema_version"] == 1
    assert Verdict(groups, VerdictSettings(), exhaustion).to_bytes() == verdict.to_bytes()
    with pytest.raises(FrozenInstanceError):
        exhaustion.rounds_completed = 2
    with pytest.raises(FrozenInstanceError):
        verdict.exhaustion = None


def test_normal_verdict_bytes_unchanged():
    expected = b'''{
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
    assert derive_verdict([], VerdictSettings()).to_bytes() == expected
    assert derive_verdict([], VerdictSettings(), exhaustion=None).to_bytes() == expected


@pytest.mark.parametrize("changes", [
    {"strategy": "secret-strategy"}, {"strategy": None}, {"strategy": []},
    {"rounds_completed": True}, {"round_limit": True}, {"rounds_completed": 3.0},
    {"round_limit": 3.0}, {"rounds_completed": "3"}, {"round_limit": "3"},
    {"rounds_completed": None}, {"round_limit": None},
    {"rounds_completed": -1}, {"rounds_completed": 0}, {"rounds_completed": 2},
    {"rounds_completed": 4}, {"round_limit": 0}, {"round_limit": -1},
    {"converged": True}, {"converged": 0}, {"converged": None},
    {"converged": "secret-convergence"},
])
def test_invalid_exhaustion_rejected(changes):
    from scrutare.findings import Exhaustion

    with pytest.raises(FindingError, match="exhaustion") as error:
        Exhaustion(**{"strategy": "debate", "rounds_completed": 3, "round_limit": 3,
                      **changes})
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("invalid", [False, True, {}, "secret-exhaustion", 3])
def test_verdict_requires_typed_exhaustion(invalid):
    from scrutare.findings import Verdict

    for construct in (Verdict, derive_verdict):
        with pytest.raises(FindingError, match="exhaustion") as error:
            construct((), VerdictSettings(), exhaustion=invalid)
        assert "secret" not in str(error.value)


def test_artifact_exhaustion_reconstruction_is_byte_identical():
    from scrutare.findings import Exhaustion, MergedFinding, parse_finding

    sources = [
        Finding(Anchor("café.py", 2, "LEFT"), "style", " Wrong\tresult ",
                "Use consistent results", "junior-dev"),
        Finding(Anchor("café.py", 2, "LEFT"), "security", "Wrong result",
                "Exposes private data 🔒", "security"),
    ]
    sources.append(sources[0])
    original = derive_verdict(dedupe_findings(sources), VerdictSettings(),
                              exhaustion=Exhaustion("iterative", 1, 1))
    captured = json.loads(original.to_bytes())
    groups = []
    for group in captured["findings"]:
        observations = tuple(parse_finding({key: value for key, value in source.items()
                                           if key != "persona"}, persona=source["persona"])
                             for source in group["sources"])
        groups.append(MergedFinding(Anchor(group["file"], group["line"], group["side"]),
                                    group["problem"], observations))
    settings = VerdictSettings(tuple(captured["config"]["blocking_categories"]),
                               tuple(captured["config"]["advisory_categories"]))
    replayed = derive_verdict(groups, settings, exhaustion=Exhaustion(**captured["exhaustion"]))
    assert replayed.to_bytes() == original.to_bytes()
    captured["exhaustion"]["strategy"] = "panel"
    assert original.exhaustion.strategy == "iterative"
