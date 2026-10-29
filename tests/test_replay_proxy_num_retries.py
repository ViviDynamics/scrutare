"""Retry controls bind correction turns and reserved executors to their own rail."""

import json
from dataclasses import replace

import pytest
from test_review_inputs import capture as capture

from scrutare.config import ModelRail, parse_config
from scrutare.replay.audit import (
    _citation_correction_issues,
    _protocol_correction_issues,
    _proxy_num_retries_issues,
)


@pytest.mark.parametrize("protocol", [False, True])
@pytest.mark.parametrize("flags", [
    ["--proxy-num-retries", "2"],
    ["--proxy-num-retries=2"],
    ["--proxy-num-retries", "0", "--proxy-num-retries=2"],
    [],
])
def test_correction_cannot_change_or_lose_its_retry_control(
        capture, tmp_path, monkeypatch, protocol, flags):
    import test_citation_correction_session as citation
    import test_protocol_correction_session as correction

    # Extend the scripted external CLI's accepted flags; execute real session/correction code.
    monkeypatch.setattr(citation, "BODY", citation.BODY.replace(
        '"max-tokens"):', '"max-tokens", "proxy-num-retries"):'))
    capable = replace(citation.RUNTIME, proxy_num_retries=True)
    monkeypatch.setattr(citation, "RUNTIME", capable)
    monkeypatch.setattr(correction, "RUNTIME", capable)
    rail = ModelRail("openai", None, "m", proxy_num_retries=0)
    if protocol:
        outcome, _, attempt = correction.launch(capture, tmp_path, correction.PLACEHOLDER,
                                                 rail=rail)
        stem, audit = "protocol-correction", _protocol_correction_issues
    else:
        outcome, _, attempt = citation.launch(capture, tmp_path, citation.PAST_END,
            {"output": citation.fix(("c0001", citation.GOOD, 1, 3))}, rail=rail)
        stem, audit = "citation-correction", _citation_correction_issues
    assert outcome.status == "complete"
    config = json.loads((capture / "config.json").read_text())
    config["models"]["default"]["proxy_num_retries"] = 0
    assert audit(capture, config) == ()
    path = attempt / (stem + ".json")
    document = json.loads(path.read_text())
    index = document["argv"].index("--proxy-num-retries")
    document["argv"][index:index + 2] = flags
    path.write_text(json.dumps(document))
    assert any(issue.code == stem.replace("-", "_") + "_disagrees"
               for issue in audit(capture, config))


@pytest.mark.parametrize("role,collision", [
    ("assessor", False), ("assessor", True), ("chair", False), ("chair", True),
])
def test_reserved_executor_cannot_substitute_another_configured_retry_rail(
        tmp_path, role, collision):
    root_name = "evidence-assessor" if role == "assessor" else "debate-chair"
    suffix = "-assessor" if role == "assessor" else "-chair"
    persona = root_name + suffix if collision else root_name
    config = parse_config(json.dumps({
        "strategy": "debate" if role == "chair" else "panel",
        "personas": ["senior-dev", "security", *(
            [{"name": root_name, "system_prompt": "Review."}] if collision else [])],
        "context": {"enabled": True},
        "findings": {"evidence": "v2", "assessment": {"enabled": role == "assessor"}},
        "models": {"default": {"provider": "openai", "model": "m", "proxy_num_retries": 0},
                   "overrides": {"security": {"proxy_num_retries": None}}},
    })).to_dict()
    attempt = tmp_path / "sessions" / persona / "attempt-0001"
    attempt.mkdir(parents=True)
    invocation = {"persona": persona, "rail": {"provider": "openai", "model": "m",
                  "base_url": None, "proxy_num_retries": 0},
                  "argv": ["nare", "run", "prompt", "--proxy-num-retries", "0"]}
    path = attempt / "invocation.json"
    path.write_text(json.dumps(invocation))
    assert _proxy_num_retries_issues(tmp_path, config) == ()
    invocation["rail"].pop("proxy_num_retries")
    invocation["argv"] = invocation["argv"][:-2]
    path.write_text(json.dumps(invocation))
    assert [issue.code for issue in _proxy_num_retries_issues(tmp_path, config)] == [
        "proxy_num_retries_unrecorded"]


