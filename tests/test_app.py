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


def payload(action="opened", head="a" * 40):
    return {"action": action, "installation": {"id": 456},
            "repository": {"full_name": "owner/repo"}, "number": 12,
            "pull_request": {"number": 12, "state": "open", "head": {"sha": head},
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
    # The delivery header is unsigned: a replay under a fresh ID is still the same job,
    # as is a later event that announces the head already admitted.
    assert admit_webhook(settings(tmp_path), store, "pull_request", "replayed", signature,
                         body) == first
    body, signature = signed(payload("reopened"))
    assert admit_webhook(settings(tmp_path), store, "pull_request", "reopen", signature,
                         body) == first
    body, signature = signed(payload("synchronize", "b" * 40))
    pushed = admit_webhook(settings(tmp_path), store, "pull_request", "push", signature, body)
    assert pushed["id"] != first["id"] and pushed["head"] == "b" * 40
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
    lambda p: p["pull_request"]["head"].update(sha="main"),
    lambda p: p["pull_request"].pop("head"),
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


def test_claim_runs_the_newest_queued_push_and_supersedes_only_older_ones(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    opened = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    performer = store.enqueue("api:task", "owner/repo", 12, 456)
    other = store.enqueue("webhook:other", "owner/repo", 13, 456, "c" * 40)
    pushed = store.enqueue("webhook:pushed", "owner/repo", 12, 456, "b" * 40)
    # The pull request's turn comes from its oldest job, but the newest push runs.
    running = store.claim()
    assert running["id"] == pushed["id"]
    covered = store.get(opened["id"])
    assert covered["status"] == "superseded"
    assert covered["result"] == {"superseded_by": pushed["id"]}
    # A failure of the running review loses no push newer than the one it reviews.
    store.finish(running["id"], "failed", {"error": "transient", "delivery": "not_sent"})
    assert [job["status"] for job in (store.get(opened["id"]), store.get(pushed["id"]))] == [
        "superseded", "failed"]
    # Explicit performer requests are never merged, and other pull requests keep order.
    assert store.claim()["id"] == performer["id"]
    assert store.claim()["id"] == other["id"]
    assert store.claim() is None
    later = store.enqueue("webhook:later", "owner/repo", 12, 456, "d" * 40)
    assert store.claim()["id"] == later["id"]


def test_released_job_returns_to_the_queue_and_only_its_owner_can_release(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    assert store.claim()["id"] == job["id"]
    store.release(job["id"])
    assert store.get(job["id"])["status"] == "queued"
    with pytest.raises(AppError):
        store.release(job["id"])
    store.recover_interrupted()
    assert store.claim()["id"] == job["id"]


def test_reviewed_names_only_a_posted_review_of_exactly_that_head(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    store.claim()
    assert store.reviewed("owner/repo", 12, "a" * 40) is None
    # The CLI reviews the head current at capture, which can be newer than announced.
    store.finish(job["id"], "posted", {"head_sha": "b" * 40})
    assert store.reviewed("owner/repo", 12, "b" * 40) == job["id"]
    assert store.reviewed("owner/repo", 12, "a" * 40) is None
    assert store.reviewed("owner/repo", 13, "b" * 40) is None
    failed = store.enqueue("webhook:pushed", "owner/repo", 12, 456, "c" * 40)
    store.claim()
    store.finish(failed["id"], "failed", {"error": "x"})
    assert store.reviewed("owner/repo", 12, "c" * 40) is None


def test_redelivery_requests_are_bounded_per_delivery(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert [store.redelivery_due("guid-1") for _ in range(4)] == [True, True, True, False]
    assert JobStore(tmp_path / "jobs.sqlite").redelivery_due("guid-1") is False
    assert store.redelivery_due("guid-2") is True


@pytest.mark.parametrize("origins", [
    (), ["https://models.example"], "https://models.example", ("http://models.example",),
    ("https://models.example/v1",), ("https://user@models.example",), ("https://",),
    ("https://Models.Example",), (7,)])
def test_model_origins_must_be_a_nonempty_tuple_of_https_origins(tmp_path, origins):
    with pytest.raises(AppError):
        AppSettings(123, tmp_path / "key.pem", "webhook-secret", "api-secret",
                    {"owner/repo": 456}, tmp_path, origins)
    AppSettings(123, tmp_path / "key.pem", "webhook-secret", "api-secret",
                {"owner/repo": 456}, tmp_path, ("https://models.example:8443",))
