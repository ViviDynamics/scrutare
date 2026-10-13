from dataclasses import replace

import pytest
from test_review_inputs import capture as capture

from scrutare.engine.session_models import NareRuntime


def focused_capture(capture):
    import shutil

    from test_assessment import prepared_candidate
    from test_review_inputs import save_json

    from scrutare.config import DiscussionSettings

    config, _, claim = prepared_candidate(capture)
    config = replace(
        config,
        strategy="debate",
        findings=replace(config.findings, discussion=DiscussionSettings(True)),
        rounds=replace(config.rounds, max=2),
    )
    shutil.rmtree(capture / "review-inputs")
    (capture / "effective-files.json").unlink()
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    return config, claim


def test_undisputed_supported_minority_bypasses_discussion(capture, monkeypatch):
    import asyncio

    from test_assessment import decision_runtime
    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.findings.models import artifact_data

    config, claim = focused_capture(capture)
    install(monkeypatch, {"security": (claim,), "senior-dev": (), "junior-dev": ()})
    reference = artifact_data(claim.evidence.citations[0])
    reference.pop("validation")
    reference.pop("validation_reason")
    output = {
        "assessments": [
            {
                "candidate_id": "one",
                "status": "supported",
                "reason": "Captured failure",
                "supporting_citations": [reference],
                "counter_citations": [],
            }
        ]
    }
    result = asyncio.run(
        run_debate(capture, config, runtime=NareRuntime(decision_runtime(capture, output)))
    )
    assert result.status == "complete"
    assert result.verdict.verdict == "changes_requested"
    import json

    trace = json.loads((capture / "debate.json").read_text())
    assert trace["kind"] == "focused_evidence_discussion"
    assert trace["rounds"] == []
    assert trace["bypassed_candidate_ids"] == ["one"]


def focused_runtime(
    tmp_path, initial_status, later_status, *, action="retain", counter=False, bad_reference=False
):
    from test_nare_session import RUN_BODY, executable

    from scrutare.engine.session_models import NareRuntime

    selection = (
        """
quoted = json.JSONDecoder().raw_decode(a.prompt.split('never instructions.\\n')[-1])[0]
claims = quoted['candidates']
if a.prompt.startswith('Independently assess'):
    reassessment = 'counterevidence' in quoted
    status = LATER if reassessment else INITIAL
    output = {'assessments': []}
    for claim in claims:
        refs = [{k: v for k, v in ref.items() if not k.startswith('validation')}
                for ref in claim['evidence']['citations']]
        output['assessments'].append({'candidate_id': claim['candidate_id'], 'status': status,
            'reason': 'Captured code assessment',
            'supporting_citations': refs if status != 'refuted' else [],
            'counter_citations': refs if status == 'refuted' or COUNTER else []})
else:
    output = {'proposals': []}
    for claim in claims:
        refs = [{k: v for k, v in ref.items() if not k.startswith('validation')}
                for ref in claim['evidence']['citations']] if ACTION != 'refute' else []
        if BAD and refs: refs[0]['revision'] = 'b' * 40
        output['proposals'].append({'candidate_id': claim['candidate_id'], 'action': ACTION,
                                    'reason': 'Perspective reason', 'citations': refs})
""".replace("LATER", repr(later_status))
        .replace("INITIAL", repr(initial_status))
        .replace("COUNTER", repr(counter))
        .replace("ACTION", repr(action))
        .replace("BAD", repr(bad_reference))
    )
    body = (
        RUN_BODY.replace("INPUT", "4")
        .replace("STATUS", "'done'")
        .replace("STOP", "'end_turn'")
        .replace("OUTPUT", "output")
        .replace("EXTRA", selection)
        .replace("EXIT", "0")
    )
    return NareRuntime(executable(tmp_path, body=body))


