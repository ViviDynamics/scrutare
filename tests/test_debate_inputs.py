"""Chair decisions can select and downgrade only verified caller-owned observations."""

from dataclasses import replace

import pytest
from test_fanout import configure
from test_panel import finding
from test_review_inputs import capture as capture

from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.findings.models import FindingError
from scrutare.personas import PersonaDefinition


def descriptor(capture):
    from scrutare.engine.debate_inputs import DebateInput

    config = configure(capture)
    return DebateInput(
        PersonaDefinition("debate-chair", "Senior developer chair"),
        prepare_review_inputs(capture, config),
        (finding(),),
        (),
        True,
        config.verdict.advisory_categories,
    )


def wire(source, category=None):
    return {
        "file": source.anchor.file,
        "line": source.anchor.line,
        "side": source.anchor.side,
        "problem": source.problem,
        "reason": source.reason,
        "category": category or source.category,
    }


def test_chair_selection_preserves_persona_and_permits_advisory_downgrade(capture):
    chair = descriptor(capture)
    assert chair.select({"findings": [wire(finding(), "style")], "converged": True}) == (
        replace(finding(), category="style"),
    )
    assert chair.select({"findings": [], "converged": True}) == ()
    assert "verified_pool" in chair.prompt and "positions" in chair.prompt
    assert chair.nare_input_args()[1:3] == ("--tools", "read")


@pytest.mark.parametrize(
    "change",
    [
        {"line": 999},
        {"problem": "invented"},
        {"reason": "invented"},
        {"category": "correctness"},
        {"persona": "security"},
    ],
)
def test_chair_cannot_invent_or_upgrade_verified_findings(capture, change):
    chair = descriptor(capture)
    data = wire(finding()) | change
    with pytest.raises(FindingError):
        chair.select({"findings": [data], "converged": True})


@pytest.mark.parametrize("value", [1, "true", None])
def test_chair_convergence_requires_boolean(capture, value):
    with pytest.raises(FindingError):
        descriptor(capture).parse_output({"findings": [], "converged": value})
