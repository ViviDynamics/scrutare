"""Panel scheduling and survivor rules using real capture, ledger and findings code."""

import asyncio
import importlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest
from test_fanout import configure
from test_review_inputs import capture as capture

from scrutare.engine.session_models import (
    NareCapability,
    NareRuntime,
    ReanchorOutcome,
    SessionOutcome,
    TokenUsage,
)
from scrutare.findings import Anchor, Finding
from scrutare.findings.verification import ReanchorCorrection


def modules():
    strategy = importlib.import_module("scrutare.engine.strategy")
    panel = importlib.import_module("scrutare.engine.panel")
    wave = importlib.import_module("scrutare.engine.fanout")
    return strategy, panel, wave


def finding(persona="security", *, line=1, category="security", problem="Problem", reason="Risk",
            file="src/app.py"):
    return Finding(Anchor(file, line, "RIGHT"), category, problem, reason, persona)


def install(monkeypatch, initial, corrections=None, *, initial_status="complete",
            initial_available=True, initial_usage=10, correction_status="complete",
            correction_available=True, correction_usage=5, confident=True, mutate=None):
    strategy, panel, wave = modules()
    calls, reservations, ledgers = [], [], []

    async def inspect(runtime):
        calls.append("inspect")
        return NareCapability("2026.10.0", 1)

    async def review(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        calls.append(("review", lease.persona))
        ledgers.append(ledger)
        await asyncio.sleep(0.01 if lease.persona == "security" else 0)
        usage = TokenUsage(initial_usage)
        ledger.settle(lease, usage, confident)
        return SessionOutcome(lease.persona, initial_status,
                              "budget" if initial_status == "partial" else "done",
                              initial.get(lease.persona, ()), initial_available, usage, confident,
                              lease.allocated_tokens, max(0, usage.total - lease.allocated_tokens),
                              lease.persona, artifact_directory, lease.limit_tokens)

    async def correct(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        calls.append(("reanchor", lease.persona))
        ledgers.append(ledger)
        reservations.append(ledger.snapshot()["active_reservations"])
        assert lease.session_key == f"{lease.persona}/attempt-0002"
        assert lease.baseline == TokenUsage()
        assert artifact_directory.name == "attempt-0002"
        assert kwargs["capability"] == NareCapability("2026.10.0", 1)
        assert rail.model == "default-model"
        assert descriptor.inputs.root.name == "review-inputs"
        await asyncio.sleep(0.01 if lease.persona == "security" else 0)
        values = corrections(descriptor) if corrections else ()
        usage = TokenUsage(correction_usage)
        complete = correction_status != "uncertain"
        ledger.settle(lease, usage, complete)
        if mutate:
            mutate(descriptor.inputs.root.parent)
        return ReanchorOutcome(
            lease.persona, "failed" if not complete else correction_status,
            "budget" if correction_status == "partial" else "done", values,
            correction_available, usage, complete, lease.allocated_tokens,
            max(0, usage.total - lease.allocated_tokens), lease.persona, artifact_directory,
            lease.limit_tokens,
        )

    monkeypatch.setattr(wave, "inspect_nare_runtime", inspect)
    monkeypatch.setattr(wave, "run_persona_session", review)
    monkeypatch.setattr(panel, "run_reanchor_session", correct)
    return strategy, calls, reservations, ledgers


def run(strategy, capture, config):
    return asyncio.run(strategy.run_review(capture, config, runtime=NareRuntime(capture / "nare")))


@pytest.mark.parametrize("name", ["iterative", "debate"])
def test_unsupported_dispatch_has_no_preparation_runtime_or_writes(capture, name):
    strategy, _, _ = modules()
    config = replace(configure(capture), strategy=name)
    before = {p: p.read_bytes() for p in capture.iterdir()}
    with pytest.raises(strategy.StrategyNotImplementedError, match="not yet implemented"):
        run(strategy, capture, config)
    assert {p: p.read_bytes() for p in capture.iterdir()} == before


@pytest.mark.parametrize("rounds", [1, 9])
@pytest.mark.parametrize("category,verdict", [
    ("security", "changes_requested"), ("style", "approve"),
])
def test_one_ordered_wave_dedupe_conflicts_and_exact_final_bytes(
        capture, monkeypatch, rounds, category, verdict):
    config = configure(capture, personas=["security", "devops"], per=100, review=200)
    config = replace(config, rounds=replace(config.rounds, max=rounds))
    (capture / "config.yaml").write_text(json.dumps(config.to_dict()))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    first = finding(category=category, problem=" Same  problem ")
    second = finding("devops", category="docs", problem="Same problem", reason="Other risk")
    strategy, calls, _, _ = install(monkeypatch, {"security": (first, first), "devops": (second,)})
    result = run(strategy, capture, config)
    assert result.status == "complete" and result.verdict.verdict == verdict
    assert result.verdict.exhaustion is None
    assert result.verdict.findings[0].sources == (first, first, second)
    assert result.usage.total == 20 and result.accounting_complete
    assert calls == ["inspect", ("review", "security"), ("review", "devops")]
    assert result.corrections == ()
    assert (capture / "verdict.json").read_bytes() == result.verdict.to_bytes()
    assert json.loads((capture / "findings.json").read_bytes()) == [
        result.verdict.findings[0].to_dict()]
    manifest = json.loads((capture / "panel.json").read_bytes())
    assert manifest["convergence_passes"] == 1 and manifest["strategy"] == "panel"
    assert manifest["head_sha"] == "abc123"
    assert manifest["ledger"]["usage"]["total"] == 20
    assert not (capture / "escalation.json").exists()
    with pytest.raises(FrozenInstanceError):
        result.status = "failed"


@pytest.mark.parametrize("status,available,confident", [
    ("failed", True, True), ("partial", False, True), ("complete", False, True),
    ("complete", True, False),
])
def test_fatal_initial_never_spends_on_corrections_or_publishes_verdict(
        capture, monkeypatch, status, available, confident):
    config = configure(capture, personas=["security"])
    strategy, calls, _, _ = install(monkeypatch, {"security": (finding(line=999),)},
                                    initial_status=status, initial_available=available,
                                    confident=confident)
    result = run(strategy, capture, config)
    assert result.status == "failed" and result.verdict is None
    assert result.initial.outcomes[0].findings == (finding(line=999),)
    assert calls == ["inspect", ("review", "security")]
    assert not (capture / "verdict.json").exists()
    assert json.loads((capture / "panel.json").read_bytes())["status"] == "failed"


def test_unadmitted_initial_is_fatal_even_with_valid_sibling(capture, monkeypatch):
    config = configure(capture, personas=["security", "devops"], review=1)
    strategy, calls, _, _ = install(monkeypatch, {"security": (finding(),)}, initial_usage=0)
    result = run(strategy, capture, config)
    assert result.status == "failed" and result.verdict is None
    assert [o.status for o in result.initial.outcomes] == ["complete", "not_started"]
    assert calls == ["inspect", ("review", "security")]


@pytest.mark.parametrize("empty", [True, False])
def test_valid_partial_initial_document_counts_even_if_empty(capture, monkeypatch, empty):
    config = configure(capture, personas=["security"])
    strategy, _, _, _ = install(monkeypatch, {"security": () if empty else (finding(),)},
                                initial_status="partial")
    result = run(strategy, capture, config)
    assert result.status == "partial"
    assert result.verdict.verdict == ("approve" if empty else "changes_requested")


def test_corrections_reserve_one_wave_and_preserve_duplicate_sources(capture, monkeypatch):
    config = configure(capture, personas=["security", "devops"], review=200, per=100)
    original = finding(line=999)
    other = finding(line=999, reason="Distinct original")
    def corrected(descriptor):
        assert [r.request_id for r in descriptor.requests] == (
            ["r0001", "r0002"] if descriptor.persona.name == "security" else ["r0001"])
        return tuple(ReanchorCorrection(r.original, Anchor("src/app.py", 1, "RIGHT"))
                     for r in reversed(descriptor.requests))
    strategy, calls, reservations, ledgers = install(monkeypatch, {
        "security": (original, original, other), "devops": (finding("devops", line=77),)
    }, corrected)
    result = run(strategy, capture, config)
    assert result.status == "complete"
    assert len(result.verdict.findings) == 1
    sources = result.verdict.findings[0].sources
    assert [s.reason for s in sources] == ["Risk", "Risk", "Distinct original", "Risk"]
    assert [s.persona for s in sources] == ["security", "security", "security", "devops"]
    assert [o.persona for o in result.corrections] == ["security", "devops"]
    assert calls == ["inspect", ("review", "security"), ("review", "devops"),
                     ("reanchor", "security"), ("reanchor", "devops")]
    assert all(len(reservation) == 2 for reservation in reservations)
    assert all(ledger is ledgers[0] for ledger in ledgers)
    assert result.usage.total == 30 and result.initial.usage.total == 20
    manifest = json.loads((capture / "panel.json").read_bytes())
    assert manifest["corrections"][0]["requests"][0]["original"]["reason"] == "Risk"
    assert json.loads((capture / "fanout.json").read_bytes())["ledger"]["usage"]["total"] == 20


@pytest.mark.parametrize("kind,reason", [("missing", "missing_correction"),
                                        ("invalid", "invalid_correction"),
                                        ("excluded", "invalid_correction")])
def test_missing_invalid_and_excluded_corrections_drop_once(capture, monkeypatch, kind, reason):
    config = configure(capture, personas=["security"])
    def corrected(descriptor):
        if kind == "missing":
            return ()
        return (ReanchorCorrection(descriptor.requests[0].original,
                Anchor("docs/secret.md" if kind == "excluded" else "src/app.py",
                       1 if kind == "excluded" else 777, "RIGHT")),)
    original = finding(line=999)
    strategy, calls, _, _ = install(monkeypatch, {"security": (original, original)}, corrected)
    result = run(strategy, capture, config)
    assert result.verdict.verdict == "approve"
    assert [d.reason for d in result.verification.dropped] == [reason, reason]
    assert len(calls) == 3


@pytest.mark.parametrize("status,available,expected", [
    ("partial", False, "partial"), ("partial", True, "partial"),
    ("failed", False, "failed"), ("uncertain", False, "failed"),
    ("complete", False, "failed"),
])
def test_budget_stopped_missing_correction_drops_but_execution_failure_is_fatal(
        capture, monkeypatch, status, available, expected):
    config = configure(capture, personas=["security"])
    strategy, _, _, _ = install(monkeypatch, {"security": (finding(), finding(line=999))},
                                correction_status=status, correction_available=available)
    result = run(strategy, capture, config)
    assert result.status == expected
    if expected == "failed":
        assert result.verdict is None and not (capture / "verdict.json").exists()
    else:
        assert result.verdict.verdict == "changes_requested"
        assert result.verification.dropped[0].reason == "missing_correction"


@pytest.mark.parametrize("usage,overshoot", [(40, 0), (45, 5)])
def test_exhaustion_denies_correction_and_preserves_actual_overshoot(
        capture, monkeypatch, usage, overshoot):
    config = configure(capture, personas=["security"], per=40, review=40)
    strategy, calls, _, _ = install(monkeypatch, {"security": (finding(line=999),)},
                                    initial_usage=usage, initial_status="partial")
    result = run(strategy, capture, config)
    assert result.status == "partial" and result.verdict.verdict == "approve"
    assert result.corrections[0].status == "not_started"
    assert result.usage.total == usage
    manifest = json.loads((capture / "panel.json").read_bytes())
    assert manifest["ledger"]["overshoot_tokens"] == overshoot
    assert calls == ["inspect", ("review", "security")]


def test_revalidate_original_config_binding_before_publication(capture, monkeypatch):
    config = configure(capture, personas=["security"])
    def mutate(run):
        changed = replace(config, rounds=replace(config.rounds, max=99))
        (run / "config.yaml").write_text(json.dumps(changed.to_dict()))
        (run / "config.json").write_text(json.dumps(changed.to_dict()))
        from hashlib import sha256

        from scrutare.engine.review_inputs import _encoded
        path = run / "effective-files.json"
        manifest = json.loads(path.read_bytes())
        manifest["config_sha256"] = sha256(_encoded(changed.to_dict())).hexdigest()
        path.write_bytes(_encoded(manifest))
    strategy, _, _, _ = install(monkeypatch, {"security": (finding(line=999),)}, mutate=mutate)
    result = run(strategy, capture, config)
    assert result.status == "failed" and result.verdict is None
    assert not (capture / "verdict.json").exists()
    assert json.loads((capture / "panel.json").read_bytes())["reason"] == "inputs"


def test_coherent_capture_diff_rewrite_cannot_change_original_input_binding(capture, monkeypatch):
    config = configure(capture, personas=["security"])
    def mutate(run):
        for path in (run / "diff.patch", run / "review-inputs" / "diff.patch"):
            path.write_bytes(path.read_bytes().replace(b"+new", b"+changed"))
    strategy, _, _, _ = install(monkeypatch, {"security": (finding(line=999),)}, mutate=mutate)
    result = run(strategy, capture, config)
    assert result.status == "failed" and result.verdict is None
    assert json.loads((capture / "panel.json").read_bytes())["reason"] == "inputs"


def test_initial_validation_exception_retains_safe_failed_panel_diagnostic(capture, monkeypatch):
    from scrutare.engine.review_inputs import ReviewInputError

    config = configure(capture, personas=["security"])
    strategy, panel, _ = modules()
    install(monkeypatch, {})
    original = panel._run_initial_wave
    async def tampered(context):
        await original(context)
        (context.inputs.root / "diff.patch").write_bytes(b"invalid")
        raise ReviewInputError("invalid saved inputs")
    monkeypatch.setattr(panel, "_run_initial_wave", tampered)
    with pytest.raises(ReviewInputError):
        run(strategy, capture, config)
    manifest = json.loads((capture / "panel.json").read_bytes())
    assert manifest["status"] == "failed" and manifest["reason"] == "initial_evidence"
    assert manifest["ledger"]["usage"]["total"] == 10
    assert not (capture / "verdict.json").exists()


def test_valid_partial_correction_retains_reanchored_evidence_and_shared_overshoot(
        capture, monkeypatch):
    config = configure(capture, personas=["security"], per=40, review=40)
    original = finding(line=999)
    def corrected(descriptor):
        return (ReanchorCorrection(descriptor.requests[0].original,
                                   Anchor("src/app.py", 1, "RIGHT")),)
    strategy, _, _, _ = install(monkeypatch, {"security": (original,)}, corrected,
                                correction_status="partial", correction_usage=35)
    result = run(strategy, capture, config)
    assert result.status == "partial" and result.verdict.verdict == "changes_requested"
    expected = replace(original, anchor=Anchor("src/app.py", 1))
    assert result.verdict.findings[0].sources == (expected,)
    assert result.initial.usage.total == 10 and result.usage.total == 45
    manifest = json.loads((capture / "panel.json").read_bytes())
    assert manifest["ledger"]["overshoot_tokens"] == 5
    assert manifest["corrections"][0]["result"]["allocated_tokens"] == 30


def test_correction_cancellation_awaits_all_admitted_cleanup(capture, monkeypatch):
    config = configure(capture, personas=["security", "devops"])
    strategy, panel, _ = modules()
    install(monkeypatch, {"security": (finding(line=999),),
                          "devops": (finding("devops", line=999),)})
    started, reaped = [], []
    async def correction(descriptor, rail, lease, *, ledger, **kwargs):
        started.append(lease.persona)
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0.01)
            ledger.settle(lease, TokenUsage(), False)
            reaped.append(lease.persona)
    monkeypatch.setattr(panel, "run_reanchor_session", correction)
    async def scenario():
        task = asyncio.create_task(strategy.run_review(capture, config,
                                                      runtime=NareRuntime(capture / "nare")))
        while len(started) != 2:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert sorted(reaped) == ["devops", "security"]
    assert not (capture / "verdict.json").exists()


def test_initial_and_correction_real_subprocess_artifacts_share_budget(capture, tmp_path):
    from test_nare_session import running_executable

    config = configure(capture, personas=["security"], per=100, review=100)
    path = running_executable(tmp_path, input_tokens=54)
    script = path.read_text().replace('"input": 54',
                                     '"input": (14 if a.prompt.startswith("Correct") else 54)')
    script = script.replace("{'findings': []}",
                            "({'corrections': [{'request_id': 'r0001', 'file': 'src/app.py', "
                            "'line': 1, 'side': 'RIGHT'}]} if a.prompt.startswith('Correct') "
                            "else {'findings': [{'file': 'src/app.py', 'line': 999, "
                            "'side': 'RIGHT', 'category': 'security', 'problem': 'Problem', "
                            "'reason': 'Risk'}]})")
    path.write_text(script)
    strategy, _, _ = modules()
    result = asyncio.run(strategy.run_review(capture, config, runtime=NareRuntime(path)))
    assert result.status == "complete" and result.verdict.verdict == "changes_requested"
    assert result.initial.usage.total == 60 and result.usage.total == 80
    assert result.corrections[0].allocated_tokens == 40
    for number in (1, 2):
        attempt = capture / "sessions" / "security" / f"attempt-{number:04d}"
        invocation = json.loads((attempt / "invocation.json").read_bytes())
        outcome = json.loads((attempt / "result.json").read_bytes())
        assert outcome["status"] == "complete"
        if number == 2:
            assert invocation["purpose"] == "reanchor"
            assert invocation["requests"][0]["request_id"] == "r0001"
    from scrutare.replay import replay_run
    assert replay_run(capture).saved_identical is True


def test_correction_evidence_failure_records_failure_and_releases_unlaunched_leases(
        capture, monkeypatch):
    from scrutare.engine.session_artifacts import SessionArtifactError

    config = configure(capture, personas=["security", "devops"], per=40, review=80)
    strategy, panel, wave = modules()
    _, calls, _, ledgers = install(monkeypatch, {
        "security": (finding(line=999),), "devops": (finding("devops", line=999),),
    })
    original = wave.run_persona_session
    async def review(descriptor, rail, lease, *, ledger, **kwargs):
        if lease.persona == "security":
            ledger.observe(lease, TokenUsage(40))
            ledger.settle(lease, TokenUsage(40), True)
            return SessionOutcome(lease.persona, "complete", "done", (finding(line=999),), True,
                                  TokenUsage(40), True, 40, 0, "s",
                                  kwargs["artifact_directory"], 40)
        return await original(descriptor, rail, lease, ledger=ledger, **kwargs)
    def fail(*args, **kwargs):
        raise SessionArtifactError("disk failure")
    monkeypatch.setattr(wave, "run_persona_session", review)
    monkeypatch.setattr(panel, "write_owned_json", fail)
    result = run(strategy, capture, config)
    assert result.status == "failed" and result.verdict is None
    assert result.usage.total == 50
    assert ledgers[0].snapshot()["active_reservations"] == []
    assert not any(call[0] == "reanchor" for call in calls if isinstance(call, tuple))
    assert json.loads((capture / "panel.json").read_bytes())["reason"] == "artifacts"
