"""Opt-in phase allowances and last-complete-stage fallback on a measured overrun (#74)."""

import json
from dataclasses import replace

import pytest
from test_assessment import configuration
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, parse_config

FOCUSED = ("strategy: debate\ncontext: {enabled: true}\nfindings: {evidence: v2, "
           "assessment: {enabled: true, tokens: 100%s}, discussion: {enabled: true%s}}")


def focused(assessment="", discussion="", extra=""):
    return configuration(extra + FOCUSED % (assessment, discussion))


def test_new_phase_keys_default_off_and_stay_out_of_canonical_config():
    config = focused()
    assert config.findings.assessment.overrun == "void"
    assert config.findings.discussion.overrun == "void"
    assert config.findings.discussion.tokens is None
    assert config.to_dict()["findings"] == {
        "evidence": "v2", "assessment": {"enabled": True, "tokens": 100},
        "discussion": {"enabled": True}}


def test_phase_keys_round_trip_when_set():
    config = focused(", overrun: fallback", ", tokens: 300, overrun: fallback")
    assert config.findings.assessment.overrun == "fallback"
    assert config.findings.discussion.overrun == "fallback"
    assert config.findings.discussion.tokens == 300
    data = config.to_dict()["findings"]
    assert data["assessment"] == {"enabled": True, "tokens": 100, "overrun": "fallback"}
    assert data["discussion"] == {"enabled": True, "tokens": 300, "overrun": "fallback"}
    assert parse_config(json.dumps(config.to_dict())) == config


@pytest.mark.parametrize("text", [
    FOCUSED % (", overrun: drop", ""),
    FOCUSED % ("", ", overrun: true"),
    FOCUSED % ("", ", tokens: 0"),
    FOCUSED % ("", ", tokens: true"),
    FOCUSED % ("", ", tokens: 2.5"),
    FOCUSED % ("", ", unknown: 1"),
    # Assessor and discussion allowances together must leave discovery capacity.
    "budgets: {review_max_tokens: 400}\n" + FOCUSED % ("", ", tokens: 300"),
    # A phase policy needs its phase enabled.
    "context: {enabled: true}\nfindings: {evidence: v2, assessment: {enabled: false, "
    "overrun: fallback}}",
])
def test_invalid_phase_settings_refused(text):
    with pytest.raises(ConfigError):
        configuration(text)


def test_iterative_refuses_the_assessor_fallback():
    # Iterative re-derives verdicts from assessment state kept across pushes; a degraded
    # record would be lost there, so the opt-in policy is refused rather than silently ignored.
    with pytest.raises(ConfigError):
        configuration("strategy: iterative\ncontext: {enabled: true}\nfindings: {evidence: v2, "
                      "assessment: {enabled: true, tokens: 100, overrun: fallback}}")


def test_assessment_overrun_policy_needs_no_discussion():
    config = configuration("context: {enabled: true}\nfindings: {evidence: v2, "
                           "assessment: {enabled: true, tokens: 100, overrun: fallback}}")
    assert config.findings.assessment.overrun == "fallback"
    assert replace(config.findings.discussion) == config.findings.discussion


def degradation(phase="assessment", **fields):
    from scrutare.findings.verdict import Degradation
    values = {"phase": phase, "round": None if phase == "assessment" else 1,
              "allowance_tokens": 20000 if phase == "assessment" else None,
              "grant_tokens": 20000, "usage_tokens": 23086, "overrun_tokens": 3086,
              "fallback_stage": ("anchor_validated" if phase == "assessment"
                                 else "pre_round_assessment")} | fields
    return Degradation(**values)


def merged(category):
    from test_assessment import candidate

    from scrutare.findings.dedupe import dedupe_findings
    finding = candidate("one", category)
    citations = tuple(replace(c, validation="valid") for c in finding.evidence.citations)
    return dedupe_findings((replace(finding, evidence=replace(finding.evidence,
                                                               citations=citations)),))


