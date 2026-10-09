from dataclasses import replace

import pytest

from scrutare.config import ConfigError, parse_config
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.session_models import TokenUsage


def configuration(extra=""):
    return parse_config("models: {default: {model: test}}\n" + extra)


def test_assessment_requires_structured_evidence_and_discovery_capacity():
    with pytest.raises(ConfigError):
        configuration("findings: {assessment: {enabled: true, tokens: 100}}")
    config = configuration("context: {enabled: true}\nfindings: {evidence: v2, "
                           "assessment: {enabled: true, tokens: 100}}")
    assert config.findings.assessment.tokens == 100
    with pytest.raises(ConfigError):
        configuration("context: {enabled: true}\nbudgets: {review_max_tokens: 100}\n"
                      "findings: {evidence: v2, assessment: {enabled: true, tokens: 100}}")
    assert "findings" not in configuration().to_dict()


def test_discovery_cannot_spend_reserved_assessment_allocation():
    settings = replace(configuration().budgets, per_persona_tokens=100, review_max_tokens=180)
    ledger = ReviewBudgetLedger(("a", "b", "assessor"), settings,
                               reserved_allocations={"assessor": 80})
    a = ledger.admit("a", "a/one")
    b = ledger.admit("b", "b/one")
    assert (a.allocated_tokens, b.allocated_tokens) == (50, 50)
    ledger.settle(a, TokenUsage(input=50), True)
    ledger.settle(b, TokenUsage(input=50), True)
    assessor = ledger.admit("assessor", "assessor/one")
    assert assessor.allocated_tokens == 80
    ledger.settle(assessor, TokenUsage(input=80), True)
    assert ledger.usage.total == 180
    assert ledger.admit("a", "a/two") is None


@pytest.mark.parametrize("reservation", [{"missing": 10}, {"a": 0}, {"a": 100}, {"a": True}])
def test_invalid_reservations_refused(reservation):
    with pytest.raises(ValueError):
        ReviewBudgetLedger(("a", "b"), replace(configuration().budgets,
                          review_max_tokens=100), reserved_allocations=reservation)


def candidate(identifier="one", category="correctness"):
    from test_structured_evidence import wire

    from scrutare.findings.models import parse_finding
    return parse_finding(wire() | {"category": category}, persona="security",
                         evidence_version=2, candidate_id=identifier)


def assessment_output(status="supported"):
    from test_structured_evidence import wire
    citation = wire()["evidence"]["citations"][0]
    return {"assessments": [{"candidate_id": "one", "status": status,
                             "reason": "The captured path establishes the claim.",
                             "supporting_citations": [citation] if status != "refuted" else [],
                             "counter_citations": [citation] if status == "refuted" else []}]}


def captured_context(tmp_path):
    import json
    from hashlib import sha256
    (tmp_path / "blob.txt").write_bytes(b"x\ny\n")
    (tmp_path / "repository-context.json").write_text(json.dumps({
        "revisions": {"head": {"sha": "a" * 40}}, "entries": [{
            "side": "head", "path": "a.py", "status": "captured", "revision": "a" * 40,
            "artifact": "blob.txt", "sha256": sha256(b"x\ny\n").hexdigest()}]}))
    return tmp_path


@pytest.mark.parametrize("status", ["supported", "refuted", "unresolved"])
def test_assessment_preserves_candidate_and_checks_both_citation_sets(tmp_path, status):
    from scrutare.engine.assessment import parse_assessments
    original = candidate()
    rows = parse_assessments(assessment_output(status), (original,), captured_context(tmp_path))
    assert rows[0].candidate is original
    assert rows[0].status == status
    references = rows[0].supporting_citations + rows[0].counter_citations
    assert references[0].validation == "valid"
    broken = assessment_output(status)
    key = "counter_citations" if status == "refuted" else "supporting_citations"
    broken["assessments"][0][key][0]["revision"] = "b" * 40
    with pytest.raises(ValueError):
        parse_assessments(broken, (original,), tmp_path)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "rewrite", "confidence",
                                       "uncited", "verdict"])