def test_chair_uses_the_senior_override_and_cannot_claim_another_persona(tmp_path):
    config = parse_config(json.dumps({
        "strategy": "debate",
        "models": {"default": {"provider": "openai", "model": "m"},
                   "overrides": {"senior-dev": {"proxy_num_retries": 0}}},
    })).to_dict()
    attempt = tmp_path / "sessions/debate-chair/attempt-0001"
    attempt.mkdir(parents=True)
    path = attempt / "invocation.json"
    invocation = {"persona": "debate-chair", "rail": {"provider": "openai", "model": "m",
                  "base_url": None, "proxy_num_retries": 0},
                  "argv": ["nare", "run", "prompt", "--proxy-num-retries", "0"]}
    path.write_text(json.dumps(invocation))
    assert _proxy_num_retries_issues(tmp_path, config) == ()
    invocation["persona"] = "security"
    invocation["rail"].pop("proxy_num_retries")
    invocation["argv"] = invocation["argv"][:-2]
    path.write_text(json.dumps(invocation))
    assert _proxy_num_retries_issues(tmp_path, config)


def test_routed_chair_identity_cannot_be_changed_in_unvalidated_focus(capture):
    from scrutare.engine.review_inputs import prepare_review_inputs

    config = parse_config(json.dumps({
        "strategy": "debate", "routing": {"enabled": True},
        "models": {"default": {"provider": "openai", "model": "m"},
                   "overrides": {"senior-dev": {"proxy_num_retries": 0}}},
    })).to_dict()
    assert config.get("context", {}).get("enabled") is not True
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config))
    inputs = prepare_review_inputs(capture, parse_config(json.dumps(config))).root
    focus_path = inputs / "routing-focus.json"
    focus = json.loads(focus_path.read_text())
    attempt = capture / "sessions/debate-chair/attempt-0001"
    attempt.mkdir(parents=True)
    path = attempt / "invocation.json"
    invocation = {"persona": "debate-chair", "rail": {"provider": "openai", "model": "m",
                  "base_url": None, "proxy_num_retries": 0},
                  "argv": ["nare", "run", "prompt", "--proxy-num-retries", "0"]}
    path.write_text(json.dumps(invocation))
    assert _proxy_num_retries_issues(capture, config) == ()
    focus["effective_personas"].append("debate-chair")
    focus_path.write_text(json.dumps(focus))
    invocation["rail"].pop("proxy_num_retries")
    invocation["argv"] = invocation["argv"][:-2]
    path.write_text(json.dumps(invocation))
    assert _proxy_num_retries_issues(capture, config)


@pytest.mark.parametrize("field", ["filename", "previous_filename"])
def test_routed_chair_retry_cannot_be_removed_by_injecting_a_prepared_path(capture, field):
    from scrutare.engine.review_inputs import prepare_review_inputs

    config = parse_config(json.dumps({
        "strategy": "debate", "routing": {"enabled": True, "rules": [{
            "persona": {"name": "debate-chair", "system_prompt": "Review."},
            "paths": ["special/**"]}]},
        "models": {"default": {"provider": "openai", "model": "m"},
                   "overrides": {"senior-dev": {"proxy_num_retries": 0}}},
    }))
    assert not config.context.enabled
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config.to_dict()))
    inputs = prepare_review_inputs(capture, config)
    focus = (inputs.root / "routing-focus.json").read_bytes()
    assert "debate-chair" not in json.loads(focus)["effective_personas"]
    attempt = capture / "sessions/debate-chair/attempt-0001"
    attempt.mkdir(parents=True)
    invocation = {"persona": "debate-chair", "rail": {"provider": "openai", "model": "m",
                  "base_url": None, "proxy_num_retries": 0},
                  "argv": ["nare", "run", "prompt", "--proxy-num-retries", "0"]}
    path = attempt / "invocation.json"
    path.write_text(json.dumps(invocation))
    assert _proxy_num_retries_issues(capture, config.to_dict()) == ()
    files_path = inputs.root / "files.json"
    files = json.loads(files_path.read_text())
    files[0][field] = "special/trigger.py"
    files_path.write_text(json.dumps(files))
    invocation["rail"].pop("proxy_num_retries")
    invocation["argv"] = invocation["argv"][:-2]
    path.write_text(json.dumps(invocation))
    assert (inputs.root / "routing-focus.json").read_bytes() == focus
    assert _proxy_num_retries_issues(capture, config.to_dict())