@pytest.mark.parametrize("category,unresolved,want,rule", [
    ("style", (), "escalated", "phase_overrun_fallback"),
    (None, (), "escalated", "phase_overrun_fallback"),
    ("correctness", (), "changes_requested", "any_blocking_finding"),
    ("correctness", ("one",), "escalated", "unresolved_semantic_assessment"),
])
def test_degraded_verdict_is_never_a_clean_approval(category, unresolved, want, rule):
    from scrutare.findings.verdict import derive_verdict
    findings = merged(category) if category else ()
    verdict = derive_verdict(findings, configuration().verdict, evidence_version=2,
                             unresolved_candidates=unresolved, degradation=degradation())
    assert (verdict.verdict, verdict.rule) == (want, rule)
    document = verdict.to_dict()
    assert document["degraded"] == {
        "phase": "assessment", "round": None, "allowance_tokens": 20000, "grant_tokens": 20000,
        "usage_tokens": 23086, "overrun_tokens": 3086, "fallback_stage": "anchor_validated"}
    plain = derive_verdict(findings, configuration().verdict, evidence_version=2,
                           unresolved_candidates=unresolved)
    assert "degraded" not in plain.to_dict()
    assert plain.to_dict() == {k: v for k, v in document.items() if k not in ("degraded",)} | {
        "verdict": plain.verdict, "rule": plain.rule}


@pytest.mark.parametrize("fields", [
    {"phase": "discovery"},
    {"fallback_stage": "pre_round_assessment"},
    {"round": 1},
    {"phase": "discussion", "round": None},
    {"phase": "discussion", "round": 0},
    {"grant_tokens": -1},
    {"usage_tokens": True},
    {"overrun_tokens": -1},
    {"allowance_tokens": 0},
])
def test_degradation_record_is_validated(fields):
    from scrutare.findings.models import FindingError
    phase = fields.pop("phase", "assessment")
    with pytest.raises(FindingError):
        degradation(phase, **fields)


def test_discussion_degradation_may_reuse_grants_without_an_allowance():
    record = degradation("discussion")
    assert record.allowance_tokens is None and record.round == 1


def test_saved_degraded_verdict_parses_and_requires_its_record():
    from scrutare.findings.verdict import derive_verdict
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError
    verdict = derive_verdict((), configuration().verdict, evidence_version=2,
                             degradation=degradation("discussion"))
    saved = parse_saved_verdict(verdict.to_bytes(), evidence_version=2)
    assert saved.degradation == degradation("discussion")
    document = verdict.to_dict()
    for broken in (
        {k: v for k, v in document.items() if k != "degraded"},
        document | {"verdict": "approve"},
        document | {"degraded": document["degraded"] | {"overrun_tokens": "3086"}},
        document | {"degraded": document["degraded"] | {"extra": 1}},
    ):
        with pytest.raises(ReplayError):
            parse_saved_verdict(json.dumps(broken).encode(), evidence_version=2)


def phase_ledger(allowance=60, ceiling=200):
    from scrutare.engine.budgets import ReviewBudgetLedger
    settings = replace(configuration().budgets, per_persona_tokens=100,
                       review_max_tokens=ceiling)
    return ReviewBudgetLedger(("a", "b", "assessor"), settings,
                              reserved_allocations={"assessor": 40},
                              phase_allowances={"discussion": allowance})


def test_phase_allowance_is_carved_before_discovery_is_split():
    ledger = phase_ledger()
    assert dict(ledger.allocations) == {"a": 50, "b": 50, "assessor": 40}
    a, b = ledger.admit("a", "a/1"), ledger.admit("b", "b/1")
    assert (a.allocated_tokens, b.allocated_tokens) == (50, 50)
    from scrutare.engine.session_models import TokenUsage
    # Discovery spends its whole grant; the phase allowance is still admitted in full.
    ledger.settle(a, TokenUsage(input=50), True)
    ledger.settle(b, TokenUsage(input=50), True)
    leases = ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3")))
    assert [lease.allocated_tokens for lease in leases] == [30, 30]
    assert [lease.limit_tokens for lease in leases] == [30, 30]


