"""Opt-in inspection procedures preserve defaults and exact overrides."""

from hashlib import sha256

import pytest
import yaml
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, PersonaDefinition, parse_config
from scrutare.personas import load_persona, resolve_personas

MINIMAL = "models:\n  default:\n    model: test-model\n"
DEFAULTS = ("senior-dev", "junior-dev", "security", "devops")


def test_testing_reviewer_is_selectable_without_changing_defaults():
    config = parse_config(MINIMAL)
    assert config.personas == DEFAULTS
    testing = load_persona("testing-verification")
    assert "counterexample" in testing.system_prompt.lower()
    assert "assertion" in testing.system_prompt.lower()
    selected = parse_config(MINIMAL + "personas: [testing-verification]\n")
    assert selected.personas == ("testing-verification",)


def test_profile_defaults_preserve_old_canonical_capture():
    config = parse_config(MINIMAL)
    assert config.inspection.procedures == "baseline"
    assert "inspection" not in config.to_dict()
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_opt_in_profile_roundtrip_and_builtin_resolution():
    config = parse_config(MINIMAL + "inspection:\n  procedures: v1\n")
    assert config.to_dict()["inspection"] == {"procedures": "v1"}
    assert parse_config(yaml.safe_dump(config.to_dict())) == config
    baseline = resolve_personas(DEFAULTS)
    revised = resolve_personas(DEFAULTS, procedures=config.inspection.procedures)
    assert tuple(p.name for p in revised) == DEFAULTS
    for old, new in zip(baseline, revised):
        assert old.system_prompt != new.system_prompt
        assert "procedure v1" in new.system_prompt.lower()
        assert "counterexample" in new.system_prompt.lower()
        assert "untrusted" in new.system_prompt.lower()
        assert "verdict" in new.system_prompt.lower()


@pytest.mark.parametrize("value", [None, [], "v1", {"procedures": "v2"},
                                      {"procedures": True}, {"unknown": "v1"}])
def test_invalid_profile_is_rejected(value):
    with pytest.raises(ConfigError, match="inspection"):
        parse_config(yaml.safe_dump({"models": {"default": {"model": "test"}},
                                    "inspection": value}))


def test_inline_overrides_preserve_identity_and_bytes_under_profile():
    override = PersonaDefinition("security", "  Our exact procedure.\n\n")
    revised = resolve_personas([override, "senior-dev"], procedures="v1")
    assert revised[0] is override
    assert revised[0].system_prompt == override.system_prompt
    with pytest.raises(ValueError, match="procedure"):
        resolve_personas(DEFAULTS, procedures="unknown")


def test_procedure_record_binds_effective_prompt_and_origin():
    from scrutare.personas.registry import procedure_record

    original = load_persona("security")
    revised = load_persona("security", procedures="v1")
    record = procedure_record(revised, procedures="v1", inline=False)
    assert record["origin"] == "builtin"
    assert record["version"] == "v1"
    assert record["system_prompt_sha256"] == sha256(revised.system_prompt.encode()).hexdigest()
    assert record["procedure_sha256"] != procedure_record(
        original, procedures="baseline", inline=False)["procedure_sha256"]
    override = PersonaDefinition("security", "Exact override")
    record = procedure_record(override, procedures="v1", inline=True)
    assert record["origin"] == "inline"
    assert record["version"] == "inline"
    assert record["system_prompt_sha256"] == sha256(b"Exact override").hexdigest()


def test_production_initial_wave_records_resolved_procedure(capture, monkeypatch):
    import asyncio
    import json

    from test_fanout import configure
    from test_panel import install

    from scrutare.engine.fanout import fan_out
    from scrutare.engine.session_models import NareRuntime

    config = configure(capture, personas=["security"])
    doc = config.to_dict()
    doc["inspection"] = {"procedures": "v1"}
    config = parse_config(json.dumps(doc))
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config.to_dict()))
    install(monkeypatch, {"security": ()})
    asyncio.run(fan_out(capture, config, runtime=NareRuntime(capture / "nare")))
    manifest = json.loads((capture / "fanout.json").read_text())
    entry = manifest["personas"][0]
    assert entry["system_prompt"] == load_persona("security", procedures="v1").system_prompt
    assert entry["procedure"]["version"] == "v1"
    assert entry["procedure"]["system_prompt_sha256"] == sha256(
        entry["system_prompt"].encode()).hexdigest()


def test_debate_chair_uses_selected_procedure(capture, monkeypatch):
    import json

    from test_debate import execute, mock_debate, settings
    from test_panel import finding, install

    config = settings(capture)
    doc = config.to_dict()
    doc["inspection"] = {"procedures": "v1"}
    config = parse_config(json.dumps(doc))
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config.to_dict()))
    install(monkeypatch, {"security": (finding(),)})
    calls = mock_debate(monkeypatch)
    result = execute(capture, config)
    assert result.status == "complete"
    chair = next(d for d, _, _ in calls if d.chair)
    assert chair.persona.system_prompt == load_persona("senior-dev", procedures="v1").system_prompt


def test_session_invocation_hashes_exact_effective_prompts(capture, tmp_path):
    import json

    from test_nare_session import run, running_executable, setup

    descriptor, ledger, lease, attempt = setup(capture, system="  Exact system.\n\n")
    result = run(descriptor, ledger, lease, attempt, running_executable(tmp_path))
    assert result.status == "complete"
    invocation = json.loads((attempt / "invocation.json").read_text())
    assert invocation["system_prompt_sha256"] == sha256(
        descriptor.persona.system_prompt.encode()).hexdigest()
    assert invocation["task_prompt_sha256"] == sha256(descriptor.prompt.encode()).hexdigest()


def test_correction_invocation_hashes_its_distinct_task(capture, tmp_path):
    import asyncio
    import json

    from test_reanchor_session import correction_setup, invoke, running_executable

    descriptor, ledger, lease, _, attempt = correction_setup(capture)
    executable = running_executable(tmp_path, output={"corrections": []})
    outcome = asyncio.run(invoke(descriptor, ledger, lease, attempt, executable))
    assert outcome.status == "complete"
    invocation = json.loads((attempt / "invocation.json").read_text())
    assert invocation["system_prompt_sha256"] == sha256(
        descriptor.persona.system_prompt.encode()).hexdigest()
    assert invocation["task_prompt_sha256"] == sha256(descriptor.prompt.encode()).hexdigest()
    assert invocation["purpose"] == "reanchor"


def test_production_manifest_identifies_exact_inline_override(capture, monkeypatch):
    import asyncio
    import json

    from test_fanout import configure
    from test_panel import install

    from scrutare.engine.fanout import fan_out
    from scrutare.engine.session_models import NareRuntime

    config = configure(capture, personas=["security"])
    doc = config.to_dict()
    doc["inspection"] = {"procedures": "v1"}
    doc["personas"] = [{"name": "security", "system_prompt": " Exact override.\n\n"}]
    config = parse_config(json.dumps(doc))
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config.to_dict()))
    install(monkeypatch, {"security": ()})
    asyncio.run(fan_out(capture, config, runtime=NareRuntime(capture / "nare")))
    entry = json.loads((capture / "fanout.json").read_text())["personas"][0]
    assert entry["system_prompt"] == " Exact override.\n\n"
    assert entry["procedure"]["origin"] == "inline"
    assert entry["procedure"]["version"] == "inline"
    assert entry["procedure"]["procedure_sha256"] == sha256(b" Exact override.\n\n").hexdigest()
