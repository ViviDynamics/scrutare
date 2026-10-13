"""App installation credentials stay isolated from prompts and other callers."""

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from test_app import settings

from scrutare.interfaces.app import AppError, JobStore
from scrutare.interfaces.app_worker import GitHubAppClient, run_job


def cli_result(root, **changes):
    """Install and return the current CLI success document for one run directory."""
    document = {"schema_version": 1, "scrutare_version": "2026.10.2", "status": "posted",
                "run_dir": str(root), "head_sha": "a" * 40, "verdict": "approve",
                "rule": "no_blocking_findings", "panel_status": "complete",
                "usage": {}, "accounting_complete": True, "review": {"id": 1}} | changes
    raw = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
    (root / "result.json").write_bytes(raw)
    return raw


def test_app_jwt_is_short_lived_rsa_and_token_request_is_repo_scoped(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = settings(tmp_path)
    config.private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    requests = []
    def request(method, path, token, body):
        requests.append((method, path, token, body))
        return {"token": "installation-token"}
    client = GitHubAppClient(config, request=request)
    assert client.installation_token("owner/repo", 456) == "installation-token"
    method, path, token, body = requests[0]
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], issuer="123")
    assert claims["exp"] - claims["iat"] <= 600
    assert path == "/app/installations/456/access_tokens"
    assert method == "POST"
    assert body == {"repositories": ["repo"], "permissions": {
        "contents": "read", "pull_requests": "write", "issues": "read"}}


def test_worker_fetches_trusted_repo_config_runs_same_cli_and_keeps_secrets_out(tmp_path,
                                                                           monkeypatch):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    monkeypatch.setenv("SCRUTARE_APP_API_TOKEN", "do-not-pass")
    monkeypatch.setenv("GH_TOKEN", "developer-token")
    monkeypatch.setenv("OPENAI_API_KEY", "model-token")
    calls = []
    class Client:
        def installation_token(self, repo, installation):
            assert (repo, installation) == ("owner/repo", 456)
            return "app-installation-token"
        def head(self, repo, number, token):
            return "a" * 40
        def configuration(self, repo, token):
            return (b"models:\n  default:\n    provider: openai\n"
                    b"    base_url: https://models.example/v1\n    model: example\n")
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root), b"")
    run_job(config, store, job, client=Client(), runner=runner)
    assert store.get(job["id"])["status"] == "posted"
    argv, kwargs = calls[0]
    assert argv[1:4] == ["-m", "scrutare", "review"]
    assert "https://github.com/owner/repo/pull/12" in argv
    assert kwargs["env"]["GH_TOKEN"] == "app-installation-token"
    assert kwargs["env"]["OPENAI_API_KEY"] == "model-token"
    assert "SCRUTARE_APP_API_TOKEN" not in kwargs["env"]
    assert "developer-token" not in kwargs["env"].values()
    assert os.environ["GH_TOKEN"] == "developer-token"


def test_repo_config_cannot_redirect_provider_credentials(tmp_path):
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    job = store.claim()
    class Client:
        def installation_token(self, *args):
            return "scoped-token"
        def head(self, *args):
            return "a" * 40
        def configuration(self, *args):
            return (b"models:\n  default:\n    provider: openai\n"
                    b"    base_url: https://attacker.example/v1\n    model: example\n")
    def runner(*args, **kwargs):
        pytest.fail("must reject config before execution")
    run_job(config, store, job, client=Client(), runner=runner)
    assert store.get(job["id"])["status"] == "failed"
    assert "origin" in store.get(job["id"])["result"]["error"]


def test_app_client_configuration_uses_default_branch_not_pr_head(tmp_path):
    paths = []
    def request(method, path, token, body):
        paths.append(path)
        if path == "/repos/owner/repo":
            return {"default_branch": "main", "full_name": "owner/repo"}
        assert path == "/repos/owner/repo/contents/scrutare.yaml?ref=main"
        import base64
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(
            b"models:\n  default:\n    model: example\n").decode()}
    client = GitHubAppClient(settings(tmp_path), request=request)
    assert b"example" in client.configuration("owner/repo", "scoped-token")
    assert len(paths) == 2