def test_assessor_cannot_rewrite_omit_or_vote_on_candidates(tmp_path, mutation):
    from scrutare.engine.assessment import parse_assessments
    output = assessment_output()
    row = output["assessments"][0]
    if mutation == "missing":
        output["assessments"] = []
    elif mutation == "duplicate":
        output["assessments"].append(row.copy())
    elif mutation == "unknown":
        row["candidate_id"] = "invented"
    elif mutation == "uncited":
        row["supporting_citations"] = []
    elif mutation == "verdict":
        output["verdict"] = "approve"
    else:
        row[mutation] = "untrusted"
    with pytest.raises(ValueError):
        parse_assessments(output, (candidate(),), captured_context(tmp_path))


def test_semantic_uncertainty_escalates_without_strategy_exhaustion():
    from scrutare.findings.verdict import derive_verdict
    verdict = derive_verdict((), configuration().verdict, evidence_version=2,
                             unresolved_candidates=("one",))
    assert verdict.verdict == "escalated"
    assert verdict.rule == "unresolved_semantic_assessment"
    assert verdict.exhaustion is None
    assert verdict.to_dict()["assessment_uncertainty"] == {
        "candidate_ids": ["one"], "attempts_completed": 1, "attempt_limit": 1}
    from scrutare.replay.artifacts import parse_saved_verdict
    assert parse_saved_verdict(verdict.to_bytes(), evidence_version=2)


from test_review_inputs import capture as capture


def prepared_candidate(capture):
    from hashlib import sha256
    from test_repository_context import contextual
    from test_review_inputs import save_json

    from scrutare.config import AssessmentSettings, FindingSettings
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.findings.models import Anchor, Citation, EvidenceV2, Finding

    config, _ = contextual(capture)
    config = replace(config, findings=FindingSettings("v2", AssessmentSettings(True, 80)),
                     budgets=replace(config.budgets, per_persona_tokens=100,
                                     review_max_tokens=400))
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    inputs = prepare_review_inputs(capture, config)
    citation = Citation("head", "a" * 40, "src/caller.py", 1, 1,
                        sha256(b"call_changed()\n").hexdigest(), validation="valid")
    finding = Finding(Anchor("src/app.py", 1), "correctness", "Claim", "Risk", "security",
                      EvidenceV2("call", (), "result", "failure", "loss", (citation,)), "one")
    return config, inputs, finding


def test_native_assessor_reconciles_output_and_accounts_shared_lease(capture, tmp_path):
    from test_nare_session import run, running_executable

    from scrutare.engine.assessment_inputs import AssessmentInput
    from scrutare.engine.session_artifacts import create_attempt_directory
    from scrutare.findings.models import artifact_data
    from scrutare.personas import PersonaDefinition

    config, inputs, original = prepared_candidate(capture)
    descriptor = AssessmentInput(PersonaDefinition("assessor", "Independently assess."),
                                 inputs, (original,))
    ledger = ReviewBudgetLedger(("security", "assessor"), config.budgets,
                               reserved_allocations={"assessor": 80})
    discovery = ledger.admit("security", "security/discovery")
    ledger.settle(discovery, TokenUsage(input=100), True)
    lease = ledger.admit("assessor", "assessor/attempt-0001")
    attempt = create_attempt_directory(capture, "assessor", prepared_root=inputs.root)
    reference = artifact_data(original.evidence.citations[0])
    reference.pop("validation")
    reference.pop("validation_reason")
    output = assessment_output()
    output["assessments"][0]["supporting_citations"] = [reference]
    outcome = run(descriptor, ledger, lease, attempt,
                  running_executable(tmp_path, output=output))
    assert outcome.status == "complete"
    assert ledger.usage.total == 110
    assert outcome.findings[0].candidate_id == "one"
    assert outcome.findings[0].persona == "assessor"
    assert outcome.findings[0].evidence == original.evidence
    assert (attempt / "session.json").exists()
    assert "assessments" in descriptor.output_schema()["properties"]


