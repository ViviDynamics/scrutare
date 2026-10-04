"""Offline correction lifecycle and shared live accounting regressions."""

import asyncio
import json

import pytest
from test_nare_session import (
    HANG_BODY,
    RUN_BODY,
    alive,
    executable,
    fixture_pids,
    module,
    running_executable,
    setup,
)
from test_review_inputs import capture as capture

from scrutare.config import BudgetSettings, ModelRail
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.persona_inputs import PersonaReanchorInput
from scrutare.engine.reanchor import make_reanchor_requests
from scrutare.engine.session_artifacts import create_attempt_directory
from scrutare.engine.session_models import NareCapability, NareRuntime, TokenUsage
from scrutare.findings.models import Anchor, Finding


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def correction_setup(capture, *, limit=100):
    review, ledger, initial, first = setup(capture, limit=limit, system="Exact SYSTEM\n--flag")
    ledger.settle(initial, TokenUsage(60), True)
    original = Finding(Anchor("src/app.py", 999, "RIGHT"), "correctness", "Problem", "Reason",
                       "security")
    descriptor = PersonaReanchorInput(review.inputs, review.persona, make_reanchor_requests(
        (original,)))
    lease = ledger.admit("security", "security/attempt-0002")
    second = create_attempt_directory(capture, "security", 2, prepared_root=review.inputs.root)
    (first / "initial-evidence.txt").write_text("initial-sentinel")
    return descriptor, ledger, lease, first, second


def invoke(descriptor, ledger, lease, attempt, path, *, timeout=5):
    return module().run_reanchor_session(
        descriptor, ModelRail("openai", "https://example.invalid/v1", "--exact-model"), lease,
        ledger=ledger, artifact_directory=attempt, runtime=NareRuntime(path, 7, timeout),
        capability=NareCapability("2026.10.0", 1))


@pytest.mark.parametrize("input_tokens,status,stop,exit_code,want", [
    (14, "done", "end_turn", 0, "complete"),
    (34, "done", "end_turn", 0, "complete"),
    (35, "done", "end_turn", 0, "partial"),
    (35, "error", "budget", 1, "partial"),
    (14, "error", None, 1, "failed"),
])
def test_typed_correction_preserves_binding_fresh_budget_and_initial_evidence(
        capture, tmp_path, input_tokens, status, stop, exit_code, want):
    descriptor, ledger, lease, first, attempt = correction_setup(capture)
    path = running_executable(tmp_path, input_tokens=input_tokens, status=status, stop=stop,
                              exit_code=exit_code, output={"corrections": [
                                  {"request_id": "r0001", "file": "src/app.py", "line": 2,
                                   "side": "RIGHT"}]})
    outcome = asyncio.run(invoke(descriptor, ledger, lease, attempt, path))
    assert outcome.status == want
    assert not hasattr(outcome, "findings")
    assert outcome.corrections[0].original == descriptor.requests[0].original
    assert outcome.corrections[0].anchor == Anchor("src/app.py", 2, "RIGHT")
    assert outcome.usage == TokenUsage(input_tokens, 2, 3, 1)
    assert ledger.usage.total == 60 + input_tokens + 6
    assert outcome.invocation_limit == outcome.allocated_tokens == 40
    assert outcome.overshoot_tokens == max(0, input_tokens - 34)
    assert (first / "initial-evidence.txt").read_text() == "initial-sentinel"
    assert sorted(p.name for p in first.iterdir()) == ["initial-evidence.txt"]
    observed = json.loads((attempt / "cwd" / "observed.json").read_bytes())
    assert observed["schema"]["required"] == ["corrections"]
    assert observed["system"] == "Exact SYSTEM\n--flag"
    assert observed["model"] == "--exact-model"
    args = observed["argv"]
    assert args[:6] == ["run", descriptor.prompt, "--tools", "read", "--root",
                        str(descriptor.inputs.root)]
    assert "--resume" not in args
    assert args[args.index("--budget-tokens") + 1] == "40"
    assert args[args.index("--session") + 1] == str(attempt / "session.json")
    assert observed["stdin"] == ""
    assert observed["environment"]["HOME"] == str(attempt / "home")
    assert not any(key.startswith("NARE_") for key in observed["environment"])
    for name in ("invocation.json", "result.json"):
        evidence = json.loads((attempt / name).read_bytes())
        assert evidence["purpose"] == "reanchor"
        assert evidence["session_key"] == "security/attempt-0002"
        assert evidence["requests"][0]["request_id"] == "r0001"
        assert evidence["requests"][0]["original"]["problem"] == "Problem"
    assert "findings" not in outcome.to_dict()
    assert ledger.snapshot()["active_reservations"] == []
    assert all(p.stat().st_mode & 0o077 == 0 for p in attempt.iterdir())