def test_app_client_never_leaks_http_errors(tmp_path):
    from urllib.error import HTTPError

    from scrutare.interfaces.app_worker import github_request
    def opener(*args, **kwargs):
        raise HTTPError("https://api.github.com", 401, "secret-token", {}, None)
    with pytest.raises(AppError, match="GitHub App request failed") as error:
        github_request("GET", "/app", "secret-token", None, opener=opener)
    assert "secret-token" not in str(error.value)


class TrustedClient:
    def installation_token(self, *args):
        return "scoped-token"
    def head(self, *args):
        return "a" * 40
    def configuration(self, *args):
        return (b"models:\n  default:\n    provider: openai\n"
                b"    base_url: https://models.example/v1\n    model: example\n")


def claimed(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("test", "owner/repo", 12, 456)
    return store, store.claim()


def journal(kwargs, status):
    """Leave the run a CLI would have created, with its posting journal in one state."""
    run = kwargs["cwd"] / ".scrutare" / "runs" / "run-test"
    run.mkdir(parents=True)
    if status is not None:
        (run / "posting.json").write_text(json.dumps({"status": status, "head_sha": "c" * 40}))
    return run


@pytest.mark.parametrize(("code", "posting", "status", "delivery"), [
    (1, "absent-run", "failed", "not_sent"),       # refused before capture
    (1, None, "failed", "not_sent"),               # captured, never prepared a review
    (1, "prepared", "failed", "not_sent"),
    (1, "rejected", "failed", "not_sent"),
    (1, "posted", "failed", "confirmed"),          # delivered, later persistence failed
    (1, "sending", "interrupted", "uncertain"),    # the App cannot reconcile its own login
    (1, "unknown", "interrupted", "uncertain"),
    (1, "novel", "interrupted", "uncertain"),
    (2, "absent-run", "failed", "not_sent"),
    (130, "prepared", "interrupted", "not_sent"),
    (130, "sending", "interrupted", "uncertain"),
    (-9, "posted", "interrupted", "confirmed"),
])
def test_job_record_says_whether_a_review_was_delivered_whatever_the_exit_code(
        tmp_path, code, posting, status, delivery):
    store, job = claimed(tmp_path)
    def runner(argv, **kwargs):
        if posting != "absent-run":
            journal(kwargs, posting)
        return subprocess.CompletedProcess(argv, code, b"", b"scrutare: stopped\n")
    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=runner)
    saved = store.get(job["id"])
    assert (saved["status"], saved["result"]["delivery"]) == (status, delivery)
    assert saved["result"]["exit_code"] == code
    # The captured head is remembered exactly when a review of it may exist.
    assert saved["reviewed"] == (None if delivery == "not_sent" else "c" * 40)
    work = tmp_path / "work" / job["id"]
    assert saved["result"]["work_dir"] == str(work)
    assert (work / "stderr.log").read_bytes() == b"scrutare: stopped\n"


def test_earlier_runs_and_iterative_history_never_decide_this_jobs_delivery(tmp_path):
    store, job = claimed(tmp_path)
    root = tmp_path / "repositories"
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, b"", b"")
    def prior(argv, **kwargs):
        journal(kwargs, "sending")
        (kwargs["cwd"] / ".scrutare" / "runs" / ".iterative").mkdir()
        return subprocess.CompletedProcess(argv, 1, b"", b"")
    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=prior)
    assert store.get(job["id"])["result"]["delivery"] == "uncertain"
    store.enqueue("api:again", "owner/repo", 12, 456)
    again = store.claim()
    run_job(settings(tmp_path), store, again, client=TrustedClient(), runner=runner)
    assert store.get(again["id"])["result"]["delivery"] == "not_sent"
    assert root.is_dir()


def test_failures_before_the_cli_starts_are_not_sent_and_missing_config_is_named(tmp_path):
    store, job = claimed(tmp_path)
    requests = []
    def request(method, path, token, body):
        requests.append(path)
        if path.endswith("/access_tokens"):
            return {"token": "scoped"}
        if path == "/repos/owner/repo/pulls/12":
            return {"number": 12, "head": {"sha": "a" * 40}}
        if path == "/repos/owner/repo":
            return {"default_branch": "main", "full_name": "owner/repo"}
        raise AppError("GitHub App resource was not found", 404)
    config = settings(tmp_path)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config.private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    run_job(config, store, job, client=GitHubAppClient(config, request=request),
            runner=lambda *a, **k: pytest.fail("must not start the CLI"))
    saved = store.get(job["id"])
    assert saved["status"] == "failed"
    assert saved["result"]["delivery"] == "not_sent"
    assert "scrutare.yaml" in saved["result"]["error"]


