"""Opt-in route preflight: each typed reason against a fake HTTP route, offline."""

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_app import settings
from test_app_worker import cli_result

from scrutare.config import (
    PREFLIGHT_MAX_TIMEOUT_SECONDS,
    ConfigError,
    PreflightSettings,
    parse_config,
)
from scrutare.engine.preflight import (
    REASONS,
    PreflightError,
    PreflightReport,
    models_url,
    request_argv,
    run_preflight,
)
from scrutare.engine.session_models import NareCapability, NareFlag, NareRuntime
from scrutare.interfaces.app import JobStore
from scrutare.interfaces.app_worker import preflight_delay, run_job, work_once

KEY = "preflight-test-key"


class Route:
    """A loopback OpenAI-compatible route whose answers each test scripts."""

    def __init__(self):
        self.requests = []
        self.models = (200, {"data": [{"id": "m"}, {"id": "other"}]}, {})
        self.chat = None
        route = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                route.requests.append(("GET", self.path, self._headers()))
                status, body, headers = route.models
                self._send(status, body, headers)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["content-length"])))
                route.requests.append(("POST", self.path, self._headers(), payload))
                route.chat(self, payload)

            def _headers(self):
                return {name.lower(): value for name, value in self.headers.items()}

            def _send(self, status, body, headers=None):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def route(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    served = Route()
    yield served
    served.close()


def configured(url, *, route=True, request=False, timeout=None, provider="openai",
               extra=""):
    block = {"route": route, "request": request}
    if timeout is not None:
        block["timeout_seconds"] = timeout
    return parse_config(json.dumps({"models": {
        "default": {"provider": provider, "model": "m",
                    **({"base_url": url} if provider == "openai" else {})},
        "preflight": block}}) if not extra else extra)


def preflight(config, **kwargs):
    return asyncio.run(run_preflight(config, NareRuntime(Path("/absent/nare")), **kwargs))


def failure(config, **kwargs) -> PreflightReport:
    with pytest.raises(PreflightError) as caught:
        preflight(config, **kwargs)
    return caught.value.report


# Configuration


def test_absent_or_disabled_preflight_keeps_canonical_bytes_and_sends_nothing(route):
    plain = parse_config(json.dumps({"models": {"default": {
        "provider": "openai", "model": "m", "base_url": route.url}}}))
    for off in ({}, {"route": False, "request": False}):
        same = parse_config(json.dumps({"models": {"default": {
            "provider": "openai", "model": "m", "base_url": route.url}, "preflight": off}}))
        assert same == plain and same.to_dict() == plain.to_dict()
    assert "preflight" not in plain.to_dict()["models"]
    assert plain.models.preflight == PreflightSettings()
    assert preflight(plain) is None
    assert route.requests == []


def test_enabled_preflight_records_every_value_and_round_trips(route):
    config = configured(route.url)
    assert config.to_dict()["models"]["preflight"] == {
        "route": True, "request": False, "timeout_seconds": 30}
    assert parse_config(json.dumps(config.to_dict())) == config


@pytest.mark.parametrize(("block", "message"), [
    ({"route": "yes"}, "models.preflight.route: expected a boolean"),
    ({"request": 1}, "models.preflight.request: expected a boolean"),
    ({"timeout_seconds": 5}, "models.preflight.timeout_seconds: requires route or request"),
    ({"route": True, "timeout_seconds": 0}, "expected a positive finite number"),
    ({"route": True, "timeout_seconds": True}, "expected a positive finite number"),
    ({"route": True, "timeout_seconds": PREFLIGHT_MAX_TIMEOUT_SECONDS + 1}, "at most 300"),
    ({"route": True, "unknown": 1}, "models.preflight.unknown: unknown field"),
])
def test_preflight_block_validation(block, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(json.dumps({"models": {"default": {"provider": "openai", "model": "m"},
                                            "preflight": block}}))


def test_route_check_requires_an_openai_rail():
    with pytest.raises(ConfigError, match="models.preflight.route: requires provider openai"):
        parse_config(json.dumps({"models": {"default": {"model": "m"},
                                            "preflight": {"route": True}}}))
    # The request check runs on any rail.
    parse_config(json.dumps({"models": {"default": {"model": "m"},
                                        "preflight": {"request": True}}}))


# The route check


def test_route_that_lists_the_model_passes_with_the_credential_in_a_header_only(route):
    report = preflight(configured(route.url + "/"))
    assert report.passed and report.to_dict()["status"] == "passed"
    (check,) = report.checks
    assert check["check"] == "route" and check["status"] == "passed"
    assert check["url"] == route.url + "/models" and check["http_status"] == 200
    assert check["listed"] == 2
    (method, path, headers), = route.requests
    assert (method, path) == ("GET", "/v1/models")
    assert headers["authorization"] == "Bearer " + KEY
    assert KEY not in json.dumps(report.to_dict())


def test_default_openai_base_url_is_where_nare_roots_the_rail():
    config = parse_config(b"models: {default: {provider: openai, model: m}}")
    assert models_url(config.models.default) == "https://api.openai.com/v1/models"


def test_model_missing(route):
    route.models = (200, {"data": [{"id": "another-model"}]}, {})
    report = failure(configured(route.url))
    assert report.reason == "model_missing"
    assert report.checks[0]["listed"] == 1


@pytest.mark.parametrize(("status", "reason"), [
    (502, "gateway_error"), (503, "gateway_error"), (500, "gateway_error"),
    (401, "credential_refused"), (403, "credential_refused"),
    (404, "route_refused"), (400, "route_refused"),
])
def test_http_failures_have_typed_reasons(route, status, reason):
    route.models = (status, {"error": "scripted"}, {})
    report = failure(configured(route.url))
    assert report.reason == reason and report.checks[0]["http_status"] == status


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"data": {}}',
                                  b'{"data": [{"name": "m"}]}', b'{"data": [{"id": 1}]}'])
