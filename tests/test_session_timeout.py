"""The configured session timeout: parsing, each interface's runtime, the App fit, replay."""

import asyncio
import importlib
import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from test_app import settings
from test_app_worker import TrustedClient, claimed, cli_result

from scrutare.config import (
    SESSION_TIMEOUT_MAX_SECONDS,
    SESSION_TIMEOUT_SECONDS,
    ConfigError,
    ModelRail,
    ModelSettings,
    parse_config,
)
from scrutare.engine.session_models import NareRuntime
from scrutare.replay.audit import _session_timeout_issues

OPENAI = "models:\n  default: {provider: openai, model: m}\n"


def configured(seconds, extra=""):
    return parse_config(OPENAI + f"  session_timeout_seconds: {seconds}\n" + extra)


def test_unset_keeps_600_seconds_and_the_configuration_bytes():
    config = parse_config(OPENAI)
    assert config.models.session_timeout_seconds is None
    assert config.models.session_seconds == SESSION_TIMEOUT_SECONDS == 600.0
    assert "session_timeout_seconds" not in config.to_dict()["models"]
    assert parse_config(OPENAI + "  session_timeout_seconds: null\n") == config
    # The runtime every interface built before is the one it builds now.
    assert NareRuntime(Path("nare"), timeout_seconds=config.models.session_seconds) == (
        NareRuntime(Path("nare")))


@pytest.mark.parametrize("value", [1800, 900.5, 1, SESSION_TIMEOUT_MAX_SECONDS])
def test_a_configured_timeout_is_recorded_and_round_trips(value):
    config = configured(value)
    assert config.models.session_timeout_seconds == value
    assert config.models.session_seconds == float(value)
    record = config.to_dict()["models"]
    assert record["session_timeout_seconds"] == value
    # Recorded after every field that existed before it.
    assert list(record)[-1] == "session_timeout_seconds"
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


@pytest.mark.parametrize(("value", "message"), [
    ("0", "expected a positive finite number or null"),
    ("-5", "expected a positive finite number or null"),
    ("true", "expected a positive finite number or null"),
    (".inf", "expected a positive finite number or null"),
    (".nan", "expected a positive finite number or null"),
    ('"600"', "expected a positive finite number or null"),
    (str(SESSION_TIMEOUT_MAX_SECONDS + 1), "expected at most 21600 seconds"),
])
def test_invalid_timeouts_are_refused_at_configuration(value, message):
    with pytest.raises(ConfigError, match="^models.session_timeout_seconds: " + message):
        configured(value)


def test_programmatic_settings_validate_like_configuration():
    rail = ModelRail("openai", None, "m")
    for value in (0, math.inf, True, SESSION_TIMEOUT_MAX_SECONDS + 1):
        with pytest.raises(ConfigError, match="session_timeout_seconds"):
            ModelSettings(rail, session_timeout_seconds=value)


def test_it_is_not_a_rail_option():
    with pytest.raises(ConfigError, match="models.default"):
        parse_config("models:\n  default: {provider: openai, model: m, "
                     "session_timeout_seconds: 900}\n")


@pytest.mark.parametrize(("document", "expected"), [
    (OPENAI, 600.0), (OPENAI + "  session_timeout_seconds: 1800\n", 1800.0)])
def test_the_cli_runs_every_session_under_the_configured_bound(
        tmp_path, monkeypatch, capsys, document, expected):
    from scrutare.engine import review
    from scrutare.interfaces import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_text(document)
    seen = []

    def stopped(config, runtime):
        seen.append(runtime)
        raise review.ReviewRunError("stopped here")
    monkeypatch.setattr(cli, "preflight_review", stopped)
    assert cli.main(["review", "--pr", "https://github.com/owner/repo/pull/12",
                     "--nare-executable", sys.executable]) == 1
    assert [runtime.timeout_seconds for runtime in seen] == [expected]
    assert seen[0].max_turns == 50


@pytest.mark.parametrize(("document", "expected"), [
    (OPENAI, 600.0), (OPENAI + "  session_timeout_seconds: 2400\n", 2400.0)])
def test_the_mcp_server_runs_every_session_under_the_configured_bound(
        tmp_path, monkeypatch, document, expected):
    module = importlib.import_module("scrutare.interfaces.mcp")
    from scrutare.engine import review
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_text(document)
    seen = []

    def stopped(config, runtime):
        seen.append(runtime)
        raise review.ReviewRunError("stopped here")
    monkeypatch.setattr(module, "preflight_review", stopped)
    with pytest.raises(review.ReviewRunError):
        asyncio.run(module._review({"pr": "1", "nare_executable": sys.executable}))
    assert [runtime.timeout_seconds for runtime in seen] == [expected]


@pytest.mark.parametrize(("document", "flag", "expected"), [
    (OPENAI, None, 600.0),
    (OPENAI, 900.0, 900.0),
    (OPENAI + "  session_timeout_seconds: 1800\n", None, 1800.0),
    (OPENAI + "  session_timeout_seconds: 1800\n", 1800.0, 1800.0),
])
def test_the_evaluator_prefers_the_configuration_then_the_flag_then_600(
        document, flag, expected):
    from scrutare.evaluation.__main__ import session_timeout
    assert session_timeout(parse_config(document), flag) == expected


