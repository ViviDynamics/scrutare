"""Opt-in phase allowances and last-complete-stage fallback on a measured overrun (#74)."""

import json
from dataclasses import replace
from pathlib import Path

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
    kind = kind_of(a.prompt)
plan = PLAN[kind]
status = plan.get('status', 'supported')
output = {'assessments': []} if 'position' not in kind else {'proposals': []}
for claim in claims:
    refs = [{k: v for k, v in ref.items() if not k.startswith('validation')}
            for ref in claim['evidence']['citations']]
    if 'position' in kind:
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
    # Later discussion rounds may follow a separate plan.
    later = 'position2' in PLAN and 'attempt-0003' not in sys.argv[sys.argv.index('--session') + 1]
    return 'position2' if later else 'position'
def shape(prompt):
    return PLAN[kind_of(prompt)].get('shape')
"""
    # "fill" spends exactly the session's grant: complete, and nothing left over.
    body = (RUN_BODY.replace("INPUT", "(int(a.budget_tokens) - 6 if PLAN[kind_of(a.prompt)]"
                             ".get('fill') else PLAN[kind_of(a.prompt)].get('input', 4))")
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
    if budgets is not None:
        config = replace(config, budgets=replace(config.budgets, **budgets))
    findings = replace(config.findings,
                       assessment=replace(config.findings.assessment, overrun=assessment),
                       discussion=DiscussionSettings(True, tokens, discussion))
    config = replace(config, findings=findings, rounds=replace(config.rounds, max=rounds))
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


# The three failed focused cells (dev3-11/focused1/3, dev3-11/focused3/3, dev3-08/focused3/2):
# four reviewers at a 100,000 ceiling with a 30,000 assessor reservation, so 17,500 each;
# about 11,100 used in discovery; the assessor supported the claim but returned a
# counter-citation; every position re-read the files and stopped about 566 past its remainder.
CELL = {"budgets": {"per_persona_tokens": 25000, "review_max_tokens": 100000},
        "personas": ("senior-dev", "junior-dev", "security", "devops")}


def cell_capture(capture, monkeypatch, *, discussion="fallback", category="correctness",
                 tokens=None, rounds=1):
    from test_panel import install
    config, claim = discussion_capture(capture, discussion=discussion, category=category,
                                       tokens=tokens, rounds=rounds, **CELL)
    config = replace(config, findings=replace(config.findings, assessment=replace(
        config.findings.assessment, tokens=30000)))
    from test_review_inputs import save_json
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    install(monkeypatch, {"security": (claim,)}, initial_usage=11100)
    return config, claim


STOPPED = {"input": 6960, "shape": "stop"}  # 6,966 total against a 6,400 remainder


@pytest.mark.parametrize("category,initial,want,rule", [
    ("correctness", "supported", "changes_requested", "any_blocking_finding"),
    ("style", "supported", "escalated", "phase_overrun_fallback"),
    ("correctness", "unresolved", "escalated", "unresolved_semantic_assessment"),
])
def test_position_overrun_falls_back_to_the_pre_round_assessment(
        capture, tmp_path, monkeypatch, category, initial, want, rule):
    import asyncio

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = cell_capture(capture, monkeypatch, category=category)
    plan = {"assessment": {"status": initial, "counter": True}, "position": STOPPED}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert result.status == "partial"
    assert result.accounting_complete
    assert (result.verdict.verdict, result.verdict.rule) == (want, rule)
    assert result.verdict.findings[0].sources == (claim,)
    assert result.verdict.unresolved_candidates == (("one",) if initial == "unresolved" else ())
    assert result.verdict.exhaustion is None
    record = {"phase": "discussion", "round": 1, "allowance_tokens": None,
              "grant_tokens": 4 * 6400, "usage_tokens": 4 * 6966, "overrun_tokens": 4 * 566,
              "fallback_stage": "pre_round_assessment"}
    assert result.verdict.to_dict()["degraded"] == record
    trace = json.loads((capture / "debate.json").read_bytes())
    assert (trace["status"], trace["reason"]) == ("partial", "discussion_overrun_fallback")
    assert trace["degraded"] == record
    assert [p["reason"] for p in trace["rounds"][0]["positions"]] == ["budget"] * 4
    assert [row["status"] for row in trace["final_assessments"]] == [initial]
    assert trace["ledger"]["accounting_complete"] is True
    assert trace["ledger"]["usage"]["total"] == result.usage.total == (
        4 * 11100 + 10 + 4 * 6966)
    assert replay_run(capture).saved_identical


def test_position_overrun_still_voids_by_default(capture, tmp_path, monkeypatch):
    import asyncio

    from scrutare.engine.debate import run_debate
    config, _ = cell_capture(capture, monkeypatch, discussion="void")
    plan = {"assessment": {"counter": True}, "position": STOPPED}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert (result.status, result.verdict) == ("failed", None)
    trace = json.loads((capture / "debate.json").read_bytes())
    assert trace["reason"] == "perspectives_incomplete" and "degraded" not in trace
    assert not (capture / "verdict.json").exists()


@pytest.mark.parametrize("tamper", ["policy", "position", "proposals", "final", "record",
                                    "status"])
def test_replay_refuses_an_unsupported_discussion_fallback(capture, tmp_path, monkeypatch,
                                                            tamper):
    import asyncio

    from test_review_inputs import save_json

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, _ = cell_capture(capture, monkeypatch, category="style")
    plan = {"assessment": {"counter": True}, "position": STOPPED}
    asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert replay_run(capture).saved_identical
    path = capture / "debate.json"
    document = json.loads(path.read_bytes())
    if tamper == "policy":
        path = capture / "config.json"
        document = json.loads(path.read_bytes())
        del document["findings"]["discussion"]["overrun"]
    elif tamper == "position":
        document["rounds"][0]["positions"][0]["reason"] = "provider"
    elif tamper == "proposals":
        document["rounds"][0]["proposals"] = [{"candidate_id": "one"}]
    elif tamper == "final":
        document["final_assessments"][0]["status"] = "refuted"
    elif tamper == "status":
        document["status"] = "complete"
    else:
        path = capture / "verdict.json"
        document = json.loads(path.read_bytes())
        document["degraded"]["grant_tokens"] = 27864
    save_json(path, document)
    try:
        replayed = replay_run(capture)
    except ReplayError:
        return
    assert not replayed.saved_identical


@pytest.mark.parametrize("outcomes,want", [
    ([{"status": "partial", "reason": "budget"}], True),
    ([{"status": "partial", "reason": "budget"}, {"status": "complete"}], True),
    ([{"status": "partial", "reason": "budget"}, {"status": "failed", "reason": "provider"}],
     False),
    ([{"status": "partial", "reason": "budget", "accounting_complete": False}], False),
    ([{"status": "partial", "reason": "budget"}, {"status": "complete",
                                                  "output_available": False}], False),
    ([{"status": "not_started", "reason": "review_budget"}], False),
    ([{"status": "complete"}], False),
    ([], False),
])
def test_only_measured_overruns_qualify_for_fallback(outcomes, want):
    from scrutare.engine.overrun import phase_degradation
    full = [{"accounting_complete": True, "output_available": True, "allocated_tokens": 10,
             "overshoot_tokens": 2, "usage": {"total": 12}} | outcome for outcome in outcomes]
    record = phase_degradation("discussion", full, round_number=1, allowance=None)
    assert (record is not None) == want


def test_reassessment_overrun_falls_back_to_the_pre_round_assessment(capture, tmp_path,
                                                                     monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = discussion_capture(capture, discussion="fallback")
    install(monkeypatch, {"security": (claim,)})
    plan = {"assessment": {"status": "unresolved"},
            "reassessment": {"status": "refuted", "input": 200}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    # The overrun reassessment's refutation is not trusted; the unresolved blocking claim
    # still escalates under the existing rule.
    assert result.status == "partial"
    assert (result.verdict.verdict, result.verdict.rule) == (
        "escalated", "unresolved_semantic_assessment")
    assert result.verdict.unresolved_candidates == ("one",)
    reassessment = json.loads((capture / "assessment-round-0001.json").read_bytes())
    outcome = reassessment["outcome"]
    assert (outcome["status"], outcome["reason"]) == ("partial", "budget")
    assert result.verdict.to_dict()["degraded"] == {
        "phase": "discussion", "round": 1, "allowance_tokens": None,
        "grant_tokens": outcome["allocated_tokens"], "usage_tokens": 206,
        "overrun_tokens": 206 - outcome["allocated_tokens"],
        "fallback_stage": "pre_round_assessment"}
    trace = json.loads((capture / "debate.json").read_bytes())
    assert trace["reason"] == "discussion_overrun_fallback"
    assert trace["rounds"][0]["disposition_changes"] == []
    assert [row["status"] for row in trace["final_assessments"]] == ["unresolved"]
    assert replay_run(capture).saved_identical


def test_later_round_overrun_keeps_the_completed_rounds(capture, tmp_path, monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = discussion_capture(capture, discussion="fallback", category="style")
    install(monkeypatch, {"security": (claim,)})
    plan = {"assessment": {"status": "unresolved"}, "reassessment": {"status": "unresolved"},
            "position2": {"input": 200, "shape": "stop"}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert result.status == "partial"
    assert result.verdict.to_dict()["degraded"]["round"] == 2
    assert result.verdict.exhaustion is None
    trace = json.loads((capture / "debate.json").read_bytes())
    assert len(trace["rounds"]) == 2
    assert trace["rounds"][0]["reassessment"] == "assessment-round-0001.json"
    assert replay_run(capture).saved_identical


@pytest.mark.parametrize("tamper", ["reason", "changes", "allocation"])
def test_replay_refuses_an_unsupported_reassessment_fallback(capture, tmp_path, monkeypatch,
                                                             tamper):
    from test_review_inputs import save_json

    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    test_reassessment_overrun_falls_back_to_the_pre_round_assessment(capture, tmp_path,
                                                                     monkeypatch)
    path = capture / "assessment-round-0001.json"
    document = json.loads(path.read_bytes())
    if tamper == "reason":
        document["outcome"]["reason"] = "provider"
    elif tamper == "allocation":
        document["outcome"]["allocated_tokens"] += 1
    else:
        path = capture / "debate.json"
        document = json.loads(path.read_bytes())
        document["rounds"][0]["disposition_changes"] = [{"candidate_id": "one"}]
    save_json(path, document)
    with pytest.raises(ReplayError):
        replay_run(capture)


# The same cells with a separate discussion allowance carved out before discovery.
ALLOWANCE_CELL = {"per_persona_tokens": 25000, "review_max_tokens": 110000}


def allowance_capture(capture, monkeypatch, *, discussion="void"):
    config, claim = cell_capture(capture, monkeypatch, discussion=discussion, tokens=30000)
    config = replace(config, budgets=replace(config.budgets, **ALLOWANCE_CELL))
    from test_review_inputs import save_json
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    return config, claim


def test_discussion_allowance_admits_positions_without_spending_discovery_grants(
        capture, tmp_path, monkeypatch):
    import asyncio

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = allowance_capture(capture, monkeypatch)
    plan = {"assessment": {"counter": True}, "position": {"input": 6960}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert (result.status, result.verdict.verdict) == ("complete", "changes_requested")
    trace = json.loads((capture / "debate.json").read_bytes())
    ledger = trace["ledger"]
    assert ledger["allocations"] == {"senior-dev": 12500, "junior-dev": 12500,
                                     "security": 12500, "devops": 12500,
                                     "evidence-assessor": 30000}
    assert ledger["configured"]["phase_allowances"] == {"discussion": 30000}
    assert [p["allocated_tokens"] for p in trace["rounds"][0]["positions"]] == [7500] * 4
    assert [p["usage"]["total"] for p in ledger["personas"][:4]] == [11100] * 4
    assert ledger["phases"][0]["usage"]["total"] == 4 * 6966
    assert ledger["usage"]["total"] == result.usage.total == 4 * 11100 + 10 + 4 * 6966
    assert {s["session_key"]: s.get("phase") for s in ledger["sessions"]}["devops/attempt-0003"] \
        == "discussion"
    fanout = json.loads((capture / "fanout.json").read_bytes())
    assert fanout["ledger"]["configured"]["phase_allowances"] == {"discussion": 30000}
    assert replay_run(capture).saved_identical


def test_discussion_allowance_overrun_records_its_allowance(capture, tmp_path, monkeypatch):
    import asyncio

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, _ = allowance_capture(capture, monkeypatch, discussion="fallback")
    plan = {"assessment": {"counter": True}, "position": {"input": 8000, "shape": "stop"}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert result.verdict.to_dict()["degraded"] == {
        "phase": "discussion", "round": 1, "allowance_tokens": 30000, "grant_tokens": 30000,
        "usage_tokens": 4 * 8006, "overrun_tokens": 4 * 506,
        "fallback_stage": "pre_round_assessment"}
    assert replay_run(capture).saved_identical


def test_reassessment_draws_on_the_discussion_allowance(capture, tmp_path, monkeypatch):
    import asyncio

    from test_panel import install
    from test_review_inputs import save_json

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, claim = discussion_capture(capture, tokens=100, rounds=1)
    install(monkeypatch, {"security": (claim,)})
    plan = {"assessment": {"status": "unresolved"}, "reassessment": {"status": "refuted"}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert (result.status, result.verdict.verdict) == ("complete", "approve")
    trace = json.loads((capture / "debate.json").read_bytes())
    positions = trace["rounds"][0]["positions"]
    used = sum(p["usage"]["total"] for p in positions)
    reassessment = json.loads((capture / "assessment-round-0001.json").read_bytes())
    assert reassessment["allocation_tokens"] == 100
    assert reassessment["outcome"]["allocated_tokens"] == 100 - used
    assessor = next(p for p in trace["ledger"]["personas"]
                    if p["persona"] == "evidence-assessor")
    assert assessor["usage"]["total"] == 10  # the initial assessment alone
    assert replay_run(capture).saved_identical
    reassessment["outcome"]["allocated_tokens"] = 101 - used
    save_json(capture / "assessment-round-0001.json", reassessment)
    with pytest.raises(ReplayError):
        replay_run(capture)


def degraded_payload(category=None, unresolved=(), phase="discussion"):
    from scrutare.findings.verdict import derive_verdict
    from scrutare.poster.payload import build_review_payload
    findings = merged(category) if category else ()
    verdict = derive_verdict(findings, configuration().verdict, evidence_version=2,
                             unresolved_candidates=unresolved, degradation=degradation(phase))
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1 +1 @@\n-old\n+new\n")
    return build_review_payload(verdict, diff, head_sha="a" * 40, strategy="debate",
                                post_mode="review", run_id="1" * 32,
                                human_reviewers=("maintainer",))


DEGRADED_TEXT = ("Degraded: the discussion phase overran its grant in round 1 and fell back "
                 "to pre_round_assessment. It is not an approval.\nGrant tokens: 20000\n"
                 "Usage tokens: 23086\nOverrun tokens: 3086\n")


def test_degraded_fallback_without_blocking_findings_posts_an_escalation():
    from scrutare.replay.posting import _original_targets
    payload = degraded_payload("style")
    assert payload.event == "COMMENT"
    assert payload.body.startswith("Scrutare escalation\n\nStrategy: debate\n"
                                   "Verdict: escalated\nRule: phase_overrun_fallback\n")
    assert DEGRADED_TEXT in payload.body
    assert "@maintainer" in payload.body
    assert _original_targets(payload) == ("maintainer",)


def test_degraded_fallback_with_blocking_findings_still_requests_changes():
    payload = degraded_payload("correctness")
    assert payload.event == "REQUEST_CHANGES"
    assert "Rule: any_blocking_finding\n" + DEGRADED_TEXT in payload.body


def test_degraded_fallback_with_unresolved_blocking_claims_escalates_under_its_rule():
    from scrutare.replay.posting import _original_targets
    payload = degraded_payload("correctness", ("one",))
    assert payload.event == "COMMENT"
    assert "Rule: unresolved_semantic_assessment\n" + DEGRADED_TEXT in payload.body
    assert _original_targets(payload) == ("maintainer",)


def test_assessor_degraded_text_names_its_stage():
    payload = degraded_payload("style", phase="assessment")
    assert ("Degraded: the assessment phase overran its grant and fell back to "
            "anchor_validated. It is not an approval.\n") in payload.body


# Review round 1.

@pytest.mark.parametrize("strategy", ["panel", "debate"])
def test_no_output_assessor_budget_stop_falls_back_and_replays(capture, tmp_path, monkeypatch,
                                                               strategy):
    # The nare error/budget shape with no output: the artifact status is "failed" while the
    # outcome is a measured partial/budget overrun.
    import asyncio

    from test_panel import install

    from scrutare.engine.strategy import run_review
    from scrutare.replay.audit import replay_run
    config, claim = assessed_capture(capture, strategy=strategy, category="style")
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(run_review(capture, config, runtime=phase_runtime(
        tmp_path, {"assessment": {"input": 120, "shape": "stop"}})))
    assert result.verdict.rule == "phase_overrun_fallback"
    assessment = json.loads((capture / "assessment.json").read_bytes())
    assert assessment["status"] == "failed"
    assert (assessment["outcome"]["status"], assessment["outcome"]["reason"]) == (
        "partial", "budget")
    assert replay_run(capture).saved_identical


def test_no_output_reassessment_budget_stop_falls_back_and_replays(capture, tmp_path,
                                                                   monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = discussion_capture(capture, discussion="fallback")
    install(monkeypatch, {"security": (claim,)})
    plan = {"assessment": {"status": "unresolved"},
            "reassessment": {"input": 200, "shape": "stop"}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert result.verdict.to_dict()["degraded"]["phase"] == "discussion"
    assert json.loads((capture / "assessment-round-0001.json").read_bytes())["status"] == "failed"
    assert replay_run(capture).saved_identical


def test_phase_allowance_survives_discovery_overshoot():
    from scrutare.engine.session_models import TokenUsage
    ledger = phase_ledger()  # a, b: 50 each; assessor 40; discussion 60; ceiling 200
    a, b = ledger.admit("a", "a/1"), ledger.admit("b", "b/1")
    # Discovery ends 80 past its grants: more than the assessor's unspent reservation.
    ledger.settle(a, TokenUsage(input=90), True)
    ledger.settle(b, TokenUsage(input=90), True)
    (assessor,) = (ledger.admit("assessor", "assessor/1"),)
    assert assessor.allocated_tokens == 40
    ledger.settle(assessor, TokenUsage(input=40), True)
    leases = ledger.admit_phase("discussion", (("a", "a/3"), ("b", "b/3")))
    assert [lease.allocated_tokens for lease in leases] == [30, 30]


def test_discovery_overshoot_cannot_starve_the_cell_allowances(capture, tmp_path, monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = allowance_capture(capture, monkeypatch)
    # Each reviewer ends 8,500 past its 12,500 grant: 34,000 in all, beyond the unspent
    # assessor reservation, so the ceiling alone would shrink both reserved pools.
    install(monkeypatch, {"security": (claim,)}, initial_usage=21000)
    plan = {"assessment": {"counter": True}, "position": {"input": 6960}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert result.verdict is not None
    assert json.loads((capture / "assessment.json").read_bytes())["outcome"][
        "allocated_tokens"] == 30000
    trace = json.loads((capture / "debate.json").read_bytes())
    assert [p["allocated_tokens"] for p in trace["rounds"][0]["positions"]] == [7500] * 4
    assert replay_run(capture).saved_identical


def test_overshoot_protection_is_opt_in():
    # Without any new setting, discovery overshoot still reduces later grants as before.
    from scrutare.engine.budgets import ReviewBudgetLedger
    from scrutare.engine.session_models import TokenUsage
    settings = replace(configuration().budgets, per_persona_tokens=100, review_max_tokens=140)
    ledger = ReviewBudgetLedger(("a", "assessor"), settings, reserved_allocations={"assessor": 40})
    a = ledger.admit("a", "a/1")
    ledger.settle(a, TokenUsage(input=130), True)
    assert ledger.admit("assessor", "assessor/1").allocated_tokens == 10


@pytest.mark.parametrize("forgery", ["native", "accounting", "extra_position"])
def test_replay_checks_native_evidence_for_a_position_fallback(capture, tmp_path, monkeypatch,
                                                               forgery):
    import asyncio

    from test_review_inputs import save_json

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, _ = cell_capture(capture, monkeypatch, category="style")
    plan = {"assessment": {"counter": True}, "position": STOPPED}
    asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert replay_run(capture).saved_identical
    trace = json.loads((capture / "debate.json").read_bytes())
    if forgery == "native":
        # The native session says the position finished well inside its grant.
        path = capture / "sessions/devops/attempt-0003/session.json"
        session = json.loads(path.read_bytes())
        session.update(status="done", stop_reason="end_turn",
                       usage={"input": 4, "output": 2, "cache_read": 3, "cache_write": 1})
        save_json(path, session)
    elif forgery == "accounting":
        trace["ledger"]["accounting_complete"] = False
        save_json(capture / "debate.json", trace)
    else:
        trace["rounds"][0]["positions"].append("not an outcome")
        save_json(capture / "debate.json", trace)
    with pytest.raises(ReplayError):
        replay_run(capture)


@pytest.mark.parametrize("forgery", ["native", "accounting"])
def test_replay_checks_native_evidence_for_an_assessor_fallback(capture, tmp_path, monkeypatch,
                                                                forgery):
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
    if forgery == "native":
        path = capture / "sessions/evidence-assessor/attempt-0001/session.json"
        session = json.loads(path.read_bytes())
        session["usage"] = {"input": 4, "output": 2, "cache_read": 3, "cache_write": 1}
        save_json(path, session)
    else:
        panel = json.loads((capture / "panel.json").read_bytes())
        panel["ledger"]["accounting_complete"] = False
        save_json(capture / "panel.json", panel)
    with pytest.raises(ReplayError):
        replay_run(capture)


def test_degraded_result_and_action_accept_the_fallback_rule():
    from scrutare.interfaces.action import ActionError, validate_success
    base = {"accounting_complete": True, "panel_status": "partial",
            "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0, "total": 2},
            "review": {"review_id": 7, "html_url":
                       "https://github.com/owner/repo/pull/12#pullrequestreview-7",
                       "commit_id": "a" * 40, "body": "x", "state": "COMMENTED",
                       "login": "scrutare[bot]"}}
    degraded = degradation("discussion").to_dict()
    validate_success(base | {"rule": "phase_overrun_fallback", "degraded": degraded},
                     "escalated", "a" * 40, "owner/repo", 12)
    with pytest.raises(ActionError):
        validate_success(base | {"rule": "phase_overrun_fallback"}, "escalated", "a" * 40,
                         "owner/repo", 12)
    from scrutare.engine.review import ReviewRunResult
    from scrutare.engine.session_models import TokenUsage
    result = ReviewRunResult(Path("/run"), "a" * 40, "escalated", "phase_overrun_fallback",
                             "partial", TokenUsage(), True, None, degraded=degraded)
    assert result.to_dict()["degraded"] == degraded
    plain = ReviewRunResult(Path("/run"), "a" * 40, "approve", "no_blocking_findings",
                            "complete", TokenUsage(), True, None)
    assert "degraded" not in plain.to_dict()



# Review round 2.

def test_reassessment_refused_by_a_spent_allowance_falls_back(capture, tmp_path, monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    config, claim = discussion_capture(capture, discussion="fallback", tokens=99, rounds=1)
    install(monkeypatch, {"security": (claim,)})
    plan = {"assessment": {"status": "unresolved"}, "position": {"fill": True}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert (result.verdict.verdict, result.verdict.rule) == (
        "escalated", "unresolved_semantic_assessment")
    assert result.verdict.to_dict()["degraded"] == {
        "phase": "discussion", "round": 1, "allowance_tokens": 99, "grant_tokens": 0,
        "usage_tokens": 0, "overrun_tokens": 0, "fallback_stage": "pre_round_assessment"}
    trace = json.loads((capture / "debate.json").read_bytes())
    assert trace["rounds"][0]["admission"] == "refused"
    assert trace["reason"] == "discussion_overrun_fallback"
    assert replay_run(capture).saved_identical


def test_reassessment_refused_by_a_spent_allowance_still_voids_by_default(capture, tmp_path,
                                                                          monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    config, claim = discussion_capture(capture, tokens=99, rounds=1)
    install(monkeypatch, {"security": (claim,)})
    plan = {"assessment": {"status": "unresolved"}, "position": {"fill": True}}
    result = asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    assert result.verdict is None
    trace = json.loads((capture / "debate.json").read_bytes())
    assert trace["reason"] == "reassessment_incomplete" and "admission" not in trace["rounds"][0]


def refused_second_round(capture, tmp_path, monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate
    config, claim = discussion_capture(capture, discussion="fallback", rounds=2)
    install(monkeypatch, {"security": (claim,)})
    # Round-1 positions spend each reviewer's whole remaining grant (reused grants).
    plan = {"assessment": {"status": "unresolved"}, "reassessment": {"status": "unresolved"},
            "position": {"fill": True}}
    return asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))


def test_a_later_round_refused_for_spent_grants_keeps_the_completed_round(capture, tmp_path,
                                                                          monkeypatch):
    from scrutare.replay.audit import replay_run
    result = refused_second_round(capture, tmp_path, monkeypatch)
    assert result.verdict.to_dict()["degraded"]["round"] == 2
    assert result.verdict.exhaustion is None
    trace = json.loads((capture / "debate.json").read_bytes())
    assert len(trace["rounds"]) == 2
    assert trace["rounds"][1]["admission"] == "refused"
    assert trace["rounds"][1]["positions"] == []
    assert replay_run(capture).saved_identical


def test_replay_refuses_an_unjustified_refusal(capture, tmp_path, monkeypatch):
    from test_review_inputs import save_json

    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    refused_second_round(capture, tmp_path, monkeypatch)
    trace = json.loads((capture / "debate.json").read_bytes())
    for persona in trace["ledger"]["personas"]:
        persona["usage"]["total"] = 0  # every reviewer still had grant left
    save_json(capture / "debate.json", trace)
    with pytest.raises(ReplayError):
        replay_run(capture)


def test_action_accepts_escalations_for_unresolved_claims():
    from scrutare.interfaces.action import validate_success
    base = {"accounting_complete": True, "panel_status": "partial",
            "usage": {"input": 1, "output": 1, "cache_read": 0, "cache_write": 0, "total": 2},
            "review": {"review_id": 7, "html_url":
                       "https://github.com/owner/repo/pull/12#pullrequestreview-7",
                       "commit_id": "a" * 40, "body": "x", "state": "COMMENTED",
                       "login": "scrutare[bot]"}}
    validate_success(base | {"rule": "unresolved_semantic_assessment"}, "escalated",
                     "a" * 40, "owner/repo", 12)


def test_replay_requires_the_exact_allowance_split(capture, tmp_path, monkeypatch):
    import asyncio

    from test_review_inputs import save_json

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, _ = allowance_capture(capture, monkeypatch)
    plan = {"assessment": {"counter": True}, "position": {"input": 6960}}
    asyncio.run(run_debate(capture, config, runtime=phase_runtime(tmp_path, plan)))
    trace = json.loads((capture / "debate.json").read_bytes())
    for position in trace["rounds"][0]["positions"]:
        position["allocated_tokens"] = 7000
    save_json(capture / "debate.json", trace)
    with pytest.raises(ReplayError):
        replay_run(capture)


@pytest.mark.parametrize("strategy", ["panel", "debate"])
@pytest.mark.parametrize("tamper", ["reason", "degraded", "assessor_usage", "rows"])
def test_replay_checks_the_panel_record_of_an_assessor_fallback(capture, tmp_path, monkeypatch,
                                                                strategy, tamper):
    import asyncio

    from test_panel import install
    from test_review_inputs import save_json

    from scrutare.engine.strategy import run_review
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, claim = assessed_capture(capture, strategy=strategy, category="style")
    install(monkeypatch, {"security": (claim,)})
    asyncio.run(run_review(capture, config, runtime=phase_runtime(
        tmp_path, {"assessment": {"input": 120}})))
    assert replay_run(capture).saved_identical
    assessment = json.loads((capture / "assessment.json").read_bytes())
    # The overrun assessor's output is never kept as dispositions.
    assert assessment["assessments"] == []
    panel = json.loads((capture / "panel.json").read_bytes())
    if tamper == "reason":
        panel["reason"] = "converged"
    elif tamper == "degraded":
        panel["degraded"]["grant_tokens"] += 1
    elif tamper == "assessor_usage":
        assessor = next(p for p in panel["ledger"]["personas"]
                        if p["persona"] == "evidence-assessor")
        assessor["usage"]["total"] += 1
    else:
        reference = {k: v for k, v in claim.evidence.citations[0].__dict__.items()}
        assessment["assessments"] = [{"candidate_id": "one", "status": "supported",
                                      "reason": "r", "supporting_citations": [reference],
                                      "counter_citations": []}]
        save_json(capture / "assessment.json", assessment)
    save_json(capture / "panel.json", panel)
    with pytest.raises(ReplayError):
        replay_run(capture)