def test_webhook_job_for_a_head_already_reviewed_is_superseded_without_a_second_review(
        tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    def runner(argv, **kwargs):
        done = len(list(tmp_path.rglob("result.json")))
        root = kwargs["cwd"] / ".scrutare" / "runs" / f"run-{done}"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root), b"")
    first = store.enqueue("webhook:first", "owner/repo", 12, 456, "a" * 40)
    run_job(settings(tmp_path), store, store.claim(), client=TrustedClient(), runner=runner)
    assert store.get(first["id"])["status"] == "posted"
    # A redelivered or late event for an older push finds the same current head.
    late = store.enqueue("webhook:late", "owner/repo", 12, 456, "9" * 40)
    run_job(settings(tmp_path), store, store.claim(), client=TrustedClient(),
            runner=lambda *a, **k: pytest.fail("must not review the same head again"))
    assert store.get(late["id"])["status"] == "superseded"
    assert store.get(late["id"])["result"] == {"superseded_by": first["id"]}
    # A performer's explicit request always runs.
    assert store.get(first["id"])["reviewed"] == "a" * 40
    asked = store.enqueue("api:asked", "owner/repo", 12, 456)
    run_job(settings(tmp_path), store, store.claim(), client=TrustedClient(), runner=runner)
    assert store.get(asked["id"])["status"] == "posted"


def test_shutdown_before_the_cli_starts_returns_the_job_to_the_queue(tmp_path):
    from scrutare.interfaces.app_server import WorkerProcess
    store, job = claimed(tmp_path)
    process = WorkerProcess()
    process.stop()
    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=process)
    assert store.get(job["id"])["status"] == "queued"
    # The released job can run after restart despite its existing work directory.
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, b"", b"")
    run_job(settings(tmp_path), store, store.claim(), client=TrustedClient(), runner=runner)
    assert store.get(job["id"])["status"] == "failed"


@pytest.mark.parametrize(("provider", "base_url", "allowed"), [
    ("openai", "https://api.openai.com/v1", True),
    ("openai", "null", True),
    ("anthropic", "null", True),
    ("openai", "https://gateway.example/v1", True),
    ("anthropic", "https://gateway.example", True),
    # An approved vendor origin still never receives the other vendor's credential.
    ("openai", "https://api.anthropic.com/v1", False),
    ("anthropic", "https://api.openai.com", False),
    ("openai", "http://gateway.example/v1", False),
    ("openai", "https://api.openai.com@gateway.example/v1", False),
])
def test_model_origin_approval_is_bound_to_the_provider_credential(
        tmp_path, provider, base_url, allowed):
    store, job = claimed(tmp_path)
    class Client(TrustedClient):
        def configuration(self, *args):
            return (f"models:\n  default:\n    provider: {provider}\n"
                    f"    base_url: {base_url}\n    model: example\n").encode()
    started = []
    def runner(argv, **kwargs):
        started.append(argv)
        return subprocess.CompletedProcess(argv, 1, b"", b"")
    origins = ("https://api.openai.com", "https://api.anthropic.com", "https://gateway.example")
    run_job(replace(settings(tmp_path), model_origins=origins), store, job,
            client=Client(), runner=runner)
    assert bool(started) is allowed
    assert ("origin" in store.get(job["id"])["result"]["error"]) is not allowed


@pytest.mark.parametrize("corrupt", [
    lambda root, raw: json.dumps(json.loads(raw)).encode(),  # not the installed bytes
    lambda root, raw: cli_result(root, schema_version=2),
    lambda root, raw: cli_result(root, status="uncertain"),
    lambda root, raw: cli_result(root, head_sha="main"),
    lambda root, raw: cli_result(root, verdict="merged"),
    lambda root, raw: cli_result(root, run_dir=str(root.parent / ".." / ".." / "elsewhere")),
    lambda root, raw: b"[]",
])
def test_success_requires_the_current_installed_cli_result_contract(tmp_path, corrupt):
    store, job = claimed(tmp_path)
    def runner(argv, **kwargs):
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, corrupt(root, cli_result(root)), b"")
    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=runner)
    saved = store.get(job["id"])
    # Exit 0 means the CLI confirmed delivery, so this is never a "not sent" failure.
    assert (saved["status"], saved["result"]["delivery"]) == ("interrupted", "uncertain")
    assert saved["result"]["exit_code"] == 0
    assert "work_dir" in saved["result"]


