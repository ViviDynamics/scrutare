"""The actual HTTP listener authenticates webhook and performer API requests."""

import contextlib
import json
import os
import re
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
        # The unauthenticated webhook answer names the job and nothing else.
        assert job == {"status": "queued", "id": job["id"]}
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
    server = make_server(settings(tmp_path), store, "127.0.0.1", 0, deadline=3)
    with serving(server) as origin:
        # One trickles a large body, the other never finishes its request line.
        body = raw(server.server_port, b"POST /webhook HTTP/1.1\r\nContent-Length: 900000\r\n\r\nx")
        line = raw(server.server_port, b"POST /web")
        try:
            # Both are still held open, yet other requests are answered.
            assert status(origin + "/healthz") == 200
            for connection in (body, line):
                # The whole-request deadline closes both without any further bytes;
                # the ten-second socket timeout here would fail the test otherwise.
                received = connection.recv(65536)
                assert received == b"" or b" 400 " in received
        finally:
            body.close()
            line.close()
    assert store.claim() is None


def test_connection_cap_refuses_excess_clients_then_recovers(tmp_path):
    server = make_server(settings(tmp_path), JobStore(tmp_path / "jobs.sqlite"), "127.0.0.1", 0,
                         deadline=30, connections=2)
    def probe():
        connection = raw(server.server_port, b"GET /healthz HTTP/1.0\r\n\r\n")
        try:
            return connection.recv(65536)
        except ConnectionError:
            return b""
        finally:
            connection.close()
    def eventually(expected):
        for _ in range(200):
            if expected(probe()):
                return True
            time.sleep(0.05)
        return False
    with serving(server):
        held = [raw(server.server_port, b"GET /hea") for _ in range(2)]
        try:
            # Once both held requests own a slot, a further client is closed unanswered.
            assert eventually(lambda answer: answer == b"")
        finally:
            for connection in held:
                connection.close()
        # Their handlers see the close and return the slots long before the deadline.
        assert eventually(lambda answer: b" 200 " in answer)


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
        for length in (b"-1", b"+2", b"2_0", b" 2 2", b"0x2", b"2.0"):
            assert b" 400 " in framed(post + b"Content-Length: " + length + b"\r\n\r\n{}")
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


def test_service_process_serves_both_listeners_and_stops_cleanly_on_sigterm(tmp_path):
    import fcntl
    import signal
    import subprocess
    import sys
    # The service binds free ports itself and reports them, so no port is guessed here.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SCRUTARE_APP_")} | environment(tmp_path) | {
        "SCRUTARE_APP_PORT": "0", "SCRUTARE_APP_HEALTH_PORT": "0",
        "SCRUTARE_APP_RECONCILE_SECONDS": "0"}
    command = [sys.executable, "-m", "scrutare.interfaces.app_server"]
    # While another worker holds the data directory lock, startup is refused.
    data = tmp_path / "data"
    data.mkdir()
    with (data / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        second = subprocess.run(command, env=env, capture_output=True, timeout=60)
    assert second.returncode == 1
    assert second.stderr == (b"scrutare-app: startup failed; check App settings, ports and "
                             b"exclusive data lock\n")
    process = subprocess.Popen(command, env=env, stderr=subprocess.PIPE)
    try:
        started = re.fullmatch(rb"scrutare-app: listening on port ([0-9]+), "
                               rb"health on port ([0-9]+)\n", process.stderr.readline())
        assert started is not None
        port, health = int(started[1]), int(started[2])
        assert port != health
        assert status(f"http://127.0.0.1:{health}/healthz") == 200
        assert status(f"http://127.0.0.1:{health}/api/jobs/x") == 404
        assert status(f"http://127.0.0.1:{port}/healthz") == 200
        assert status(f"http://127.0.0.1:{port}/api/jobs/x") == 401
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=60) == 0
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


def gone(pid):
    """Whether a process has exited: absent, or a zombie awaiting its reaper."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return True


def test_stop_interrupts_a_running_cli_then_kills_sessions_in_their_own_groups(tmp_path):
    import sys

    from scrutare.interfaces.app import WorkerStopping
    from scrutare.interfaces.app_server import WorkerProcess
    marker = tmp_path / "session.pid"
    ignore = "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
    # Like a nare session: a new session, outside the CLI's process group. Both ignore
    # the interrupt, so only the kill after the grace period can end them.
    script = ignore + (
        "import subprocess, sys, pathlib; "
        f"child = subprocess.Popen([sys.executable, '-c', {ignore + 'time.sleep(600)'!r}], "
        "start_new_session=True); "
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(600)")
    runner = WorkerProcess(grace=1)
    outcome = []
    def run():
        outcome.append(runner([sys.executable, "-c", script], capture_output=True,
                              shell=False, timeout=600))
    thread = threading.Thread(target=run)
    thread.start()
    try:
        # Wait for the session itself, not for a guessed startup time.
        for _ in range(600):
            if marker.exists() and marker.read_text():
                break
            time.sleep(0.05)
        session = int(marker.read_text())
        assert not gone(session)
    finally:
        runner.stop()
        thread.join(60)
    assert not thread.is_alive()
    assert outcome[0].returncode < 0
    for _ in range(600):
        if gone(session):
            break
        time.sleep(0.05)
    assert gone(session), "orphaned session survived the kill"
    # After stop, a claimed job is released rather than started.
    with pytest.raises(WorkerStopping):
        runner([sys.executable, "-c", "pass"], capture_output=True, shell=False, timeout=5)
