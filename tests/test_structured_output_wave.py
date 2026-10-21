"""A wave on a rail that asks for constrained answers refuses an incapable runtime up front."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_runner import corpus
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.nare_session import SessionRuntimeError
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_models import NareCapability, NareRuntime, SessionOutcome, TokenUsage
from scrutare.evaluation.runner import run_experiment

ENABLED = {"default": {"provider": "openai", "model": "m", "structured_output": "json_schema"}}


def configure(capture, models, personas=("senior-dev",)):
    document = {"personas": list(personas), "models": models,
                "budgets": {"per_persona_tokens": 40, "review_max_tokens": 40 * len(personas)}}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    prepare_review_inputs(capture, config)
    return config


def wave(capture, monkeypatch, config, capability):
    import scrutare.engine.fanout as fanout
    launched = []

    async def inspect(runtime):
        return capability

    async def session(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        launched.append(rail)
        ledger.settle(lease, TokenUsage(), True)
        return SessionOutcome(lease.persona, "complete", "done", (), True, TokenUsage(), True,
                              lease.allocated_tokens, 0, lease.persona, artifact_directory,
                              lease.limit_tokens)

    monkeypatch.setattr(fanout, "inspect_nare_runtime", inspect)
    monkeypatch.setattr(fanout, "run_persona_session", session)
    return launched, asyncio.run(fanout.fan_out(capture, config,
                                               runtime=NareRuntime(capture / "nare")))


def test_an_incapable_runtime_fails_before_any_session(capture, monkeypatch):
    config = configure(capture, ENABLED)
    with pytest.raises(SessionRuntimeError,
                       match="nare 2026.10.4 cannot send structured output json_schema"):
        wave(capture, monkeypatch, config, NareCapability("2026.10.4", 1))
    assert not (capture / "fanout.json").exists()


def test_an_override_alone_also_requires_the_capability(capture, monkeypatch):
    config = configure(capture, {"default": {"provider": "openai", "model": "m"},
                                 "overrides": {"senior-dev": {"structured_output": "json_schema"}}})
    with pytest.raises(SessionRuntimeError, match="cannot send structured output"):
        wave(capture, monkeypatch, config, NareCapability("2026.10.11", 2))


def test_a_capable_runtime_receives_the_enabled_rail(capture, monkeypatch):
    config = configure(capture, ENABLED)
    launched, result = wave(capture, monkeypatch, config,
                            NareCapability("2026.10.12", 2, ("json_schema",)))
    assert [rail.structured_output for rail in launched] == ["json_schema"]
    manifest = json.loads((capture / "fanout.json").read_bytes())
    assert manifest["personas"][0]["rail"]["structured_output"] == "json_schema"


def test_an_opted_in_manifest_records_the_field_on_every_rail_like_the_config(
        capture, monkeypatch):
    config = configure(capture, {**ENABLED, "overrides": {"security": {"structured_output": None}}},
                       personas=("senior-dev", "security"))
    wave(capture, monkeypatch, config, NareCapability("2026.10.12", 2, ("json_schema",)))
    manifest = json.loads((capture / "fanout.json").read_bytes())
    rails = {entry["name"]: entry["rail"] for entry in manifest["personas"]}
    assert rails["security"]["structured_output"] is None
    assert rails["senior-dev"]["structured_output"] == "json_schema"
    assert rails["security"] == config.to_dict()["models"]["overrides"]["security"]


def test_an_unconstrained_wave_never_needs_the_capability(capture, monkeypatch):
    config = configure(capture, {"default": {"provider": "openai", "model": "m"}})
    launched, _ = wave(capture, monkeypatch, config, NareCapability("2026.10.4", 1))
    assert [rail.structured_output for rail in launched] == [None]


def test_the_evaluator_refuses_an_incapable_runtime_before_any_cell(tmp_path, monkeypatch):
    cases = corpus(tmp_path / "corpus")
    calls = []

    async def execute(run_dir, conf, *, runtime):
        calls.append(run_dir)

    monkeypatch.setattr("scrutare.evaluation.runner.run_review", execute)
    monkeypatch.setattr("scrutare.evaluation.runner.inspect_nare_runtime",
                        lambda runtime: asyncio.sleep(
                            0, result=NareCapability("2026.10.4", 1)))
    config = parse_config(json.dumps({"models": ENABLED, "budgets": {
        "review_max_tokens": 400, "per_persona_tokens": 100}}))
    with pytest.raises(SessionRuntimeError, match="cannot send structured output"):
        asyncio.run(run_experiment(cases, config, tmp_path / "out",
                                   runtime=NareRuntime(Path("/unused")), evidence_kind="offline"))
    assert calls == []
    assert not (tmp_path / "out/experiment.json").exists()


def test_the_evaluator_records_the_capability_only_when_a_rail_uses_it(tmp_path, monkeypatch):
    cases = corpus(tmp_path / "corpus")

    async def execute(run_dir, conf, *, runtime):
        return SimpleNamespace(status="complete", accounting_complete=True,
                               usage=TokenUsage(input=5), verdict=SimpleNamespace(findings=()))

    monkeypatch.setattr("scrutare.evaluation.runner.run_review", execute)
    monkeypatch.setattr("scrutare.evaluation.runner.inspect_nare_runtime",
                        lambda runtime: asyncio.sleep(0, result=NareCapability(
                            "2026.10.12", 2, ("json_schema",))))
    budgets = {"review_max_tokens": 400, "per_persona_tokens": 100}
    on = parse_config(json.dumps({"models": ENABLED, "budgets": budgets}))
    asyncio.run(run_experiment(cases, on, tmp_path / "on", runtime=NareRuntime(Path("/unused")),
                               evidence_kind="offline"))
    snapshot = json.loads((tmp_path / "on/snapshot.json").read_bytes())
    assert snapshot["nare_structured_output"] == ["json_schema"]
    off = parse_config(json.dumps({"models": {"default": {"provider": "openai", "model": "m"}},
                                   "budgets": budgets}))
    asyncio.run(run_experiment(cases, off, tmp_path / "off", runtime=NareRuntime(Path("/unused")),
                               evidence_kind="offline"))
    assert "nare_structured_output" not in json.loads((tmp_path / "off/snapshot.json").read_bytes())