@pytest.mark.parametrize("verdict", ["approve", "changes_requested", "escalated"])
def test_every_posted_verdict_is_a_successful_job(tmp_path, verdict):
    store, job = claimed(tmp_path)
    def runner(argv, **kwargs):
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root, verdict=verdict), b"")
    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=runner)
    saved = store.get(job["id"])
    assert saved["status"] == "posted"
    assert saved["result"]["verdict"] == verdict


def test_job_deadline_stops_the_cli_before_its_installation_token_expires(tmp_path):
    store, job = claimed(tmp_path)
    seen = {}
    def runner(argv, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"", stderr=b"late\n")
    config = replace(settings(tmp_path), job_timeout=90)
    run_job(config, store, job, client=TrustedClient(), runner=runner)
    saved = store.get(job["id"])
    assert seen["timeout"] == 90
    assert saved["status"] == "interrupted"
    assert "deadline" in saved["result"]["error"]
    assert saved["result"]["delivery"] == "not_sent"
    assert (tmp_path / "work" / job["id"] / "stderr.log").read_bytes() == b"late\n"


@pytest.mark.parametrize("seconds", [0, -1, 3301, True, 1.5])
def test_job_deadline_must_fit_inside_the_token_lifetime(tmp_path, seconds):
    with pytest.raises(AppError):
        replace(settings(tmp_path), job_timeout=seconds)
    assert settings(tmp_path).job_timeout == 3300


CURRENT_CONFIG = b"""\
strategy: debate
rounds: {max: 2}
personas: [senior-dev, security]
budgets: {per_persona_tokens: 50000, review_max_tokens: 200000}
models:
  default: {provider: openai, base_url: "https://models.example/v1", model: example}
  overrides:
    security: {provider: anthropic, base_url: null, model: other}
context: {enabled: true, related_paths: [src/caller.py]}
analysis: {enabled: true}
inspection: {procedures: v1}
findings: {evidence: v2, assessment: {enabled: true}}
github: {post_mode: comment, human_reviewers: [octocat]}
"""


@pytest.mark.parametrize(("origins", "status"), [
    (("https://models.example", "https://api.anthropic.com"), "posted"),
    (("https://models.example",), "failed"),
    (("https://api.anthropic.com",), "failed"),
])
def test_current_repository_config_reaches_the_cli_only_with_every_rail_approved(
        tmp_path, origins, status):
    store, job = claimed(tmp_path)
    class Client(TrustedClient):
        def configuration(self, *args):
            return CURRENT_CONFIG
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        root = kwargs["cwd"] / ".scrutare" / "runs" / "test"
        root.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, cli_result(root), b"")
    run_job(replace(settings(tmp_path), model_origins=origins), store, job,
            client=Client(), runner=runner)
    assert store.get(job["id"])["status"] == status
    assert len(calls) == (status == "posted")
    for argv in calls:
        # Exact trusted bytes select the strategy; no caller-controlled evidence path exists.
        assert Path(argv[argv.index("--config") + 1]).read_bytes() == CURRENT_CONFIG
        assert "--analysis-capture" not in argv


def test_stdout_evidence_write_failure_after_delivery_is_not_reported_as_unsent(tmp_path):
    store, job = claimed(tmp_path)
    def runner(argv, **kwargs):
        run = journal(kwargs, "posted")
        (tmp_path / "work" / job["id"] / "stdout.json").mkdir()  # the evidence write fails
        return subprocess.CompletedProcess(argv, 0, cli_result(run), b"")
    run_job(settings(tmp_path), store, job, client=TrustedClient(), runner=runner)
    saved = store.get(job["id"])
    assert (saved["status"], saved["result"]["delivery"]) == ("failed", "confirmed")


