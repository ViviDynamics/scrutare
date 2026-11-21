"""A review that outlasts one installation token posts with a refreshed one.

The worker keeps a private token file fresh while the CLI runs; the CLI reads it before
every GitHub call. The job record carries the CLI's timing and the job's own clock.
"""

import json
import os
import stat
import subprocess
import threading
import time

from test_app import settings
from test_app_worker import TrustedClient, claimed, cli_result, journal

from scrutare.engine.github import TOKEN_FILE_VARIABLE, gh_environment
from scrutare.interfaces.app import AppError
from scrutare.interfaces.app_worker import TOKEN_FILE, TokenRefresher, run_job


class RotatingClient(TrustedClient):
    """Hands out a new installation token on every request, counting them."""

    def __init__(self, fail=0):
        self.issued, self.fail = 0, fail
        self.lock = threading.Lock()

    def installation_token(self, *args):
        with self.lock:
            self.issued += 1
            if self.issued <= self.fail:
                raise AppError("GitHub App request failed; check installation permissions")
            return f"token-{self.issued}"


def wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_the_cli_reads_the_freshest_token_before_each_call(tmp_path, monkeypatch):
    path = tmp_path / "token"
    monkeypatch.setenv("GH_TOKEN", "inherited")
    monkeypatch.delenv(TOKEN_FILE_VARIABLE, raising=False)
    assert gh_environment()["GH_TOKEN"] == "inherited"
    assert gh_environment()["GH_HOST"] == "github.com"
    monkeypatch.setenv(TOKEN_FILE_VARIABLE, str(path))
    assert gh_environment()["GH_TOKEN"] == "inherited"  # Missing file: inherited token.
    path.write_text("first\n")
    assert gh_environment()["GH_TOKEN"] == "first"
    path.write_text("second")
    assert gh_environment()["GH_TOKEN"] == "second"
    for unusable in ("", "has space", "line\nbreak"):
        path.write_text(unusable)
        assert gh_environment()["GH_TOKEN"] == "inherited"


def test_the_refresher_rewrites_the_private_file_and_removes_it(tmp_path):
    client = RotatingClient()
    path = tmp_path / TOKEN_FILE
    with TokenRefresher(path, client.installation_token, initial="token-0",
                        interval=0.02) as refresher:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert wait_for(lambda: refresher.refreshes >= 2)
        assert path.read_text().startswith("token-")
    assert not path.exists() and not list(tmp_path.iterdir())


def test_a_refused_refresh_keeps_the_current_token_and_tries_again(tmp_path):
    client = RotatingClient(fail=2)
    path = tmp_path / TOKEN_FILE
    with TokenRefresher(path, client.installation_token, initial="token-0", interval=0.3,
                        retry=0.01) as refresher:
        assert wait_for(lambda: refresher.failures >= 1)
        assert path.read_text() == "token-0"  # Still the valid current token.
        assert wait_for(lambda: refresher.refreshes >= 1)
        assert refresher.failures == 2
        assert path.read_text() == "token-3"


def test_a_long_review_posts_with_a_token_issued_after_it_started(tmp_path):
    store, job = claimed(tmp_path)
    client = RotatingClient()
    seen = {}

    def runner(argv, **kwargs):
        environment = kwargs["env"]
        token_path = environment[TOKEN_FILE_VARIABLE]
        seen["path"] = token_path
        seen["first"] = open(token_path).read()
        # The review runs past several refreshes, then posts with whatever is current.
        assert wait_for(lambda: open(token_path).read() != seen["first"])
        seen["posting"] = open(token_path).read()
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        timing = {"schema_version": 1, "wall_seconds": 4000.0, "sessions": 1}
        return subprocess.CompletedProcess(argv, 0, cli_result(root, timing=timing), b"")

    run_job(settings(tmp_path), store, job, client=client, runner=runner, refresh_seconds=0.02)
    saved = store.get(job["id"])
    assert saved["status"] == "posted"
    assert seen["first"] == "token-1" and seen["posting"] != "token-1"
    assert seen["path"] == str(tmp_path / "work" / job["id"] / TOKEN_FILE)
    assert not os.path.exists(seen["path"])
    result = saved["result"]
    assert result["timing"]["wall_seconds"] == 4000.0
    assert result["job_seconds"] >= 0 and result["token_refreshes"] >= 1


def test_a_job_that_stops_keeps_its_clock_and_the_run_timing(tmp_path):
    store, job = claimed(tmp_path)

    def runner(argv, **kwargs):
        run = journal(kwargs, "rejected")
        (run / "timing.json").write_text(json.dumps({"wall_seconds": 12.0}))
        return subprocess.CompletedProcess(argv, 1, b"", b"")

    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=runner)
    result = store.get(job["id"])["result"]
    assert result["timing"] == {"wall_seconds": 12.0}
    assert result["token_refreshes"] == 0 and result["job_seconds"] >= 0
    assert not (tmp_path / "work" / job["id"] / TOKEN_FILE).exists()


def test_a_job_past_its_deadline_records_how_long_it_ran(tmp_path):
    store, job = claimed(tmp_path)

    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"", stderr=b"")

    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=runner)
    saved = store.get(job["id"])
    assert saved["status"] == "interrupted" and "job_seconds" in saved["result"]
    assert not (tmp_path / "work" / job["id"] / TOKEN_FILE).exists()

