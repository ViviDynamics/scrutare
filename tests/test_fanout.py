"""Concurrent wave behavior against real prepared capture inputs."""

import asyncio
import importlib
import json
from hashlib import sha256

import pytest
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_models import NareCapability, NareRuntime, SessionOutcome, TokenUsage


def configure(capture, *, personas=None, review=80, per=40, empty=False):
    document = {
        "personas": personas
        or ["senior-dev", {"name": "custom", "system_prompt": "Custom SYSTEM"}],
        "models": {"default": {"model": "default-model"}, "overrides": {}},
        "budgets": {"per_persona_tokens": per, "review_max_tokens": review},
    }
    if empty:
        document["github"] = {"paths": {"include": ["absent/**"]}}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    return config


def wave_module():
    return importlib.import_module("scrutare.engine.fanout")


def test_wave_admits_all_before_tasks_overlap_and_preserves_order(capture, monkeypatch):
    config = configure(capture)
    inputs = prepare_review_inputs(capture, config)
    before = {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()}
    wave = wave_module()
    calls, finished, ledgers = [], [], []

    async def inspect(runtime):
        calls.append("inspect")
        return NareCapability("2026.10.0", 1)

    async def session(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        assert len(ledger.snapshot()["active_reservations"]) == 2
        ledgers.append(ledger)
        calls.append(descriptor.persona.name)
        assert descriptor.nare_input_args()[1:] == ("--tools", "read", "--root", str(inputs.root))
        assert inputs.root not in artifact_directory.parents
        await asyncio.sleep(0.02 if descriptor.persona.name == "senior-dev" else 0)
        usage = TokenUsage(2, 3, 5, 8)
        ledger.settle(lease, usage, True)
        finished.append(lease.persona)
        return SessionOutcome(
            lease.persona,
            "complete",
            "done",
            (),
            True,
            usage,
            True,
            lease.allocated_tokens,
            0,
            lease.persona,
            artifact_directory,
            lease.limit_tokens,
        )

    monkeypatch.setattr(wave, "inspect_nare_runtime", inspect)
    monkeypatch.setattr(wave, "run_persona_session", session)
    result = asyncio.run(wave.fan_out(capture, config, runtime=NareRuntime(capture / "nare")))
    assert calls == ["inspect", "senior-dev", "custom"]
    assert finished == ["custom", "senior-dev"]
    assert [o.persona for o in result.outcomes] == ["senior-dev", "custom"]
    assert result.usage == TokenUsage(4, 6, 10, 16)
    assert not result.failed and not result.partial
    assert ledgers[0] is ledgers[1]
    manifest = json.loads((capture / "fanout.json").read_bytes())
    assert manifest["schema_version"] == 1 and manifest["head_sha"] == "abc123"
    assert manifest["nare_version"] == "2026.10.0" and manifest["contract"] == 1
    assert manifest["personas"][1]["system_prompt"] == "Custom SYSTEM"
    assert manifest["personas"][1]["rail"]["model"] == "default-model"
    assert manifest["inputs_sha256"] == {
        p.name: sha256(p.read_bytes()).hexdigest() for p in inputs.root.iterdir()
    }
    assert manifest["ledger"]["active_reservations"] == []
    assert manifest["ledger"]["accounting_complete"] is True
    assert manifest["result"] == result.to_dict()
    assert all(p.read_bytes() == b for p, b in before.items())
    assert not (capture / "findings.json").exists() and not (capture / "verdict.json").exists()


@pytest.mark.parametrize(
    "existing", ["sessions", "fanout.json", "sessions-link", "fanout.json-link"]
)
def test_existing_wave_artifacts_fail_before_runtime(capture, monkeypatch, existing):
    config = configure(capture)
    prepare_review_inputs(capture, config)
    if existing.endswith("link"):
        (capture / existing.removesuffix("-link")).symlink_to(capture / "absent")
    elif existing == "sessions":
        (capture / existing).mkdir()
    else:
        (capture / existing).write_text("preserve")
    wave = wave_module()

    async def unexpected(runtime):
        pytest.fail("existing wave must fail before runtime access")

    monkeypatch.setattr(wave, "inspect_nare_runtime", unexpected)
    with pytest.raises(ValueError, match="artifact"):
        asyncio.run(wave.fan_out(capture, config, runtime=NareRuntime(capture / "nare")))
    if existing == "fanout.json":
        assert (capture / existing).read_text() == "preserve"


def test_zero_quotas_are_explicit_without_launch_after_sibling_failure(capture, monkeypatch):
    config = configure(capture, personas=["senior-dev", "security", "devops"], review=1)
    wave = wave_module()
    launched = []

    async def inspect(runtime):
        return NareCapability("2026.10.0", 1)

    async def session(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        launched.append(lease.persona)
        usage = TokenUsage(10, 5)
        ledger.settle(lease, usage, False)
        return SessionOutcome(
            lease.persona,
            "failed",
            "provider",
            (),
            False,
            usage,
            False,
            lease.allocated_tokens,
            14,
            None,
            artifact_directory,
            1,
        )

    monkeypatch.setattr(wave, "inspect_nare_runtime", inspect)
    monkeypatch.setattr(wave, "run_persona_session", session)
    result = asyncio.run(wave.fan_out(capture, config, runtime=NareRuntime(capture / "nare")))
    assert launched == ["senior-dev"]
    assert [o.status for o in result.outcomes] == ["failed", "not_started", "not_started"]
    assert all(o.reason == "review_budget" and o.invocation_limit == 0 for o in result.outcomes[1:])
    assert result.failed and result.partial and result.review_exhausted
    assert result.review_overshoot_tokens == 14
    assert (
        json.loads((capture / "fanout.json").read_bytes())["ledger"]["accounting_complete"] is False
    )


def test_cancellation_awaits_every_child_cleanup(capture, monkeypatch):
    config = configure(capture)
    wave = wave_module()
    started, reaped = [], []

    async def inspect(runtime):
        return NareCapability("2026.10.0", 1)

    async def session(descriptor, rail, lease, **kwargs):
        started.append(lease.persona)
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0.01)
            reaped.append(lease.persona)

    monkeypatch.setattr(wave, "inspect_nare_runtime", inspect)
    monkeypatch.setattr(wave, "run_persona_session", session)

    async def scenario():
        task = asyncio.create_task(
            wave.fan_out(capture, config, runtime=NareRuntime(capture / "nare"))
        )
        while len(started) != 2:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(reaped) == ["custom", "senior-dev"]

    asyncio.run(scenario())
    assert not (capture / "fanout.json").exists()