def test_phase_sessions_split_atomically_and_account_to_the_phase():
    from scrutare.engine.session_models import TokenUsage
    ledger = phase_ledger(allowance=61)
    a, b = ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3")))
    assert (a.allocated_tokens, b.allocated_tokens) == (31, 30)
    ledger.settle(a, TokenUsage(input=40), True)  # nine past its grant
    # The other position's live remainder stays reserved: nothing is left to admit.
    assert ledger.admit_phase("discussion", (("assessor", "assessor/2"),)) is None
    ledger.settle(b, TokenUsage(input=10), True)
    (reassessment,) = ledger.admit_phase("discussion", (("assessor", "assessor/2"),))
    assert reassessment.allocated_tokens == 11
    ledger.settle(reassessment, TokenUsage(input=11), True)
    snapshot = ledger.snapshot()
    assert snapshot["usage"]["total"] == ledger.usage.total == 61
    assert [p["usage"]["total"] for p in snapshot["personas"]] == [0, 0, 0]
    assert snapshot["phases"] == [{
        "phase": "discussion", "allocated_tokens": 61, "usage": TokenUsage(input=61).to_dict(),
        "exhausted": True, "overshoot_tokens": 0}]
    assert {s["session_key"]: s["phase"] for s in snapshot["sessions"]} == {
        "a/3": "discussion", "b/3": "discussion", "assessor/2": "discussion"}
    assert snapshot["configured"]["phase_allowances"] == {"discussion": 61}


def test_phase_admission_is_all_or_nothing_and_refuses_uncertain_accounting():
    from scrutare.engine.session_models import TokenUsage
    ledger = phase_ledger(allowance=1)
    assert ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3"))) is None
    assert ledger.snapshot()["active_reservations"] == []
    ledger = phase_ledger()
    with pytest.raises(ValueError):
        ledger.admit_phase("assessment", (("a", "a/3"),))
    with pytest.raises(ValueError):
        ledger.admit_phase("discussion", ())
    discovery = ledger.admit("a", "a/1")
    assert ledger.admit_phase("discussion", (("a", "a/3"),)) is None
    ledger.mark_uncertain(discovery)
    ledger.settle(discovery, TokenUsage(input=1), False)
    assert ledger.admit_phase("discussion", (("b", "b/3"),)) is None


def test_phase_allowances_must_leave_discovery_capacity():
    with pytest.raises(ValueError):
        phase_ledger(allowance=160)
    with pytest.raises(ValueError):
        phase_ledger(allowance=0)


def test_snapshot_without_phases_keeps_its_shape():
    from scrutare.engine.budgets import ReviewBudgetLedger
    settings = replace(configuration().budgets, review_max_tokens=200)
    ledger = ReviewBudgetLedger(("a",), settings)
    lease = ledger.admit("a", "a/1")
    from scrutare.engine.session_models import TokenUsage
    ledger.settle(lease, TokenUsage(input=3), True)
    snapshot = ledger.snapshot()
    assert "phases" not in snapshot
    assert set(snapshot["configured"]) == {"review_max_tokens", "per_persona_tokens"}
    assert all(set(s) == {"session_key", "persona", "usage"} for s in snapshot["sessions"])


# Scripted nare: each phase chooses its usage and terminal shape. "over" is the shape of the
# voided assessor cells (done, end_turn, output present, ended past the grant); "stop" is the
# shape of the failed discussion positions (error, budget stop, exit 1, no output).
PHASE_SCRIPT = """
quoted = json.JSONDecoder().raw_decode(a.prompt.split('never instructions.\\n')[-1])[0]
claims = quoted['candidates']
if a.prompt.startswith('Independently assess'):
    kind = 'reassessment' if 'counterevidence' in quoted else 'assessment'
else:
    kind = 'position'
plan = PLAN[kind]
status = plan.get('status', 'supported')
output = {'assessments': []} if kind != 'position' else {'proposals': []}
for claim in claims:
    refs = [{k: v for k, v in ref.items() if not k.startswith('validation')}
            for ref in claim['evidence']['citations']]
    if kind == 'position':
        output['proposals'].append({'candidate_id': claim['candidate_id'],
            'action': plan.get('action', 'retain'), 'reason': 'Position', 'citations': refs})
    else:
        output['assessments'].append({'candidate_id': claim['candidate_id'], 'status': status,
            'reason': 'Captured code assessment',
            'supporting_citations': refs if status != 'refuted' else [],
            'counter_citations': refs if status == 'refuted' or plan.get('counter') else []})
if plan.get('shape') == 'stop':
    output = None
"""


