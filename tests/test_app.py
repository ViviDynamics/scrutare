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
    # The newer push replaces the queued one; both survive a restart.
    reopened = JobStore(tmp_path / "jobs.sqlite", max_pending=2)
    assert reopened.get(pushed["id"]) == pushed
    assert reopened.get(first["id"])["result"] == {"superseded_by": pushed["id"]}


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
    running, = store.running()
    store.finish(running["id"], "interrupted", {"error": "restarted", "delivery": "uncertain"})
    assert store.get(job["id"])["status"] == "interrupted"
    assert store.claim() is None


def test_admission_keeps_one_queued_push_per_pull_request(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite", max_pending=3)
    opened = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    performer = store.enqueue("api:task", "owner/repo", 12, 456)
    other = store.enqueue("webhook:other", "owner/repo", 13, 456, "c" * 40)
    # At the cap, a newer push replaces its pull request's queued one instead of
    # being refused, so one author cannot fill the queue for everyone else.
    pushed = store.enqueue("webhook:pushed", "owner/repo", 12, 456, "b" * 40)
    newest = store.enqueue("webhook:newest", "owner/repo", 12, 456, "d" * 40)
    assert [store.get(job["id"])["status"] for job in (opened, pushed, newest)] == [
        "superseded", "superseded", "queued"]
    assert store.get(pushed["id"])["result"] == {"superseded_by": newest["id"]}
    with pytest.raises(AppError) as full:
        store.enqueue("webhook:third", "owner/repo", 14, 456, "e" * 40)
    assert full.value.status == 503
    # A refused admission supersedes nothing.
    assert store.get(newest["id"])["status"] == "queued"
    # Explicit performer requests are never merged; a running review is not replaced.
    for expected in (performer, other):
        assert store.claim()["id"] == expected["id"]
        store.finish(expected["id"], "failed", {"error": "x", "delivery": "not_sent"})
    assert store.claim()["id"] == newest["id"]
    later = store.enqueue("webhook:later", "owner/repo", 12, 456, "f" * 40)
    assert store.get(newest["id"])["status"] == "running"
    store.finish(newest["id"], "failed", {"error": "transient", "delivery": "not_sent"})
    assert store.claim()["id"] == later["id"]


def test_superseded_head_is_admitted_again_when_it_becomes_the_head_once_more(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    first = store.enqueue("webhook:h1", "owner/repo", 12, 456, "a" * 40)
    second = store.enqueue("webhook:h2", "owner/repo", 12, 456, "b" * 40)
    assert store.get(first["id"])["status"] == "superseded"
    store.claim()
    store.finish(second["id"], "posted", {"head_sha": "b" * 40}, reviewed="b" * 40)
    # A force-push back to the never-reviewed head runs its original job.
    again = store.enqueue("webhook:h1", "owner/repo", 12, 456, "a" * 40)
    assert (again["id"], again["status"], again["result"]) == (first["id"], "queued", None)
    assert store.claim()["id"] == first["id"]
    # A reviewed or failed head stays admitted once: no automatic second review.
    assert store.enqueue("webhook:h2", "owner/repo", 12, 456, "b" * 40)["status"] == "posted"
    store.finish(first["id"], "failed", {"error": "x", "delivery": "not_sent"})
    assert store.enqueue("webhook:h1", "owner/repo", 12, 456, "a" * 40)["status"] == "failed"


def test_released_job_returns_to_the_queue_and_only_its_owner_can_release(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    assert store.claim()["id"] == job["id"]
    store.release(job["id"])
    assert store.get(job["id"])["status"] == "queued"
    with pytest.raises(AppError):
        store.release(job["id"])
    assert store.running() == []
    claimed = store.claim()
    assert claimed["id"] == job["id"] and claimed["claimed"] >= job["created"]
    assert [row["id"] for row in store.running()] == [job["id"]]


@pytest.mark.parametrize(("status", "result", "reviewed"), [
    ("posted", {"head_sha": "b" * 40}, True),
    # Delivered or possibly delivered reviews count, whatever the job's status.
    ("failed", {"error": "x", "delivery": "confirmed"}, True),
    ("interrupted", {"error": "x", "delivery": "uncertain"}, True),
])
def test_reviewed_names_every_job_that_may_have_delivered_a_review_of_that_head(
        tmp_path, status, result, reviewed):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    store.claim()
    assert store.reviewed("owner/repo", 12, "a" * 40) is None
    # The CLI reviews the head current at capture, which can be newer than announced.
    store.finish(job["id"], status, result, reviewed="b" * 40)
    assert store.reviewed("owner/repo", 12, "b" * 40) == job["id"]
    assert store.reviewed("owner/repo", 12, "a" * 40) is None
    assert store.reviewed("owner/repo", 13, "b" * 40) is None
    assert store.get(job["id"])["head"] == "a" * 40


def test_job_that_sent_nothing_does_not_mark_its_head_reviewed(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue("webhook:opened", "owner/repo", 12, 456, "a" * 40)
    store.claim()
    store.finish(job["id"], "failed", {"error": "x", "delivery": "not_sent"})
    assert store.reviewed("owner/repo", 12, "a" * 40) is None


def test_redelivery_budget_counts_only_recorded_requests_and_is_pruned(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert store.redelivery_due("guid-1") and store.redelivery_due("guid-1")
    for _ in range(3):
        assert store.redelivery_due("guid-1")
        store.redelivery_requested("guid-1", now=1000.0)
    assert not JobStore(tmp_path / "jobs.sqlite").redelivery_due("guid-1")
    assert store.redelivery_due("guid-2")
    # Deliveries older than the reconciliation window no longer need a budget row.
    store.redelivery_requested("guid-2", now=1000.0 + 3 * 86400)
    assert store.redelivery_due("guid-1")


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


def reply_event(action="created", *, comment=51, body="This is intentional.", thread=50,
                login="lead", kind="User", number=12, head="a" * 40, state="open"):
    comment_document = {"id": comment, "body": body, "user": {"login": login, "type": kind}}
    if thread is not None:
        comment_document["in_reply_to_id"] = thread
    return {"action": action, "installation": {"id": 456},
            "repository": {"full_name": "owner/repo"}, "comment": comment_document,
            "sender": {"login": login, "type": kind},
            "pull_request": {"number": number, "state": state, "head": {"sha": head},
                             "base": {"repo": {"full_name": "owner/repo"}}}}


def admit_reply(tmp_path, store, document, delivery="reply"):
    body, signature = signed(document)
    return admit_webhook(settings(tmp_path), store, "pull_request_review_comment", delivery,
                         signature, body)


def finish(store, status="posted"):
    job = store.claim()
    store.finish(job["id"], status, {"head_sha": job["head"]}, reviewed=job["head"])
    return job


def test_signed_reply_is_admitted_once_however_often_it_is_delivered(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite", max_pending=5)
    first = admit_reply(tmp_path, store, reply_event())
    assert first["status"] == "queued" and first["head"] == "a" * 40
    assert first["delivery"].startswith("webhook:reply:")
    assert (first["repository"], first["number"], first["installation"]) == (
        "owner/repo", 12, 456)
    # A redelivery, and a replay under a fresh unsigned delivery ID, are the same job.
    assert admit_reply(tmp_path, store, reply_event()) == first
    assert admit_reply(tmp_path, store, reply_event(), "replayed") == first
    finish(store)
    done = store.get(first["id"])
    assert admit_reply(tmp_path, store, reply_event()) == done
    # An edit that leaves the text unchanged is not new evidence.
    assert admit_reply(tmp_path, store, reply_event("edited")) == done
    edited = admit_reply(tmp_path, store, reply_event("edited", body="Edited reasoning."))
    assert edited["id"] != first["id"] and edited["status"] == "queued"
    assert admit_reply(tmp_path, store, reply_event("edited", body="Edited reasoning.")) == edited
    assert store.claim()["id"] == edited["id"] and store.claim() is None


@pytest.mark.parametrize("document", [
    reply_event(thread=None),                      # a new thread, not a reply
    reply_event("deleted"),
    reply_event(kind="Bot", login="scrutare[bot]"),  # Scrutare's own response
    reply_event(kind="Bot", login="renovate"),
    reply_event(login="helper[bot]"),
    reply_event(kind="Organization"),
    reply_event(state="closed"),
])
def test_replies_that_can_never_contest_are_ignored_after_authentication(tmp_path, document):
    store = JobStore(tmp_path / "jobs.sqlite")
    assert admit_reply(tmp_path, store, document) == {"status": "ignored"}
    assert store.claim() is None


def test_reply_edited_by_a_bot_is_ignored(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    document = reply_event("edited")
    document["sender"] = {"login": "scrutare[bot]", "type": "Bot"}
    assert admit_reply(tmp_path, store, document) == {"status": "ignored"}


@pytest.mark.parametrize("mutate", [
    lambda p: p["installation"].update(id=789),
    lambda p: p["repository"].update(full_name="other/repo"),
    lambda p: p["pull_request"].update(number=True),
    lambda p: p["pull_request"]["base"]["repo"].update(full_name="other/repo"),
    lambda p: p["pull_request"]["head"].update(sha="main"),
    lambda p: p["comment"].update(id="51"),
    lambda p: p["comment"].update(id=0),
    lambda p: p["comment"].update(in_reply_to_id="50"),
    lambda p: p["comment"].update(in_reply_to_id=True),
    lambda p: p["comment"].update(body=None),
    lambda p: p["comment"].pop("user"),
    lambda p: p.pop("comment"),
    lambda p: p.pop("sender"),
])
def test_signed_but_unauthorized_or_malformed_replies_are_refused(tmp_path, mutate):
    document = reply_event()
    mutate(document)
    store = JobStore(tmp_path / "jobs.sqlite")
    with pytest.raises(AppError):
        admit_reply(tmp_path, store, document)
    assert store.claim() is None


def test_unsigned_reply_is_never_parsed(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    body, _ = signed(reply_event())
    with pytest.raises(AppError, match="signature"):
        admit_webhook(settings(tmp_path), store, "pull_request_review_comment", "d",
                      "sha256=" + "0" * 64, body)
    assert store.claim() is None


def test_replies_coalesce_with_the_queued_job_of_their_pull_request(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite", max_pending=3)
    body, signature = signed(payload("synchronize", "b" * 40))
    pushed = admit_webhook(settings(tmp_path), store, "pull_request", "push", signature, body)
    # The queued push will capture this reply, so it needs no job of its own.
    assert admit_reply(tmp_path, store, reply_event(head="b" * 40)) == pushed
    assert store.claim()["id"] == pushed["id"]
    # A review already running may have captured before the reply: queue exactly one.
    first = admit_reply(tmp_path, store, reply_event(head="b" * 40))
    assert first["id"] != pushed["id"] and first["status"] == "queued"
    second = admit_reply(tmp_path, store, reply_event(comment=52, body="And another thing."))
    third = admit_reply(tmp_path, store, reply_event(comment=53, body="One more."))
    assert second == first and third == first
    # Another pull request has its own slot.
    other = admit_reply(tmp_path, store, reply_event(number=13))
    assert other["id"] != first["id"]
    # A new push covers the queued reply job.
    body, signature = signed(payload("synchronize", "c" * 40))
    newer = admit_webhook(settings(tmp_path), store, "pull_request", "push2", signature, body)
    assert store.get(first["id"])["status"] == "superseded"
    assert store.get(first["id"])["result"] == {"superseded_by": newer["id"]}
    assert store.get(other["id"])["status"] == "queued"


def test_replies_respect_the_admission_cap(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite", max_pending=1)
    admit_reply(tmp_path, store, reply_event())
    with pytest.raises(AppError) as full:
        admit_reply(tmp_path, store, reply_event(number=13))
    assert full.value.status == 503


def test_unchanged_is_a_terminal_status(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    job = admit_reply(tmp_path, store, reply_event())
    store.claim()
    store.finish(job["id"], "unchanged", {"reason": "nothing to reassess"})
    saved = store.get(job["id"])
    assert saved["status"] == "unchanged" and saved["reviewed"] is None
    assert store.claim() is None
