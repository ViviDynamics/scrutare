"""The actual HTTP listener authenticates webhook and performer API requests."""

import contextlib
import json
import os
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_app import payload, settings, signed

from scrutare.interfaces.app import JobStore
from scrutare.interfaces.app_server import make_server


def test_http_webhook_performer_api_and_private_job_status(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    server = make_server(config, store, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        body, signature = signed(payload())
        with urlopen(Request(origin + "/webhook", data=body, headers={
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery",
            "X-Hub-Signature-256": signature})) as response:
            assert response.status == 202
            job = json.load(response)
        with pytest.raises(HTTPError) as error:
            urlopen(origin + "/api/jobs/" + job["id"])
        assert error.value.code == 401
        with urlopen(Request(origin + "/api/jobs/" + job["id"], headers={
            "Authorization": "Bearer api-secret"})) as response:
            assert json.load(response)["id"] == job["id"]
        with urlopen(Request(origin + "/api/reviews", data=json.dumps({
            "repository": "owner/repo", "number": 13, "installation": 456,
            "request_id": "performer-1"}).encode(), headers={
                "Authorization": "Bearer api-secret"})) as response:
            assert json.load(response)["number"] == 13
        with pytest.raises(HTTPError) as error:
            urlopen(Request(origin + "/api/reviews", data=b"{}", headers={
                "Authorization": "Bearer wrong-token"}))
        assert error.value.code == 401
        with urlopen(origin + "/healthz") as response:
            assert json.load(response) == {"status": "ok"}
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def environment(tmp_path):
    key = tmp_path / "key.pem"
    key.write_text("placeholder; signing is not exercised here\n")
    return {"SCRUTARE_APP_ID": "123", "SCRUTARE_APP_PRIVATE_KEY": str(key),
            "SCRUTARE_APP_WEBHOOK_SECRET": "webhook-secret",
            "SCRUTARE_APP_API_TOKEN": "api-secret",
            "SCRUTARE_APP_INSTALLATIONS": '{"owner/repo": 456}',
            "SCRUTARE_APP_MODEL_ORIGINS": '["https://models.example"]',
            "SCRUTARE_APP_DATA_DIR": str(tmp_path / "data")}


@pytest.mark.parametrize("change", [
    {}, {"SCRUTARE_APP_ID": None}, {"SCRUTARE_APP_ID": "0"},
    {"SCRUTARE_APP_PRIVATE_KEY": "/nonexistent/key.pem"},
    {"SCRUTARE_APP_WEBHOOK_SECRET": ""}, {"SCRUTARE_APP_API_TOKEN": None},
    {"SCRUTARE_APP_INSTALLATIONS": "{}"}, {"SCRUTARE_APP_INSTALLATIONS": '{"repo": 456}'},
    {"SCRUTARE_APP_MODEL_ORIGINS": None}, {"SCRUTARE_APP_MODEL_ORIGINS": "[]"},
    {"SCRUTARE_APP_MODEL_ORIGINS": '"https://models.example"'},
    {"SCRUTARE_APP_MODEL_ORIGINS": '{"https://models.example": 1}'},
    {"SCRUTARE_APP_MODEL_ORIGINS": '["http://models.example"]'},
    {"SCRUTARE_APP_JOB_TIMEOUT": "0"}, {"SCRUTARE_APP_MAX_PENDING": "many"},
])
def test_startup_requires_identity_secrets_allowlist_and_https_origins(
        tmp_path, monkeypatch, change):
    from scrutare.interfaces.app import AppError
    from scrutare.interfaces.app_server import load_settings
    for key in [name for name in os.environ if name.startswith("SCRUTARE_APP_")]:
        monkeypatch.delenv(key)
    for key, value in (environment(tmp_path) | change).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    if not change:
        assert load_settings().model_origins == ("https://models.example",)
    else:
        with pytest.raises(AppError):
            load_settings()


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def status(request):
    try:
        with urlopen(request, timeout=5) as response:
            return response.status
    except HTTPError as error:
        return error.code


def raw(port, data):
    connection = socket.create_connection(("127.0.0.1", port), timeout=10)
    connection.sendall(data)
    return connection


def test_slow_clients_cannot_starve_health_or_outlive_the_request_deadline(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    server = make_server(settings(tmp_path), store, "127.0.0.1", 0, deadline=1)
    with serving(server) as origin:
        started = time.monotonic()
        # One trickles a large body, the other never finishes its request line.
        body = raw(server.server_port, b"POST /webhook HTTP/1.1\r\nContent-Length: 900000\r\n\r\nx")
        line = raw(server.server_port, b"POST /web")
        try:
            assert status(origin + "/healthz") == 200
            assert time.monotonic() - started < 1
            for connection in (body, line):
                # The whole-request deadline closes both without any further bytes.
                received = connection.recv(65536)
                assert received == b"" or b" 400 " in received
            assert time.monotonic() - started < 5
        finally:
            body.close()
            line.close()
    assert store.claim() is None


def test_connection_cap_refuses_excess_clients_then_recovers(tmp_path):
    server = make_server(settings(tmp_path), JobStore(tmp_path / "jobs.sqlite"), "127.0.0.1", 0,
                         deadline=1, connections=2)
    with serving(server) as origin:
        held = [raw(server.server_port, b"GET /hea") for _ in range(2)]
        try:
            time.sleep(0.2)
            refused = raw(server.server_port, b"GET /healthz HTTP/1.0\r\n\r\n")
            try:
                assert refused.recv(65536) == b""
            except ConnectionError:
                pass
            refused.close()
            time.sleep(1.5)  # the held requests expire and return their slots
            assert status(origin + "/healthz") == 200
        finally:
            for connection in held:
                connection.close()


def test_http_refusals_are_exact_and_never_admit_work(tmp_path):
    config = replace(settings(tmp_path), max_pending=1)
    store = JobStore(tmp_path / "jobs.sqlite", max_pending=1)
    auth = {"Authorization": "Bearer api-secret"}
    def review(**changes):
        document = {"repository": "owner/repo", "number": 13, "installation": 456,
                    "request_id": "one"} | changes
        return Request(origin + "/api/reviews", data=json.dumps(document).encode(), headers=auth)
    def webhook(document, **headers):
        body, signature = signed(document)
        return Request(origin + "/webhook", data=body, headers={
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d",
            "X-Hub-Signature-256": signature} | headers)
    def framed(head):
        connection = raw(server.server_port, head)
        try:
            return connection.recv(65536).split(b"\r\n", 1)[0]
        finally:
            connection.close()
    server = make_server(config, store, "127.0.0.1", 0)
    with serving(server) as origin:
        outsider = payload()
        outsider["repository"]["full_name"] = "other/repo"
        outsider["pull_request"]["base"]["repo"]["full_name"] = "other/repo"
        assert status(webhook(outsider)) == 403
        assert status(webhook(payload(), **{"X-Hub-Signature-256": "sha256=" + "0" * 64})) == 401
        assert status(review(repository="other/repo")) == 403
        assert status(review(extra=True)) == 400
        assert status(review(request_id="")) == 400
        assert status(Request(origin + "/api/reviews", data=b"[" * 200000, headers=auth)) == 400
        assert status(Request(origin + "/api/jobs/missing", headers=auth)) == 404
        assert status(Request(origin + "/elsewhere", data=b"{}")) == 404
        post = b"POST /api/reviews HTTP/1.1\r\nAuthorization: Bearer api-secret\r\n"
        assert b" 413 " in framed(post + b"Content-Length: 1048577\r\n\r\n")
        assert b" 400 " in framed(post + b"\r\n")
        assert b" 400 " in framed(post + b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}")
        assert b" 400 " in framed(post + b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n")
        assert b" 400 " in framed(post + b"Content-Length: -1\r\n\r\n")
        assert store.claim() is None
        # Admission: idempotent key, conflicting reuse, then the bounded queue.
        assert status(review()) == 202
        assert status(review()) == 202
        assert status(review(number=14)) == 409
        assert status(review(request_id="two")) == 503
        assert status(webhook(payload())) == 503


def test_health_listener_is_separate_and_reports_only_worker_liveness(tmp_path):
    from scrutare.interfaces.app_server import make_health_server
    alive = [True]
    with serving(make_health_server("127.0.0.1", 0, lambda: alive[0])) as origin:
        assert status(origin + "/healthz") == 200
        assert status(origin + "/api/jobs/x") == 404
        alive[0] = False
        assert status(origin + "/healthz") == 503


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_service_process_serves_both_listeners_and_stops_cleanly_on_sigterm(tmp_path):
    import signal
    import subprocess
    import sys
    port, health = free_port(), free_port()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SCRUTARE_APP_")} | environment(tmp_path) | {
        "SCRUTARE_APP_PORT": str(port), "SCRUTARE_APP_HEALTH_PORT": str(health),
        "SCRUTARE_APP_RECONCILE_SECONDS": "0"}
    command = [sys.executable, "-m", "scrutare.interfaces.app_server"]
    process = subprocess.Popen(command, env=env, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                if status(f"http://127.0.0.1:{health}/healthz") == 200:
                    break
            except OSError:
                time.sleep(0.1)
        assert status(f"http://127.0.0.1:{health}/healthz") == 200
        assert status(f"http://127.0.0.1:{port}/healthz") == 200
        assert status(f"http://127.0.0.1:{port}/api/jobs/x") == 401
        # The data directory lock admits exactly one worker.
        second = subprocess.run(command, env=env | {
            "SCRUTARE_APP_PORT": str(free_port()), "SCRUTARE_APP_HEALTH_PORT": str(free_port())},
            capture_output=True, timeout=20)
        assert second.returncode == 1
        assert second.stderr.startswith(b"scrutare-app: startup failed")
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=20) == 0
        assert process.stderr.read() == b""
    finally:
        process.kill()
        process.wait()
        process.stderr.close()


@pytest.mark.parametrize("script", [
    "import time; time.sleep(60)",
    "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('up', flush=True);"
    " time.sleep(60)",
])
def test_worker_process_enforces_deadline_on_the_cli(script):
    import subprocess
    import sys

    from scrutare.interfaces.app_server import WorkerProcess
    runner = WorkerProcess(grace=1)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        runner([sys.executable, "-c", script], capture_output=True, shell=False, timeout=1)
    assert time.monotonic() - started < 20
    assert runner.process is None


def test_deadline_kill_reaches_sessions_the_cli_started_in_their_own_groups(tmp_path):
    import subprocess
    import sys

    from scrutare.interfaces.app_server import WorkerProcess
    marker = tmp_path / "session.pid"
    ignore = "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
    # Like a nare session: a new session, outside the CLI's process group.
    script = ignore + (
        "import subprocess, sys, pathlib; "
        f"child = subprocess.Popen([sys.executable, '-c', {ignore + 'time.sleep(60)'!r}], "
        "start_new_session=True); "
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(60)")
    with pytest.raises(subprocess.TimeoutExpired):
        WorkerProcess(grace=1)([sys.executable, "-c", script], capture_output=True,
                               shell=False, timeout=5)
    session = int(marker.read_text())
    for _ in range(50):
        # Gone, or a zombie awaiting its reaper: either way it no longer runs.
        try:
            if Path(f"/proc/{session}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                break
        except (FileNotFoundError, ProcessLookupError):
            break
        time.sleep(0.1)
    else:
        pytest.fail("orphaned session survived the deadline")