@pytest.mark.parametrize(("code", "posting"), [(1, "posted"), (1, "unknown"), (-9, "sending")])
def test_head_whose_review_may_exist_is_not_reviewed_again_by_a_late_webhook(
        tmp_path, code, posting):
    store = JobStore(tmp_path / "jobs.sqlite")
    class Client(TrustedClient):
        def head(self, *args):
            return "c" * 40
    store.enqueue("webhook:h2", "owner/repo", 12, 456, "c" * 40)
    def delivered_then_failed(argv, **kwargs):
        journal(kwargs, posting)
        return subprocess.CompletedProcess(argv, code, b"", b"")
    run_job(settings(tmp_path), store, store.claim(), client=Client(),
            runner=delivered_then_failed)
    # GitHub redelivers an older push that was refused while the queue was full.
    late = store.enqueue("webhook:h1", "owner/repo", 12, 456, "a" * 40)
    run_job(settings(tmp_path), store, store.claim(), client=Client(),
            runner=lambda *a, **k: pytest.fail("must not post a second review of this head"))
    assert store.get(late["id"])["status"] == "superseded"


def test_orphaned_leases_are_ended_with_delivery_read_from_their_own_runs(tmp_path):
    import os

    from scrutare.interfaces.app_worker import recover_jobs
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    def orphan(delivery, number):
        store.enqueue(delivery, "owner/repo", number, 456)
        return store.claim()
    # Preflight was interrupted: nothing ran, so nothing was sent.
    idle = orphan("api:idle", 12)
    assert recover_jobs(config, store) == 1
    saved = store.get(idle["id"])
    assert (saved["status"], saved["result"]["delivery"], saved["reviewed"]) == (
        "interrupted", "not_sent", None)
    # The worker died after the review was posted.
    busy = orphan("api:busy", 12)
    from scrutare.interfaces.app_worker import _repository_root
    cwd = _repository_root(config, busy)
    old = journal({"cwd": cwd}, "sending")
    old.rename(old.with_name("run-old"))
    os.utime(old.with_name("run-old"), (busy["claimed"] - 60, busy["claimed"] - 60))
    journal({"cwd": cwd}, "posted")
    assert recover_jobs(config, store) == 1
    saved = store.get(busy["id"])
    assert (saved["status"], saved["result"]["delivery"], saved["reviewed"]) == (
        "interrupted", "confirmed", "c" * 40)
    assert recover_jobs(config, store) == 0