def test_unreadable_model_list_is_route_invalid(route, body):
    route.models = (200, body, {})
    assert failure(configured(route.url)).reason == "route_invalid"


def test_redirect_is_refused_before_the_credential_can_follow(route):
    elsewhere = Route()
    try:
        route.models = (302, b"", {"Location": elsewhere.url + "/models"})
        assert failure(configured(route.url)).reason == "route_refused"
        assert elsewhere.requests == []
    finally:
        elsewhere.close()


def test_unreachable_route(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    report = failure(configured(f"http://127.0.0.1:{port}/v1"))
    assert report.reason == "route_unreachable"


def test_slow_route_is_unreachable_within_the_bound(route):
    gate = threading.Event()
    answer_get = route.server.RequestHandlerClass.do_GET

    def held(self):
        gate.wait(5)
        answer_get(self)
    route.server.RequestHandlerClass.do_GET = held
    try:
        started = time.monotonic()
        assert failure(configured(route.url, timeout=0.5)).reason == "route_unreachable"
        assert time.monotonic() - started < 4
    finally:
        gate.set()


def test_missing_credential_sends_nothing(route, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    assert failure(configured(route.url)).reason == "credential_missing"
    assert route.requests == []


def test_origin_outside_the_allowlist_is_never_asked(route):
    report = failure(configured(route.url), origins=("https://models.example",))
    assert report.reason == "origin_refused"
    assert route.requests == []
    allowed = "http://127.0.0.1:" + str(route.server.server_port)
    assert preflight(configured(route.url), origins=(allowed,)).passed


def test_ambient_proxy_settings_are_not_used(route, monkeypatch):
    # nare's sessions run without proxy variables, and so does the check.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    assert preflight(configured(route.url)).passed


def test_anthropic_rail_is_skipped_with_a_recorded_reason(route):
    config = parse_config(json.dumps({"models": {
        "default": {"provider": "openai", "model": "m", "base_url": route.url},
        "overrides": {"security": {"provider": "anthropic", "base_url": None}},
        "preflight": {"route": True}}}))
    report = preflight(config)
    assert [check["status"] for check in report.checks] == ["passed", "skipped"]
    assert report.checks[1]["provider"] == "anthropic"
    assert "openai routes only" in report.checks[1]["skipped"]
    assert len(route.requests) == 1


def test_one_route_is_asked_once_and_the_first_failure_stops(route):
    config = parse_config(json.dumps({"models": {
        "default": {"provider": "openai", "model": "m", "base_url": route.url},
        "overrides": {"security": {"model": "m"}, "devops": {"model": "absent"},
                      "junior-dev": {"model": "other"}},
        "preflight": {"route": True}}}))
    report = failure(config)
    assert report.reason == "model_missing"
    assert [check["model"] for check in report.checks] == ["m", "absent"]
    assert len(route.requests) == 2


def test_every_reason_is_typed():
    assert set(REASONS) >= {"route_unreachable", "model_missing", "gateway_error",
                            "credential_refused", "origin_refused", "request_failed",
                            "request_timeout", "answer_invalid", "runtime_refused",
                            "encoding_refused"}


# The request check (argv shape runs everywhere; execution needs installed nare)


def capability(**extra):
    flags = tuple(NareFlag(name, True) for name in (
        "--provider", "--model", "--base-url", "--tools", "--root", "--system", "--contract",
        "--schema", "--budget-tokens", "--session", "--resume", "--max-turns", "--max-tokens",
        "--max-cutoffs", "--request-retries", "--max-corrections", "--prompt-file",
        "--structured-output")) + (NareFlag("--jsonl", False), NareFlag("--yes", False),
                                   NareFlag("--stream", False))
    return NareCapability("2026.11.107", 3, ("json_schema",), flags=flags,
                          structured_output_providers=("openai",), **extra)


def test_request_argv_is_tool_free_carries_the_rail_options_and_no_credential(
        tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    config = parse_config(json.dumps({"models": {"default": {
        "provider": "openai", "model": "m", "base_url": "https://litellm.example/v1",
        "structured_output": "json_schema", "max_output_tokens": 512}}}))
    argv = request_argv(Path("/bin/nare"), config.models.default, capability(), tmp_path)
    assert argv[:4] == ["/bin/nare", "run", "--prompt-file", str(tmp_path / "prompt.txt")]
    assert argv[argv.index("--tools") + 1] == "none"
    assert argv[argv.index("--structured-output") + 1] == "json_schema"
    assert argv[argv.index("--max-tokens") + 1] == "512"
    assert argv[argv.index("--base-url") + 1] == "https://litellm.example/v1"
    assert argv[argv.index("--request-retries") + 1] == "0"
    assert "--stream" in argv and "--max-turns" in argv
    assert not any(KEY in part for part in argv)
    assert not any(part.startswith("--capacity") for part in argv)


def installed():
    executable = os.environ.get("SCRUTARE_TEST_NARE_EXECUTABLE")
    if not executable:
        pytest.skip("SCRUTARE_TEST_NARE_EXECUTABLE names no installed nare")
    return NareRuntime(Path(executable))


def sse(handler, frames):
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.end_headers()
    for frame in frames:
        handler.wfile.write(("data: " + (frame if isinstance(frame, str)
                                         else json.dumps(frame)) + "\n\n").encode())
        handler.wfile.flush()


def answer(text):
    def chat(handler, payload):
        sse(handler, [{"choices": [{"index": 0, "delta": {"content": text},
                                    "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                      {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
                      "[DONE]"])
    return chat


def request_config(url, **rail):
    return parse_config(json.dumps({"models": {
        "default": {"provider": "openai", "model": "m", "base_url": url, **rail},
        "preflight": {"request": True, "timeout_seconds": 20}}}))


def run_request(config, runtime):
    return asyncio.run(run_preflight(config, runtime))


def test_installed_request_passes_with_the_rail_encoding(route):
    runtime = installed()
    route.chat = answer('{"ok": true}')
    report = run_request(request_config(route.url, structured_output="json_schema"), runtime)
    (check,) = report.checks
    assert check["status"] == "passed" and check["stop_reason"] == "end_turn"
    assert check["usage"] == {"input": 9, "output": 4, "total": 13}
    (_, path, headers, payload), = [r for r in route.requests if r[0] == "POST"]
    assert path == "/v1/chat/completions" and headers["authorization"] == "Bearer " + KEY
    assert payload["response_format"]["type"] == "json_schema"
    assert "tools" not in payload or not payload["tools"]


def test_installed_refused_encoding_is_encoding_refused(route):
    runtime = installed()

    def refuse(handler, payload):
        handler._send(400, {"error": {"message": "response_format is not supported"}})
    route.chat = refuse
    with pytest.raises(PreflightError) as caught:
        run_request(request_config(route.url, structured_output="json_schema"), runtime)
    check = caught.value.report.checks[0]
    assert caught.value.reason == "encoding_refused" and check["stop_reason"] is None
    assert check["failure"] == {"reason": "status", "status": 400, "transient": False,
                                "constrained": True, "attempts": 1}
    # The remote body is not recorded.
    assert "not supported" not in json.dumps(caught.value.report.to_dict())


@pytest.mark.parametrize(("status", "reason"), [
    (401, "credential_refused"), (403, "credential_refused"), (418, "request_failed")])
def test_installed_failed_request_is_classified_from_nare_failure_record(route, status, reason):
    runtime = installed()

    def refuse(handler, payload):
        handler._send(status, {"error": {"message": "no"}})
    route.chat = refuse
    with pytest.raises(PreflightError) as caught:
        run_request(request_config(route.url), runtime)
    assert caught.value.reason == reason
    assert caught.value.report.checks[0]["failure"]["status"] == status


@pytest.mark.parametrize(("failure", "reason"), [
    ({"reason": "status", "status": 422, "constrained": True}, "encoding_refused"),
    ({"reason": "status", "status": 400, "constrained": False}, "request_failed"),
    ({"reason": "status", "status": 503, "transient": True}, "gateway_error"),
    ({"reason": "overloaded", "status": None}, "gateway_error"),
    ({"reason": "connection", "status": None}, "route_unreachable"),
    ({"reason": "timeout", "status": None}, "request_timeout"),
    ({"reason": "stream", "status": None}, "request_failed"),
    ({"reason": None, "status": None}, "request_failed"),
])
def test_failure_reasons_follow_nare_failure_record(failure, reason):
    from scrutare.engine.preflight import _failure, _failure_reason
    record = _failure({"failure": failure})
    assert record is not None and _failure_reason(record) == reason


@pytest.mark.parametrize("failure", [
    None, [], {"status": "400"}, {"status": 200}, {"status": 400, "reason": 3}])
def test_a_malformed_failure_record_is_ignored(failure):
    from scrutare.engine.preflight import _failure
    assert _failure({"failure": failure}) is None


def test_installed_invalid_answer_is_answer_invalid(route):
    runtime = installed()
    route.chat = answer("no json here")
    with pytest.raises(PreflightError) as caught:
        run_request(request_config(route.url), runtime)
    assert caught.value.reason == "answer_invalid"
    assert caught.value.report.checks[0]["stop_reason"] == "schema_violation"


def test_installed_request_past_its_bound_is_request_timeout(route):
    runtime = installed()
    release = threading.Event()

    def hang(handler, payload):
        release.wait(10)
    route.chat = hang
    config = parse_config(json.dumps({"models": {
        "default": {"provider": "openai", "model": "m", "base_url": route.url},
        "preflight": {"request": True, "timeout_seconds": 2}}}))
    try:
        started = time.monotonic()
        with pytest.raises(PreflightError) as caught:
            run_request(config, runtime)
        assert caught.value.reason == "request_timeout"
        assert time.monotonic() - started < 8
    finally:
        release.set()


def test_request_check_on_an_absent_runtime_is_runtime_refused(route):
    route.chat = answer('{"ok": true}')
    with pytest.raises(PreflightError) as caught:
        run_request(request_config(route.url), NareRuntime(Path("/absent/nare")))
    assert caught.value.reason == "runtime_refused"
    assert route.requests == []


# The App worker: per-job record, bounded backoff, nothing started


CONFIG = (b"models:\n  default:\n    provider: openai\n"
          b"    base_url: https://models.example/v1\n    model: example\n"
          b"  preflight:\n    route: true\n")


class Client:
    def __init__(self, raw=CONFIG):
        self.raw = raw

    def installation_token(self, *args):
        return "scoped-token"

    def head(self, *args):
        return "a" * 40

    def configuration(self, *args):
        return self.raw


def failing(reason="gateway_error"):
    calls = []

    def check(config, app_settings, nare):
        calls.append((config, app_settings, nare))
        raise PreflightError(PreflightReport(({"check": "route", "status": "failed",
                                               "reason": reason},)))
    return check, calls


def test_failed_preflight_defers_the_job_with_backoff_and_starts_nothing(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    check, calls = failing()

    def runner(*args, **kwargs):
        pytest.fail("a failed preflight starts no review")
    before = time.time()
    assert work_once(config, store, client=Client(), runner=runner, preflight=check)
    job = store.get(_only(store))
    assert job["status"] == "queued" and job["claimed"] is None
    assert job["not_before"] >= before + 60
    assert [record["reason"] for record in job["preflight"]] == ["gateway_error"]
    assert job["preflight"][0]["status"] == "failed" and "at" in job["preflight"][0]
    # Not due yet, so nothing is claimed; once due, it is.
    assert store.claim(now=job["not_before"] - 1) is None
    assert store.claim(now=job["not_before"])["id"] == job["id"]
    assert calls[0][1].model_origins == ("https://models.example",)
    assert not (tmp_path / "repositories").exists() or not any(
        (tmp_path / "repositories").rglob("runs/*"))


def _only(store):
    with sqlite3.connect(store.path) as db:
        (identity,), = db.execute("SELECT id FROM jobs").fetchall()
    return identity


def test_backoff_doubles_and_is_capped(tmp_path):
    config = settings(tmp_path)
    assert [preflight_delay(config, n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert preflight_delay(config, 10) == 3600


def test_exhausted_preflight_ends_not_started_with_every_attempt(tmp_path):
    from dataclasses import replace
    config = replace(settings(tmp_path), preflight_attempts=3, preflight_backoff=1)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    check, calls = failing("route_unreachable")

    def runner(*args, **kwargs):
        pytest.fail("a failed preflight starts no review")
    identity = _only(store)
    for _ in range(3):
        job = store.claim(now=time.time() + 3600)
        assert job is not None
        run_job(config, store, job, client=Client(), runner=runner, preflight=check)
    job = store.get(identity)
    assert job["status"] == "not_started"
    assert job["result"]["reason"] == "route_unreachable"
    assert job["result"]["delivery"] == "not_sent"
    assert len(job["result"]["preflight"]) == 3 == len(calls)
    assert job["reviewed"] is None


def test_passed_preflight_is_recorded_and_the_cli_does_not_repeat_it(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    argvs = []

    def check(config, app_settings, nare):
        return PreflightReport(({"check": "route", "status": "passed", "reason": None},))

    def runner(argv, **kwargs):
        argvs.append(argv)
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root), b"")
    run_job(config, store, job, client=Client(), runner=runner, preflight=check)
    job = store.get(job["id"])
    assert job["status"] == "posted"
    assert [record["status"] for record in job["preflight"]] == ["passed"]
    assert argvs[0][-1] == "--skip-preflight"


def test_without_the_block_the_worker_runs_the_same_cli_and_no_preflight(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    argvs = []

    def check(*args):
        pytest.fail("no preflight without the block")

    def runner(argv, **kwargs):
        argvs.append(argv)
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root), b"")
    raw = CONFIG.split(b"  preflight:")[0]
    run_job(config, store, job, client=Client(raw), runner=runner, preflight=check)
    assert store.get(job["id"])["status"] == "posted"
    assert store.get(job["id"])["preflight"] == []
    assert "--skip-preflight" not in argvs[0]
    assert argvs[0][-2:] == ["--nare-executable", "nare"]


def test_disallowed_origin_fails_before_any_preflight(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    check, calls = failing()
    raw = CONFIG.replace(b"models.example", b"attacker.example")
    run_job(config, store, job, client=Client(raw), runner=None, preflight=check)
    assert store.get(job["id"])["status"] == "failed" and calls == []


def test_store_from_before_the_preflight_gains_its_columns(tmp_path):
    path = tmp_path / "jobs.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, delivery TEXT UNIQUE NOT NULL,
            repository TEXT NOT NULL, number INTEGER NOT NULL,
            installation INTEGER NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, result TEXT, head TEXT, reviewed TEXT,
            claimed REAL)""")
        db.execute("INSERT INTO jobs VALUES ('old', 'test', 'owner/repo', 12, 456, 'queued', "
                   "1, NULL, NULL, NULL, NULL)")
    store = JobStore(path)
    job = store.claim()
    assert job["id"] == "old" and job["preflight"] == [] and job["not_before"] is None
    store.enqueue("another", "owner/repo", 13, 456)


def test_app_settings_bound_the_retries(tmp_path):
    from dataclasses import replace

    from scrutare.interfaces.app import AppError
    for change in ({"preflight_attempts": 0}, {"preflight_attempts": 11},
                   {"preflight_backoff": 0}, {"preflight_backoff": 3601},
                   {"preflight_attempts": True}):
        with pytest.raises(AppError):
            replace(settings(tmp_path), **change)


# The CLI and the evaluation launcher


def test_cli_preflight_failure_exits_4_before_github_and_claims_no_run(
        tmp_path, monkeypatch, capsys, route):
    from scrutare.engine import github
    from scrutare.interfaces.cli import main
    route.models = (502, {"error": "scripted"}, {})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_text(json.dumps({"models": {
        "default": {"provider": "openai", "model": "m", "base_url": route.url},
        "preflight": {"route": True}}}))
    monkeypatch.setattr(github.subprocess, "run",
                        lambda *a, **k: pytest.fail("no GitHub access"))
    assert main(["review", "--pr", "https://github.com/owner/repo/pull/12",
                 "--nare-executable", sys.executable]) == 4
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["status"] == "not_started" and document["reason"] == "gateway_error"
    assert document["preflight"]["checks"][0]["http_status"] == 502
    assert "gateway_error" in output.err
    assert not (tmp_path / ".scrutare").exists()


def test_cli_skip_preflight_sends_no_check(tmp_path, monkeypatch, capsys, route):
    from scrutare.engine import review
    from scrutare.interfaces.cli import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_text(json.dumps({"models": {
        "default": {"provider": "openai", "model": "m", "base_url": route.url},
        "preflight": {"route": True}}}))

    async def stopped(*args, **kwargs):
        raise review.ReviewRunError("stopped here")
    monkeypatch.setattr("scrutare.interfaces.cli.review_pr", stopped)
    assert main(["review", "--pr", "https://github.com/owner/repo/pull/12",
                 "--nare-executable", sys.executable, "--skip-preflight"]) == 1
    assert route.requests == []


def test_evaluation_preflight_failure_claims_no_output(tmp_path, route):
    from test_evaluation_runner import corpus

    from scrutare.evaluation.runner import run_experiment
    route.models = (200, {"data": []}, {})
    config = parse_config(json.dumps({
        "models": {"default": {"provider": "openai", "model": "m", "base_url": route.url},
                   "preflight": {"route": True}},
        "budgets": {"review_max_tokens": 400, "per_persona_tokens": 100}}))
    with pytest.raises(PreflightError) as caught:
        asyncio.run(run_experiment(corpus(tmp_path / "corpus"), config, tmp_path / "out",
                                   runtime=NareRuntime(Path("/unused")),
                                   evidence_kind="offline"))
    assert caught.value.reason == "model_missing"
    assert not (tmp_path / "out").exists()


def test_evaluation_command_prints_not_started_and_exits_4(tmp_path, route):
    from test_evaluation_runner import corpus
    corpus(tmp_path / "corpus")
    route.models = (503, {"error": "scripted"}, {})
    (tmp_path / "config.yaml").write_text(json.dumps({
        "models": {"default": {"provider": "openai", "model": "m", "base_url": route.url},
                   "preflight": {"route": True}},
        "budgets": {"review_max_tokens": 400, "per_persona_tokens": 100}}))
    completed = subprocess.run(
        [sys.executable, "-m", "scrutare.evaluation", "run", "--corpus",
         str(tmp_path / "corpus"), "--config", str(tmp_path / "config.yaml"), "--output",
         str(tmp_path / "out"), "--nare-executable", "/unused", "--evidence-kind", "offline"],
        capture_output=True, env={**os.environ, "OPENAI_API_KEY": KEY}, timeout=60)
    assert completed.returncode == 4, completed.stderr
    document = json.loads(completed.stdout)
    assert document["status"] == "not_started" and document["reason"] == "gateway_error"
    assert not (tmp_path / "out").exists()