def test_the_evaluator_refuses_a_flag_that_disagrees_with_the_configuration(tmp_path):
    from scrutare.evaluation.__main__ import session_timeout
    config = parse_config(OPENAI + "  session_timeout_seconds: 1800\n")
    with pytest.raises(ValueError, match="--timeout-seconds 600 disagrees with "
                                         "models.session_timeout_seconds 1800"):
        session_timeout(config, 600.0)
    (tmp_path / "config.yaml").write_text(OPENAI + "  session_timeout_seconds: 1800\n")
    completed = subprocess.run(
        [sys.executable, "-m", "scrutare.evaluation", "run", "--corpus", str(tmp_path),
         "--config", str(tmp_path / "config.yaml"), "--output", str(tmp_path / "out"),
         "--nare-executable", sys.executable, "--timeout-seconds", "600"],
        capture_output=True, text=True)
    assert completed.returncode == 2
    assert "disagrees with models.session_timeout_seconds" in completed.stderr
    assert not (tmp_path / "out").exists()


APP_CONFIG = (b"models:\n  default:\n    provider: openai\n"
              b"    base_url: https://models.example/v1\n    model: example\n")


def app_job(tmp_path, raw, *, deadline):
    store, job = claimed(tmp_path)

    class Client(TrustedClient):
        def configuration(self, *args):
            return raw
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root), b"")

    def preflight(*args):
        pytest.fail("a refused job must not run its preflight")
    run_job = importlib.import_module("scrutare.interfaces.app_worker").run_job
    run_job(replace(settings(tmp_path), job_timeout=deadline), store, job, client=Client(),
            runner=runner, preflight=preflight)
    return store.get(job["id"]), calls


@pytest.mark.parametrize(("extra", "deadline", "message"), [
    (b"  session_timeout_seconds: 3300\n", 3300,
     "repository models.session_timeout_seconds (3300) does not fit inside the App job "
     "deadline (3300 seconds); lower it or raise SCRUTARE_APP_JOB_TIMEOUT"),
    (b"  session_timeout_seconds: 7200\n", 3300,
     "repository models.session_timeout_seconds (7200) does not fit"),
    (b"  session_timeout_seconds: 3000\n  preflight: {route: true}\n"
     b"  capacity: {mode: fixed, wait_seconds: 300, refusals: 5}\n", 3300,
     "repository models.session_timeout_seconds (3000 plus 300 capacity wait seconds) does "
     "not fit"),
])
def test_the_app_refuses_a_session_timeout_that_cannot_fit_its_job_deadline(
        tmp_path, extra, deadline, message):
    saved, calls = app_job(tmp_path, APP_CONFIG + extra, deadline=deadline)
    assert calls == []
    assert saved["status"] == "failed"
    assert saved["result"]["delivery"] == "not_sent"
    assert saved["result"]["error"].startswith(message)


@pytest.mark.parametrize(("extra", "deadline"), [
    (b"", 300),  # Unset keeps 600 and is never refused, whatever the deadline.
    (b"  session_timeout_seconds: 1800\n", 3300),
    (b"  session_timeout_seconds: 3000\n"
     b"  capacity: {mode: fixed, wait_seconds: 299, refusals: 5}\n", 3300),
])
def test_a_session_timeout_that_fits_runs_the_same_cli(tmp_path, extra, deadline):
    saved, calls = app_job(tmp_path, APP_CONFIG + extra, deadline=deadline)
    assert saved["status"] == "posted" and len(calls) == 1
    # The CLI reads the timeout from the same trusted bytes; the argv names no bound.
    assert not any(item.startswith("--timeout") for item in calls[0])


def invocation(run_dir, persona, attempt, **fields):
    path = run_dir / "sessions" / persona / f"attempt-{attempt:04d}"
    path.mkdir(parents=True)
    (path / "invocation.json").write_text(json.dumps(fields))


def test_replay_binds_a_configured_timeout_to_every_session(tmp_path):
    document = configured(1800).to_dict()
    invocation(tmp_path, "security", 1, timeout_seconds=1800.0)
    invocation(tmp_path, "senior-dev", 1, timeout_seconds=1800)
    assert _session_timeout_issues(tmp_path, document) == ()
    invocation(tmp_path, "senior-dev", 2, timeout_seconds=600.0)
    invocation(tmp_path, "devops", 1)
    assert [(issue.code, issue.path, issue.severity)
            for issue in _session_timeout_issues(tmp_path, document)] == [
        ("session_timeout_unrecorded", "sessions/devops/attempt-0001/invocation.json",
         "invalid"),
        ("session_timeout_unrecorded", "sessions/senior-dev/attempt-0002/invocation.json",
         "invalid")]


def test_replay_does_not_audit_a_run_without_a_configured_timeout(tmp_path):
    invocation(tmp_path, "security", 1, timeout_seconds=900.0)
    assert _session_timeout_issues(tmp_path, parse_config(OPENAI).to_dict()) == ()
    assert _session_timeout_issues(tmp_path, {"models": 3}) == ()
    assert [issue.code for issue in _session_timeout_issues(
        tmp_path, {"models": {"session_timeout_seconds": "1800"}})] == [
        "session_timeout_unrecorded"]