def test_unexpected_worker_failure_leaves_a_lease_the_next_pass_recovers(tmp_path):
    from scrutare.interfaces.app_worker import work_once
    config = settings(tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("api:one", "owner/repo", 12, 456)
    class Broken(TrustedClient):
        def installation_token(self, *args):
            raise RuntimeError("not a failure run_job anticipates")
    with pytest.raises(RuntimeError):
        work_once(config, store, client=Broken())
    assert store.get(job["id"])["status"] == "running"
    assert work_once(config, store, client=Broken()) is False
    saved = store.get(job["id"])
    assert (saved["status"], saved["result"]["delivery"]) == ("interrupted", "not_sent")


DELIVERY = {"id": 1, "guid": "g-1", "delivered_at": "2026-10-13T10:00:00Z", "status_code": 502,
            "event": "pull_request", "action": "opened", "installation_id": 456}
NOW = 1791885600.0 + 600  # ten minutes after DELIVERY


def deliveries(tmp_path, items):
    calls = []
    def request(method, path, token, body):
        calls.append((method, path))
        return {"items": items} if method == "GET" else {}
    config = settings(tmp_path)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config.private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return GitHubAppClient(config, request=request), calls


def test_only_recent_undelivered_reviewable_events_are_redelivery_candidates(tmp_path):
    items = [
        DELIVERY,
        DELIVERY | {"id": 2, "guid": "g-timeout", "status_code": 0},
        DELIVERY | {"id": 3, "guid": "g-refused", "status_code": 403},
        DELIVERY | {"id": 4, "guid": "g-ok", "status_code": 202},
        DELIVERY | {"id": 5, "guid": "g-recovered", "status_code": 503},
        DELIVERY | {"id": 6, "guid": "g-recovered", "status_code": 202},
        DELIVERY | {"id": 7, "guid": "g-closed", "action": "closed"},
        DELIVERY | {"id": 8, "guid": "g-ping", "event": "ping", "action": None},
        DELIVERY | {"id": 9, "guid": "g-foreign", "installation_id": 999},
        DELIVERY | {"id": 10, "guid": "g-old", "delivered_at": "2026-10-11T10:00:00Z"},
        DELIVERY | {"id": 11, "guid": "g-1", "status_code": 500},
    ]
    client, calls = deliveries(tmp_path, items)
    assert client.failed_deliveries(NOW) == [(2, "g-timeout"), (11, "g-1")]
    assert calls == [("GET", "/app/hook/deliveries?per_page=100")]
    # A record this code does not understand is skipped, not fatal to the others.
    odd, _ = deliveries(tmp_path, [DELIVERY | {"guid": 7}, "text", {"id": 3},
                                   DELIVERY | {"delivered_at": None},
                                   DELIVERY | {"id": 12, "guid": "g-null", "status_code": None},
                                   DELIVERY | {"id": 13, "guid": "g-ok", "status": "OK",
                                               "status_code": None}])
    assert odd.failed_deliveries(NOW) == [(12, "g-null")]
    def request(method, path, token, body):
        return {"message": "not a list"}
    with pytest.raises(AppError, match="delivery records"):
        GitHubAppClient(odd.settings, request=request).failed_deliveries(NOW)


def test_reconcile_counts_only_accepted_requests_and_survives_one_refusal(tmp_path):
    from scrutare.interfaces.app_worker import reconcile
    store = JobStore(tmp_path / "jobs.sqlite")
    asked = []
    class Client:
        def failed_deliveries(self):
            return [(2, "g-refused"), (11, "g-1")]
        def redeliver(self, delivery):
            asked.append(delivery)
            if delivery == 2:
                raise AppError("GitHub App request failed; check installation permissions")
    # The refusal is reported after the remaining deliveries were still requested.
    for _ in range(3):
        with pytest.raises(AppError, match="1 webhook redelivery requests failed"):
            reconcile(store, Client())
    assert asked == [2, 11] * 3
    # g-1 has used its three accepted requests; the refused one never spent its budget.
    with pytest.raises(AppError):
        reconcile(store, Client())
    assert asked[-1] == 2 and len(asked) == 7
    class Healthy(Client):
        def failed_deliveries(self):
            return [(21, "g-2")]
    assert reconcile(store, Healthy()) == 1


def test_redelivery_and_head_use_exact_app_endpoints(tmp_path):
    client, calls = deliveries(tmp_path, [])
    client.redeliver(11)
    assert calls == [("POST", "/app/hook/deliveries/11/attempts")]
    def request(method, path, token, body):
        assert (method, path, token) == ("GET", "/repos/owner/repo/pulls/12", "scoped")
        return {"number": 12, "head": {"sha": "b" * 40}}
    assert GitHubAppClient(settings(tmp_path), request=request).head(
        "owner/repo", 12, "scoped") == "b" * 40
    with pytest.raises(AppError):
        GitHubAppClient(settings(tmp_path), request=lambda *a: {"number": 13}).head(
            "owner/repo", 12, "scoped")


def test_github_transport_maps_missing_resources_lists_and_refuses_redirects(tmp_path):
    import io
    from urllib.error import HTTPError
    from urllib.request import Request

    from scrutare.interfaces.app_worker import NoRedirect, github_request
    class Response(io.BytesIO):
        url = "https://api.github.com/x"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def missing(*args, **kwargs):
        raise HTTPError("https://api.github.com", 404, "secret-token", {}, None)
    with pytest.raises(AppError) as error:
        github_request("GET", "/x", "secret-token", None, opener=missing)
    assert error.value.status == 404 and "secret-token" not in str(error.value)
    assert github_request("GET", "/x", "t", None, opener=lambda *a, **k: Response(b"[1]")) == {
        "items": [1]}
    assert github_request("POST", "/x", "t", None, opener=lambda *a, **k: Response(b"")) == {}
    elsewhere = Response(b"{}")
    elsewhere.url = "https://example.com/x"
    with pytest.raises(AppError, match="redirected"):
        github_request("GET", "/x", "t", None, opener=lambda *a, **k: elsewhere)
    with pytest.raises(AppError, match="redirects are refused"):
        NoRedirect().redirect_request(Request("https://api.github.com/x"), None, 302, "Found",
                                      {}, "https://example.com/x")
