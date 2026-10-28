"""g3 protocol slips corrected by the model, within one original session grant."""

import asyncio
import copy
from dataclasses import replace

import pytest
from test_citation_correction_defects import answer, citation, finding
from test_citation_correction_session import RAIL, RUNTIME, TOTAL, fake_nare, load
from test_review_inputs import capture as capture
from test_review_inputs import save_json

from scrutare.config import ModelRail
from scrutare.engine.assessment_inputs import AssessmentInput
from scrutare.engine.nare_session import run_persona_session
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.engine.session_output import _findings

VALID = answer(finding(citation()))
PLACEHOLDER = answer(finding(citation()), dict(finding(), file="placeholder-unused", line=0))


def launch(
    capture,
    tmp_path,
    first,
    second=VALID,
    *,
    enabled=True,
    kind="discovery",
    first_step=None,
    correction=None,
    limit=1000,
    both=False,
    rail=RAIL,
    timeout=10,
):
    from test_citation_correction_defects import CALLER, OPENAI_MODELS
    from test_repository_context import contextual

    from scrutare.config import BudgetSettings, FindingSettings
    from scrutare.engine.budgets import ReviewBudgetLedger
    from scrutare.engine.persona_inputs import PersonaReviewInput
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.engine.session_artifacts import create_attempt_directory
    from scrutare.personas import PersonaDefinition

    config, _ = contextual(capture, caller=CALLER)
    config = replace(
        config,
        findings=FindingSettings("v2"),
        models=OPENAI_MODELS,
        budgets=BudgetSettings(limit, limit),
    )
    config = replace(
        config,
        findings=replace(config.findings, protocol_correction=enabled, citation_correction=both),
    )
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    inputs = prepare_review_inputs(capture, config)
    descriptor = PersonaReviewInput(PersonaDefinition("security", "Review."), inputs)
    ledger = ReviewBudgetLedger(("security",), BudgetSettings(limit, limit))
    lease = ledger.admit("security", "security/attempt-0001")
    attempt = create_attempt_directory(capture, "security", prepared_root=inputs.root)
    if kind != "discovery":
        candidates = tuple(
            replace(
                _findings(
                    VALID, "security", evidence_version=2, expected_root=descriptor.inputs.root
                )[0],
                candidate_id=f"given-{i}",
            )
            for i in range(1, 5)
        )
        if kind == "assessment":
            descriptor = AssessmentInput(descriptor.persona, descriptor.inputs, candidates)
        else:
            from scrutare.engine.discussion_inputs import DiscussionInput

            descriptor = DiscussionInput(
                descriptor.persona, descriptor.inputs, candidates, (), False, ()
            )
    outcome = asyncio.run(
        run_persona_session(
            descriptor,
            rail,
            lease,
            ledger=ledger,
            artifact_directory=attempt,
            runtime=NareRuntime(
                fake_nare(
                    tmp_path, first, {"output": second, **(correction or {})}, first_step=first_step
                ),
                6,
                timeout,
            ),
            capability=RUNTIME,
        )
    )
    return outcome, ledger, attempt


def assessments(*ids):
    return {
        "assessments": [
            dict(
                candidate_id=i,
                status="supported",
                reason="captured code",
                supporting_citations=[citation()],
                counter_citations=[],
            )
            for i in ids
        ]
    }


IDS = tuple(f"given-{i}" for i in range(1, 5))


def test_g3_line_zero_placeholder_corrected_without_engine_dropping_it(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PLACEHOLDER)
    assert (outcome.status, outcome.reason) == ("complete", "done")
    assert len(outcome.findings) == 1 and outcome.usage == ledger.usage == TOTAL
    assert load(attempt / "session.json")["output"] == PLACEHOLDER
    assert load(attempt / "correction-session.json")["output"] == VALID
    record = load(attempt / "protocol-correction.json")
    assert {d["type"] for d in record["defects"]} >= {"non_positive_line", "missing_evidence"}
    assert load(attempt / "result.json")["protocol_correction"]["outcome"] == "corrected"
    assert not (attempt / "citation-correction.json").exists()
    argv = record["argv"]
    for flag, value in [("--tools", "none"), ("--max-turns", "1"), ("--budget-tokens", "1000")]:
        assert argv[argv.index(flag) + 1] == value


@pytest.mark.parametrize(
    "bad_ids,defect",
    [
        ((*IDS, "never-given"), "unknown_candidate_id"),
        (IDS[:-1], "missing_candidate_id"),
        ((*IDS, IDS[0]), "duplicate_candidate_id"),
    ],
)
def test_g3_assessor_unknown_fifth_and_required_coverage(capture, tmp_path, bad_ids, defect):
    outcome, ledger, attempt = launch(
        capture, tmp_path, assessments(*bad_ids), assessments(*IDS), kind="assessment"
    )
    assert outcome.status == "complete" and len(outcome.findings) == 4
    assert outcome.usage == ledger.usage == TOTAL
    assert defect in {d["type"] for d in load(attempt / "protocol-correction.json")["defects"]}