@pytest.mark.parametrize("output", [
    {"findings": []},
    {"corrections": [{"request_id": "r0002", "file": "src/app.py", "line": 2, "side": "RIGHT"}]},
    {"corrections": [{"request_id": "r0001", "file": "src/app.py", "line": 2, "side": "RIGHT",
                      "problem": "Replacement"}]},
])
def test_invalid_correction_output_seals_admission_without_inventing_findings(
        capture, tmp_path, output):
    descriptor, ledger, lease, _, attempt = correction_setup(capture)
    outcome = asyncio.run(invoke(descriptor, ledger, lease, attempt, running_executable(
        tmp_path, input_tokens=4, output=output)))
    assert outcome.status == "failed" and outcome.reason == "protocol"
    assert outcome.corrections == () and not outcome.accounting_complete
    assert ledger.usage.total == 70
    assert ledger.admit("security", "later") is None


@pytest.mark.parametrize("moment", ["before", "after"])
def test_correction_revalidates_prepared_root(capture, tmp_path, moment):
    descriptor, ledger, lease, _, attempt = correction_setup(capture)
    path = running_executable(tmp_path, output={"corrections": []}, extra=(
        'Path(a.root, "diff.patch").write_text("changed")' if moment == "after" else ""))
    if moment == "before":
        (descriptor.inputs.root / "diff.patch").write_text("changed")
    outcome = asyncio.run(invoke(descriptor, ledger, lease, attempt, path))
    assert outcome.status == "failed" and outcome.reason == "inputs"
    assert outcome.accounting_complete == (moment == "before")
    assert ledger.accounting_complete == (moment == "before")
    assert ledger.usage.total == (60 if moment == "before" else 70)
    assert (attempt / "cwd" / "observed.json").exists() == (moment == "after")


def test_correction_malformed_live_usage_seals_before_terminal_and_sibling_settles(
        capture, tmp_path):
    descriptor, _, _, _, attempt = correction_setup(capture)
    ledger = ReviewBudgetLedger(("security", "sibling", "other"), BudgetSettings(100, 300))
    initial = ledger.admit("security", "security/attempt-0001")
    ledger.settle(initial, TokenUsage(60), True)
    lease = ledger.admit("security", "security/attempt-0002")
    sibling = ledger.admit("sibling", "sibling/attempt-0001")
    path = running_executable(tmp_path, output={"corrections": []}, extra='''
print("malformed-live-usage", flush=True)
import time
while not Path("release.marker").exists():
    time.sleep(0.005)
''')

    async def scenario():
        task = asyncio.create_task(invoke(descriptor, ledger, lease, attempt, path))
        try:
            async def wait_for_seal():
                while ledger.accounting_complete:
                    await asyncio.sleep(0.005)
            await asyncio.wait_for(wait_for_seal(), 2)
            assert not task.done()
            assert ledger.usage.total == 70
            assert ledger.admit("other", "later") is None
            ledger.settle(sibling, TokenUsage(7), True)
            (attempt / "cwd" / "release.marker").touch()
            outcome = await task
            assert outcome.status == "failed" and outcome.corrections == ()
            assert ledger.usage.total == 77
            assert ledger.snapshot()["active_reservations"] == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_correction_timeout_and_repeated_cancellation_reap_owned_group(
        capture, tmp_path, cancel):
    descriptor, ledger, lease, _, attempt = correction_setup(capture)
    path = executable(tmp_path, body=RUN_BODY.split("EXTRA")[0].replace("INPUT", "4") + HANG_BODY)

    async def scenario():
        task = asyncio.create_task(invoke(descriptor, ledger, lease, attempt, path,
                                          timeout=5 if cancel else 0.2))
        try:
            pids = await fixture_pids(attempt / "cwd" / "pids.json", task)
            if cancel:
                task.cancel()
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                outcome = await task
                assert outcome.reason == "timeout" and outcome.corrections == ()
            assert all(not alive(pid) for pid in pids)
            assert ledger.usage.total == 70 and not ledger.accounting_complete
            assert ledger.snapshot()["active_reservations"] == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["destination", "key", "zero_usage_initial_key"])
def test_correction_refuses_wrong_attempt_or_initial_identity_and_settles(
        capture, tmp_path, invalid):
    descriptor, ledger, lease, first, attempt = correction_setup(capture)
    if invalid == "destination":
        attempt = create_attempt_directory(capture, "security", 3,
                                           prepared_root=descriptor.inputs.root)
    else:
        ledger = ReviewBudgetLedger(("security",), BudgetSettings(100, 100))
        key = "arbitrary-correction-key"
        if invalid == "zero_usage_initial_key":
            key = "security/attempt-0001"
            initial = ledger.admit("security", key)
            ledger.settle(initial, TokenUsage(), True)
        lease = ledger.admit("security", key)
    outcome = asyncio.run(invoke(descriptor, ledger, lease, attempt, running_executable(
        tmp_path, output={"corrections": []})))
    assert outcome.status == "failed"
    assert outcome.reason == ("artifacts" if invalid == "destination" else "invocation")
    assert outcome.accounting_complete and outcome.usage == TokenUsage()
    assert outcome.corrections == ()
    assert not (attempt / "cwd").exists()
    assert ledger.snapshot()["active_reservations"] == []
    assert (first / "initial-evidence.txt").read_text() == "initial-sentinel"
