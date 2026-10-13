"""The actual HTTP listener authenticates webhook and performer API requests."""

import json
import threading
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


def test_startup_requires_identity_secrets_and_operator_allowlist(tmp_path, monkeypatch):
    from scrutare.interfaces.app_server import load_settings
    for key in ("SCRUTARE_APP_ID", "SCRUTARE_APP_INSTALLATIONS",
                "SCRUTARE_APP_WEBHOOK_SECRET", "SCRUTARE_APP_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    from scrutare.interfaces.app import AppError
    with pytest.raises(AppError):
        load_settings()


@pytest.mark.parametrize("script", [
    "import time; time.sleep(60)",
    "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); print('up', flush=True);"
    " time.sleep(60)",
])
def test_worker_process_enforces_deadline_on_the_whole_cli_group(script):
    import subprocess
    import sys
    import time

    from scrutare.interfaces.app_server import WorkerProcess
    runner = WorkerProcess(grace=1)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        runner([sys.executable, "-c", script], capture_output=True, shell=False, timeout=1)
    assert time.monotonic() - started < 20
    assert runner.process is None