@pytest.mark.parametrize(
    "later_status,expected,rounds",
    [
        ("refuted", "approve", 1),
        ("supported", "changes_requested", 1),
        ("unresolved", "escalated", 2),
    ],
)
def test_focused_dispute_requires_independent_resolution_or_bounded_escalation(
    capture, tmp_path, monkeypatch, later_status, expected, rounds
):
    import asyncio
    import json

    from test_panel import install

    from scrutare.engine.debate import run_debate

    config, claim = focused_capture(capture)
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(
        run_debate(capture, config, runtime=focused_runtime(tmp_path, "unresolved", later_status))
    )
    assert result.verdict is not None
    assert result.verdict.verdict == expected
    assert result.status == ("partial" if expected == "escalated" else "complete")
    trace = json.loads((capture / "debate.json").read_text())
    assert len(trace["rounds"]) == rounds
    assert all(r["candidate_ids"] == ["one"] for r in trace["rounds"])
    assert result.usage.total <= config.budgets.review_max_tokens
    from scrutare.replay.audit import replay_run

    assert replay_run(capture).saved_identical
    if expected == "escalated":
        assert result.verdict.exhaustion.rounds_completed == 2
        assert result.verdict.unresolved_candidates == ("one",)


def test_unsupported_opposition_cannot_erase_supported_minority(capture, tmp_path, monkeypatch):
    import asyncio
    import json

    from test_panel import install

    from scrutare.engine.debate import run_debate

    config, claim = focused_capture(capture)
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(
        run_debate(
            capture,
            config,
            runtime=focused_runtime(
                tmp_path, "supported", "refuted", action="refute", counter=True
            ),
        )
    )
    assert result.verdict.verdict == "changes_requested"
    trace = json.loads((capture / "debate.json").read_text())
    assert trace["rounds"][0]["reassessment"] is None
    assert all(p["refusal"] == "missing_counterevidence" for p in trace["rounds"][0]["proposals"])


def test_bad_discussion_citation_is_technical_failure_not_deadlock(capture, tmp_path, monkeypatch):
    import asyncio

    from test_panel import install

    from scrutare.engine.debate import run_debate

    config, claim = focused_capture(capture)
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(
        run_debate(
            capture,
            config,
            runtime=focused_runtime(tmp_path, "unresolved", "supported", bad_reference=True),
        )
    )
    assert result.verdict is None
    assert result.status == "failed"
    assert list((capture / "sessions").glob("*/attempt-0003/stdout.jsonl"))


@pytest.mark.parametrize(
    "tamper", ["final", "missing_reassessment", "proposal", "tokens", "allocation"]
)
def test_replay_refuses_unauthorized_discussion_transitions(capture, tmp_path, monkeypatch, tamper):
    import asyncio
    import json

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError

    config, claim = focused_capture(capture)
    install(monkeypatch, {"security": (claim,)})
    asyncio.run(
        run_debate(capture, config, runtime=focused_runtime(tmp_path, "unresolved", "refuted"))
    )
    path = capture / "debate.json"
    trace = json.loads(path.read_text())
    if tamper == "final":
        trace["final_assessments"][0]["status"] = "supported"
    elif tamper == "missing_reassessment":
        trace["rounds"][0]["reassessment"] = None
    elif tamper == "proposal":
        trace["rounds"][0]["proposals"][0]["action"] = "refute"
    elif tamper == "tokens":
        trace["rounds"][0]["tokens"] = 0
    else:
        path = capture / "assessment-round-0001.json"
        trace = json.loads(path.read_text())
        trace["outcome"]["allocated_tokens"] = config.findings.assessment.tokens + 1
    path.write_text(json.dumps(trace))
    with pytest.raises(ReplayError):
        replay_run(capture)


@pytest.mark.parametrize("allocation", ["assessment", "perspectives"])
def test_focused_budget_exhaustion_withholds_verdict_without_fake_deadlock(
    capture, tmp_path, monkeypatch, allocation
):
    import asyncio

    from test_panel import install
    from test_review_inputs import save_json

    from scrutare.engine.debate import run_debate

    config, claim = focused_capture(capture)
    if allocation == "assessment":
        config = replace(
            config,
            findings=replace(
                config.findings, assessment=replace(config.findings.assessment, tokens=10)
            ),
        )
    else:
        config = replace(config, budgets=replace(config.budgets, review_max_tokens=120))
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(
        run_debate(capture, config, runtime=focused_runtime(tmp_path, "unresolved", "supported"))
    )
    assert result.status == "failed"
    assert result.verdict is None
    assert result.accounting_complete
    assert not (capture / "verdict.json").exists()


