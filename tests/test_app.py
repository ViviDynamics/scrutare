"""The App authenticates before admitting bounded, durable review work."""

import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from scrutare.interfaces.app import AppError, AppSettings, JobStore, admit_webhook


def settings(tmp_path):
    return AppSettings(123, tmp_path / "key.pem", "webhook-secret", "api-secret",
                       {"owner/repo": 456}, tmp_path, ("https://models.example",), 2)


def payload(action="opened"):
    return {"action": action, "installation": {"id": 456},
            "repository": {"full_name": "owner/repo"}, "number": 12,
            "pull_request": {"number": 12, "state": "open", "head": {"sha": "a" * 40},
                             "base": {"repo": {"full_name": "owner/repo"}}}}


def signed(document):
    body = json.dumps(document).encode()
    signature = "sha256=" + hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
    return body, signature


def test_signed_open_and_push_are_durable_idempotent_jobs(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite", max_pending=2)
    body, signature = signed(payload())
    first = admit_webhook(settings(tmp_path), store, "pull_request", "delivery", signature, body)
    assert first["status"] == "queued"
    assert admit_webhook(settings(tmp_path), store, "pull_request", "delivery", signature,
                         body) == first
    body, signature = signed(payload("synchronize"))
    assert admit_webhook(settings(tmp_path), store, "pull_request", "push", signature,
                         body)["id"] != first["id"]
    reopened = JobStore(tmp_path / "jobs.sqlite", max_pending=2)
    assert reopened.get(first["id"]) == first


@pytest.mark.parametrize("signature", ["", "sha1=bad", "sha256=" + "0" * 64])
def test_invalid_signatures_never_admit(tmp_path, signature):
    store = JobStore(tmp_path / "jobs.sqlite")
    with pytest.raises(AppError, match="signature"):
        admit_webhook(settings(tmp_path), store, "pull_request", "delivery", signature, b"{}")
    assert store.claim() is None


@pytest.mark.parametrize("mutate", [
    lambda p: p["installation"].update(id=789),
    lambda p: p["repository"].update(full_name="other/repo"),
    lambda p: p.update(number=True),
    lambda p: p["pull_request"]["base"]["repo"].update(full_name="other/repo"),
])
def test_signed_but_unauthorized_or_malformed_events_are_refused(tmp_path, mutate):
    document = payload()
    mutate(document)
    body, signature = signed(document)
    store = JobStore(tmp_path / "jobs.sqlite")
    with pytest.raises(AppError):
        admit_webhook(settings(tmp_path), store, "pull_request", "delivery", signature, body)
    assert store.claim() is None


def test_irrelevant_events_are_ignored_after_authentication(tmp_path):
    body, signature = signed(payload("closed"))
    store = JobStore(tmp_path / "jobs.sqlite")
    assert admit_webhook(settings(tmp_path), store, "pull_request", "delivery", signature,
                         body) == {"status": "ignored"}
    assert store.claim() is None


def test_queue_is_bounded_and_atomic_across_concurrent_connections(tmp_path):
    path = tmp_path / "jobs.sqlite"
    store = JobStore(path, max_pending=2)
    def submit(n):
        try:
            return JobStore(path, max_pending=2).enqueue(str(n), "owner/repo", 12, 456)
        except AppError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(result is not None for result in results) == 2
    job = store.claim()
    assert job["status"] == "running"
    store.finish(job["id"], "posted", {"verdict": "approve"})
    assert store.get(job["id"])["result"] == {"verdict": "approve"}


def test_restart_retains_uncertain_delivery_and_never_retries(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("delivery", "owner/repo", 12, 456)
    assert store.claim()["id"] == job["id"]
    store.recover_interrupted()
    assert store.get(job["id"])["status"] == "interrupted"
    assert store.claim() is None