def test_saved_assessment_reason_must_match_terminal_evidence(capture, tmp_path):
    import json
    from test_nare_session import run, running_executable

    from scrutare.engine.assessment_inputs import AssessmentInput
    from scrutare.engine.session_artifacts import create_attempt_directory
    from scrutare.engine.session_output import SessionProtocolError, decode_session
    from scrutare.findings.models import artifact_data
    from scrutare.personas import PersonaDefinition

    config, inputs, original = prepared_candidate(capture)
    descriptor = AssessmentInput(PersonaDefinition("assessor", "Independently assess."),
                                 inputs, (original,))
    ledger = ReviewBudgetLedger(("assessor",), config.budgets)
    lease = ledger.admit("assessor", "assessor/attempt-0001")
    attempt = create_attempt_directory(capture, "assessor", prepared_root=inputs.root)
    reference = artifact_data(original.evidence.citations[0])
    reference.pop("validation")
    reference.pop("validation_reason")
    output = assessment_output("refuted")
    output["assessments"][0]["counter_citations"] = [reference]
    outcome = run(descriptor, ledger, lease, attempt,
                  running_executable(tmp_path, output=output))
    assert outcome.status == "complete"
    saved = json.loads((attempt / "session.json").read_bytes())
    saved["output"]["assessments"][0]["reason"] = "A different unsupported rationale."
    with pytest.raises(SessionProtocolError):
        decode_session((attempt / "stdout.jsonl").read_bytes(), json.dumps(saved).encode(),
                       persona="assessor", exit_code=0, expected_limit=lease.limit_tokens,
                       expected_root=inputs.root, descriptor=descriptor, evidence_version=2)


@pytest.mark.parametrize("status,want", [("supported", "changes_requested"),
                                         ("refuted", "approve"), ("unresolved", "escalated")])
def test_panel_applies_independent_assessment_and_preserves_disposition(capture, tmp_path,
                                                                       monkeypatch, status, want):
    import asyncio
    import json
    from test_nare_session import running_executable
    from test_panel import install

    from scrutare.engine.strategy import run_review
    from scrutare.engine.session_models import NareRuntime
    from scrutare.findings.models import artifact_data

    config, inputs, original = prepared_candidate(capture)
    install(monkeypatch, {"security": (original,)})
    output = assessment_output(status)
    reference = artifact_data(original.evidence.citations[0])
    reference.pop("validation")
    reference.pop("validation_reason")
    key = "counter_citations" if status == "refuted" else "supporting_citations"
    output["assessments"][0][key] = [reference]
    result = asyncio.run(run_review(capture, config, runtime=NareRuntime(
        running_executable(tmp_path, output=output))))
    assert result.verdict.verdict == want
    assert result.status == ("partial" if status == "unresolved" else "complete")
    assert result.usage.total == 50  # Four discovery turns (10 each) + assessor (10).
    saved = json.loads((capture / "assessment.json").read_bytes())
    assert saved["status"] == "complete"
    assert saved["candidates"][0]["candidate_id"] == "one"
    assert saved["assessments"][0]["status"] == status
    assert saved["allocation_tokens"] == 80
    if status != "supported":
        assert not result.verdict.findings
    else:
        assert result.verdict.findings[0].sources == (original,)
    from scrutare.replay.audit import replay_run
    assert replay_run(capture).saved_identical


def test_incomplete_assessment_withholds_verdict_and_retains_raw_output(capture, tmp_path,
                                                                      monkeypatch):
    import asyncio
    from test_nare_session import running_executable
    from test_panel import install

    from scrutare.engine.strategy import run_review
    from scrutare.engine.session_models import NareRuntime

    config, _, original = prepared_candidate(capture)
    install(monkeypatch, {"security": (original,)})
    result = asyncio.run(run_review(capture, config, runtime=NareRuntime(
        running_executable(tmp_path, output={"assessments": []}))))
    assert result.verdict is None
    assert result.status == "failed"
    assert not (capture / "verdict.json").exists()
    assert list(capture.glob("sessions/*/attempt-0001/stdout.jsonl"))


def test_semantic_escalation_posts_comment_with_explicit_reason():
    from scrutare.findings.verdict import derive_verdict
    from scrutare.poster.payload import build_review_payload
    verdict = derive_verdict((), configuration().verdict, evidence_version=2,
                             unresolved_candidates=("one",))
    payload = build_review_payload(verdict, "", head_sha="a" * 40, strategy="panel",
                                   post_mode="review", run_id="1" * 32,
                                   human_reviewers=("maintainer",))
    assert payload.event == "COMMENT"
    assert "semantic" in payload.body.lower()
    assert "@maintainer" in payload.body
    assert "one" in payload.body