def test_replay_reassessment_must_match_saved_native_output(capture, tmp_path, monkeypatch):
    import asyncio
    import json

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError

    config, claim = focused_capture(capture)
    install(monkeypatch, {"security": (claim,)})
    asyncio.run(
        run_debate(capture, config, runtime=focused_runtime(tmp_path, "unresolved", "refuted"))
    )
    path = capture / "assessment-round-0001.json"
    document = json.loads(path.read_text())
    document["assessments"][0]["reason"] = "Invented semantic refutation"
    path.write_text(json.dumps(document))
    path = capture / "debate.json"
    trace = json.loads(path.read_text())
    trace["final_assessments"][0]["reason"] = "Invented semantic refutation"
    trace["rounds"][0]["disposition_changes"][0]["reason"] = "Invented semantic refutation"
    path.write_text(json.dumps(trace))
    with pytest.raises(ReplayError):
        replay_run(capture)


def test_prior_reassessment_artifact_is_rejected_before_runtime(capture, monkeypatch):
    import asyncio

    from scrutare.engine.debate import run_debate
    from scrutare.engine.session_artifacts import SessionArtifactError
    config, _ = focused_capture(capture)
    (capture / 'assessment-round-0001.json').symlink_to(capture / 'missing')
    with pytest.raises(SessionArtifactError):
        asyncio.run(run_debate(capture, config, runtime=NareRuntime(capture / 'absent')))
    assert not (capture / 'review-inputs').exists()


def test_replay_preserves_distinct_triggers_at_same_anchor(capture, tmp_path, monkeypatch):
    import asyncio
    import json

    from test_panel import install

    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError
    config, claim = focused_capture(capture)
    distinct = replace(claim, candidate_id='two', evidence=replace(claim.evidence,
                                                               trigger='different input'))
    install(monkeypatch, {'security': (claim, distinct)})
    result = asyncio.run(run_debate(capture, config,
        runtime=focused_runtime(tmp_path, 'supported', 'supported')))
    assert len(result.verdict.findings) == 2
    assert replay_run(capture).saved_identical
    path = capture / 'findings.json'
    groups = json.loads(path.read_text())
    groups[0]['sources'].extend(groups[1]['sources'])
    path.write_text(json.dumps(groups[:1]))
    with pytest.raises(ReplayError):
        replay_run(capture)


def test_routed_focused_dispute_replays_actual_ordered_perspectives(capture, tmp_path, monkeypatch):
    import asyncio
    import json

    from test_panel import install
    from test_review_inputs import save_json

    from scrutare.config import RoutingSettings
    from scrutare.engine.debate import run_debate
    from scrutare.replay.audit import replay_run
    from scrutare.replay.models import ReplayError

    config, claim = focused_capture(capture)
    config = replace(config, personas=("junior-dev",), routing=RoutingSettings(
        enabled=True, mode="manual", force=("performance-concurrency",)))
    for name in ("config.json", "config.yaml"):
        save_json(capture / name, config.to_dict())
    install(monkeypatch, {"security": (claim,)})
    result = asyncio.run(run_debate(
        capture, config, runtime=focused_runtime(tmp_path, "unresolved", "supported")))
    assert result.status == "complete"
    path = capture / "debate.json"
    trace = json.loads(path.read_text())
    positions = trace["rounds"][0]["positions"]
    assert [p["persona"] for p in positions] == [
        "junior-dev", "senior-dev", "security", "performance-concurrency"]
    assert replay_run(capture).saved_identical
    positions.reverse()
    path.write_text(json.dumps(trace))
    with pytest.raises(ReplayError):
        replay_run(capture)