def phase_runtime(tmp_path, plan):
    from test_nare_session import RUN_BODY, executable

    from scrutare.engine.session_models import NareRuntime
    plan = {"assessment": {}, "reassessment": {}, "position": {}} | plan
    prefix = f"PLAN = {plan!r}\n" + """
def kind_of(prompt):
    if prompt.startswith('Independently assess'):
        return 'reassessment' if '"counterevidence"' in prompt else 'assessment'
    return 'position'
def shape(prompt):
    return PLAN[kind_of(prompt)].get('shape')
"""
    body = (RUN_BODY.replace("INPUT", "PLAN[kind_of(a.prompt)].get('input', 4)")
            .replace("STATUS", "('error' if shape(a.prompt) == 'stop' else 'done')")
            .replace("STOP", "('budget' if shape(a.prompt) == 'stop' else 'end_turn')")
            .replace("OUTPUT", "output").replace("EXTRA", PHASE_SCRIPT)
            .replace("EXIT", "(1 if shape(a.prompt) == 'stop' else 0)"))
    return NareRuntime(executable(tmp_path, body=prefix + body))


def assessed_capture(capture, *, strategy="panel", overrun="fallback", category="correctness"):
    import shutil

    from test_assessment import prepared_candidate
    from test_review_inputs import save_json
    config, _, claim = prepared_candidate(capture)
    config = replace(config, strategy=strategy, findings=replace(
        config.findings, assessment=replace(config.findings.assessment, overrun=overrun)))
    shutil.rmtree(capture / "review-inputs")
    (capture / "effective-files.json").unlink()
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    return config, replace(claim, category=category)


@pytest.mark.parametrize("strategy", ["panel", "debate"])
@pytest.mark.parametrize("category,want,rule", [
    ("correctness", "changes_requested", "any_blocking_finding"),
    ("style", "escalated", "phase_overrun_fallback"),
])
def test_assessor_overrun_falls_back_to_anchor_validated_findings(
        capture, tmp_path, monkeypatch, strategy, category, want, rule):
    import asyncio

    from test_panel import install

    from scrutare.engine.strategy import run_review
    from scrutare.replay.audit import replay_run
    config, claim = assessed_capture(capture, strategy=strategy, category=category)
    install(monkeypatch, {"security": (claim,)})
    # The voided tools cell: the assessor finished its output 3,086 tokens past its grant.
    runtime = phase_runtime(tmp_path, {"assessment": {"input": 120}})
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    assert result.status == "partial"
    assert result.accounting_complete
    assert (result.verdict.verdict, result.verdict.rule) == (want, rule)
    assert result.verdict.findings[0].sources == (claim,)
    assert result.verdict.unresolved_candidates == ()
    record = {"phase": "assessment", "round": None, "allowance_tokens": 80, "grant_tokens": 80,
              "usage_tokens": 126, "overrun_tokens": 46, "fallback_stage": "anchor_validated"}
    assert result.verdict.to_dict()["degraded"] == record
    saved = json.loads((capture / "verdict.json").read_bytes())
    assert saved["degraded"] == record
    assessment = json.loads((capture / "assessment.json").read_bytes())
    assert assessment["status"] == "partial"
    assert assessment["outcome"]["reason"] == "budget"
    document = json.loads((capture / ("debate.json" if strategy == "debate" else "panel.json"))
                          .read_bytes())
    assert document["status"] == "partial"
    assert document["reason"] == "assessment_overrun_fallback"
    assert document["degraded"] == record
    replayed = replay_run(capture)
    assert replayed.saved_identical


@pytest.mark.parametrize("strategy", ["panel", "debate"])
def test_assessor_overrun_still_voids_by_default(capture, tmp_path, monkeypatch, strategy):
    import asyncio

    from test_panel import install

    from scrutare.engine.strategy import run_review
    config, claim = assessed_capture(capture, strategy=strategy, overrun="void")
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(run_review(capture, config, runtime=phase_runtime(
        tmp_path, {"assessment": {"input": 120}})))
    assert result.verdict is None
    assert not (capture / "verdict.json").exists()
    document = json.loads((capture / ("debate.json" if strategy == "debate" else "panel.json"))
                          .read_bytes())
    assert document["reason"] == "assessment_incomplete"
    assert "degraded" not in document


