"""Debate invokes real lifecycle adapters with a shared pool and budget ledger."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_fanout import configure
from test_panel import finding, install
from test_review_inputs import capture as capture

from scrutare.engine.session_models import NareRuntime, SessionOutcome, TokenUsage


def settings(capture, *, rounds=2, per=100, review=200):
    config = configure(capture, personas=["security"], per=per, review=review)
    config = replace(config, strategy="debate", rounds=replace(config.rounds, max=rounds))
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config.to_dict()))
    return config


def execute(capture, config):
    from scrutare.engine.debate import run_debate

    return asyncio.run(run_debate(capture, config, runtime=NareRuntime(capture / "nare")))


def mock_debate(
    monkeypatch,
    *,
    converged=True,
    category="security",
    status="complete",
    available=True,
    confident=True,
    usage=5,
):
    from scrutare.engine import debate

    calls = []

    async def session(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        calls.append((descriptor, lease, ledger))
        data = {
            "findings": [
                {
                    "file": "src/app.py",
                    "line": 1,
                    "side": "RIGHT",
                    "category": category,
                    "problem": "Problem",
                    "reason": "Risk",
                }
            ]
        }
        if descriptor.chair:
            data["converged"] = converged
        candidates = descriptor.parse_output(data)
        (artifact_directory / "session.json").write_text(json.dumps({"output": data}))
        ledger.settle(lease, TokenUsage(usage), confident)
        return SessionOutcome(
            lease.persona,
            status,
            "done",
            candidates,
            available,
            TokenUsage(usage),
            confident,
            lease.allocated_tokens,
            0,
            "session",
            artifact_directory,
            lease.limit_tokens,
        )

    monkeypatch.setattr(debate, "run_persona_session", session)
    return calls


def test_debate_chair_sees_positions_and_downgrades_code_derived_verdict(capture, monkeypatch):
    config = settings(capture)
    install(monkeypatch, {"security": (finding(),)})
    calls = mock_debate(monkeypatch, category="style")
    result = execute(capture, config)
    assert result.status == "complete" and result.verdict.verdict == "approve"
    assert result.usage.total == 20
    assert len(calls) == 2 and calls[0][0].chair is False and calls[1][0].chair is True
    assert calls[1][0].pool == (finding(),)
    assert calls[1][0].positions[0]["persona"] == "security"
    assert calls[0][2] is calls[1][2]
    assert calls[1][1].persona == "debate-chair"
    assert result.verdict.findings[0].personas == ("security",)
    assert result.verdict.exhaustion is None
    from scrutare.replay import replay_run

    assert replay_run(capture).saved_identical


def test_deadlock_exhaustion_preserves_unresolved_pool_and_round_bound(capture, monkeypatch):
    config = settings(capture, rounds=2)
    install(monkeypatch, {"security": (finding(),)})
    calls = mock_debate(monkeypatch, converged=False)
    result = execute(capture, config)
    assert len(calls) == 4 and result.usage.total == 30
    assert result.verdict.verdict == "escalated"
    assert result.verdict.exhaustion.rounds_completed == 2
    assert result.verdict.exhaustion.strategy == "debate"
    assert result.verdict.findings[0].categories == ("security",)
    from scrutare.replay import replay_run

    assert replay_run(capture).saved_identical


@pytest.mark.parametrize(
    "status,available,confident",
    [
        ("failed", True, True),
        ("complete", False, True),
        ("complete", True, False),
    ],
)
def test_unusable_chair_never_approves_or_asserts_completed_rounds(
    capture, monkeypatch, status, available, confident
):
    config = settings(capture)
    install(monkeypatch, {"security": (finding(),)})
    calls = mock_debate(monkeypatch)
    from scrutare.engine import debate

    original = debate.run_persona_session

    async def broken(descriptor, *args, **kwargs):
        result = await original(descriptor, *args, **kwargs)
        return (
            replace(
                result, status=status, output_available=available, accounting_complete=confident
            )
            if descriptor.chair
            else result
        )

    monkeypatch.setattr(debate, "run_persona_session", broken)
    result = execute(capture, config)
    assert len(calls) == 2 and result.status == "failed" and result.verdict is None
    assert not (capture / "verdict.json").exists()


def test_exhausted_chair_quota_does_not_invent_deadlock_bound(capture, monkeypatch):
    config = settings(capture, rounds=3, per=15, review=30)
    install(monkeypatch, {"security": (finding(),)})
    calls = mock_debate(monkeypatch, converged=False)
    result = execute(capture, config)
    assert result.status == "failed" and result.verdict is None
    assert len(calls) == 2