@pytest.mark.parametrize(
    "field,value,defect",
    [
        ("line", 0, "non_positive_line"),
        ("line", True, "field_type"),
        ("category", "invented", "field_enum"),
        ("problem", 12, "field_type"),
    ],
)
def test_field_violations_surviving_schema(capture, tmp_path, field, value, defect):
    first = copy.deepcopy(VALID)
    first["findings"][0][field] = value
    outcome, _, attempt = launch(capture, tmp_path, first)
    assert outcome.status == "complete"
    assert defect in {d["type"] for d in load(attempt / "protocol-correction.json")["defects"]}


def test_citation_and_anchor_errors_share_one_full_answer_turn(capture, tmp_path):
    first = answer(dict(finding(citation(end_line=99)), line=-1))
    outcome, _, attempt = launch(
        capture, tmp_path, first, both=True, rail=ModelRail("openai", None, "m", "json_schema", 200)
    )
    assert outcome.status == "complete"
    record = load(attempt / "protocol-correction.json")
    assert {d["type"] for d in record["defects"]} >= {
        "non_positive_line",
        "line_range_outside_capture",
    }
    argv = record["argv"]
    assert argv[argv.index("--structured-output") + 1] == "json_schema"
    assert argv[argv.index("--max-tokens") + 1] == "200"
    assert not (attempt / "citation-correction.json").exists()


def test_failed_correction_is_never_corrected_again(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PLACEHOLDER, PLACEHOLDER)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert outcome.findings == () and outcome.usage == ledger.usage == TOTAL
    assert load(attempt / "result.json")["protocol_correction"]["outcome"] == "refused"


def test_off_is_unchanged(capture, tmp_path):
    outcome, _, attempt = launch(capture, tmp_path, PLACEHOLDER, enabled=False)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert "protocol_correction" not in load(attempt / "result.json")
    assert not list(attempt.glob("*correction*"))
    assert outcome.usage == TokenUsage(40, 10)


@pytest.mark.parametrize(
    "options",
    [
        {"first_step": {"saved_output": VALID}},
        {"first_step": {"status": "error", "stop": "provider"}},
        {"limit": 50},
    ],
)
def test_non_correctable_evidence_or_no_budget_not_offered(capture, tmp_path, options):
    outcome, _, attempt = launch(capture, tmp_path, PLACEHOLDER, **options)
    assert outcome.status != "complete"
    assert load(attempt / "result.json")["protocol_correction"]["offered"] is False
    assert not (attempt / "protocol-correction.json").exists()


def proposals(*ids):
    return {
        "proposals": [
            dict(candidate_id=i, action="retain", reason="captured code", citations=[citation()])
            for i in ids
        ]
    }


def test_discussion_uses_same_single_correction_for_pool_coverage(capture, tmp_path):
    outcome, ledger, attempt = launch(
        capture, tmp_path, proposals(*IDS, "extra"), proposals(*IDS), kind="discussion"
    )
    assert outcome.status == "complete" and len(outcome.findings) == 4
    assert outcome.usage == ledger.usage == TOTAL
    assert load(attempt / "result.json")["protocol_correction"]["outcome"] == "corrected"


@pytest.mark.parametrize(
    "step,expected",
    [
        ({"status": "error", "stop": "max_turns"}, "no_answer"),
        ({"never_start": True}, "no_answer"),
        ({"corrupt": True}, "failed"),
        ({"session_id": "foreign-session"}, "failed"),
    ],
)
def test_unusable_correction_keeps_refusal_and_records_outcome(capture, tmp_path, step, expected):
    outcome, _, attempt = launch(capture, tmp_path, PLACEHOLDER, correction=step)
    assert outcome.status == "failed" and outcome.findings == ()
    assert load(attempt / "result.json")["protocol_correction"]["outcome"] == expected


def test_a_mixed_answer_is_exhaustively_described(capture, tmp_path):
    first = answer(
        dict(finding(citation(sha256="bad", end_line=4)), line=0, category="unknown", problem=None)
    )
    outcome, _, attempt = launch(capture, tmp_path, first)
    assert outcome.status == "complete"
    assert {d["type"] for d in load(attempt / "protocol-correction.json")["defects"]} == {
        "non_positive_line",
        "field_enum",
        "field_type",
        "malformed_sha256",
        "line_range_outside_capture",
    }