@pytest.mark.parametrize("failure", ["malformed", "uncertain"])
def test_assessor_failure_that_is_not_an_overrun_still_voids(capture, tmp_path, monkeypatch,
                                                              failure):
    import asyncio

    from test_nare_session import running_executable
    from test_panel import install

    from scrutare.engine.session_models import NareRuntime
    from scrutare.engine.strategy import run_review
    config, claim = assessed_capture(capture)
    install(monkeypatch, {"security": (claim,)})
    if failure == "malformed":
        runtime = NareRuntime(running_executable(tmp_path, output={"assessments": []}))
    else:  # A provider error with unknown accounting, past the grant.
        runtime = NareRuntime(running_executable(tmp_path, input_tokens=120, status="error",
                                                 stop=None, exit_code=1))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    assert result.verdict is None
    assert not (capture / "verdict.json").exists()


@pytest.mark.parametrize("tamper", ["policy", "complete", "accounting", "reason", "findings",
                                    "record", "allocation"])
def test_replay_refuses_a_fallback_the_evidence_does_not_support(capture, tmp_path, monkeypatch,
                                                                 tamper):
    import asyncio

    from test_panel import install
    from test_review_inputs import save_json

    from scrutare.engine.strategy import run_review
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, claim = assessed_capture(capture, category="style")
    install(monkeypatch, {"security": (claim,)})
    asyncio.run(run_review(capture, config, runtime=phase_runtime(
        tmp_path, {"assessment": {"input": 120}})))
    assert replay_run(capture).saved_identical
    path = capture / "assessment.json"
    document = json.loads(path.read_bytes())
    if tamper == "policy":
        path = capture / "config.json"
        document = json.loads(path.read_bytes())
        del document["findings"]["assessment"]["overrun"]
    elif tamper == "complete":
        document["status"] = document["outcome"]["status"] = "complete"
    elif tamper == "accounting":
        document["outcome"]["accounting_complete"] = False
    elif tamper == "reason":
        document["outcome"]["reason"] = "provider"
    elif tamper == "allocation":
        document["outcome"]["allocated_tokens"] = 79
    elif tamper == "findings":
        path = capture / "findings.json"
        document = []
    else:
        path = capture / "verdict.json"
        document = json.loads(path.read_bytes())
        document["degraded"]["usage_tokens"] = 80
    save_json(path, document)
    try:
        replayed = replay_run(capture)
    except ReplayError:
        return
    assert not replayed.saved_identical


def discussion_capture(capture, *, assessment="void", discussion="void", tokens=None,
                       budgets=None, personas=None, rounds=2, category="correctness"):
    from test_focused_debate_runtime import focused_capture
    from test_review_inputs import save_json

    from scrutare.config import DiscussionSettings
    config, claim = focused_capture(capture)
    findings = replace(config.findings,
                       assessment=replace(config.findings.assessment, overrun=assessment),
                       discussion=DiscussionSettings(True, tokens, discussion))
    config = replace(config, findings=findings, rounds=replace(config.rounds, max=rounds))
    if budgets is not None:
        config = replace(config, budgets=replace(config.budgets, **budgets))
    if personas is not None:
        config = replace(config, personas=personas)
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    return config, replace(claim, category=category)


def test_focused_assessor_overrun_falls_back_before_any_discussion(capture, tmp_path,
                                                                   monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = discussion_capture(capture, assessment="fallback", category="style")
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(
        tmp_path, {"assessment": {"input": 120, "status": "unresolved"}})))
    assert result.status == "partial"
    assert (result.verdict.verdict, result.verdict.rule) == ("escalated", "phase_overrun_fallback")
    # The overrun assessor's unresolved rows are not trusted; the candidate is kept unassessed.
    assert result.verdict.unresolved_candidates == ()
    assert result.verdict.findings[0].sources == (claim,)
    trace = json.loads((capture / "debate.json").read_bytes())
    assert (trace["status"], trace["reason"]) == ("partial", "assessment_overrun_fallback")
    assert trace["rounds"] == [] and trace["final_assessments"] == []
    assert trace["degraded"]["phase"] == "assessment"
    assert replay_run(capture).saved_identical
