"""Escalation exercises captured artifacts, durable writes and restart decisions."""

import json
from collections import deque
from dataclasses import FrozenInstanceError

import pytest
from test_post_review import REF, SHA, CaptureClient, FakePoster, metadata

from scrutare.config import parse_config
from scrutare.engine.github import GitHubError
from scrutare.engine.ingestion import ingest_pr
from scrutare.findings import Exhaustion, derive_verdict
from scrutare.poster import (
    PostingError,
    PostingRateLimited,
    PostingRejected,
    PostingUncertain,
    ReviewerRequestReceipt,
    post_review,
)
from scrutare.poster.journal import run_lock


@pytest.fixture
def captured(tmp_path):
    raw = (b"models: {default: {model: test-model}}\n"
           b"github: {human_reviewers: [Alice, alice, service-bot]}\n")
    config = parse_config(raw)
    run = ingest_pr(CaptureClient(), REF, tmp_path, config_bytes=raw,
                    config_data=config.to_dict())
    verdict = derive_verdict([], config.verdict, exhaustion=Exhaustion("panel", 3, 3))
    return run, verdict


def post(*args, **kwargs):
    from scrutare.poster import post_escalation
    return post_escalation(*args, **kwargs)


def state(run, name="escalation.json"):
    return json.loads((run / name).read_bytes())


def save(run, value, name="escalation.json"):
    (run / name).write_text(json.dumps(value))


class Escalator(FakePoster):
    def __init__(self, run, outcomes=(), **kwargs):
        super().__init__(**kwargs)
        self.run = run
        self.request_outcomes = deque(outcomes)
        self.requests = []
        self.requested = ()

    def create_review(self, ref, payload):
        assert state(self.run, "posting.json")["status"] == "sending"
        return super().create_review(ref, payload)

    def request_reviewers(self, ref, reviewers):
        assert ref == REF
        assert state(self.run)["status"] == "sending"
        assert state(self.run, "posting.json")["status"] == "posted"
        assert state(self.run, "reviewer-request.json") == {"reviewers": list(reviewers)}
        self.requests.append(reviewers)
        outcome = self.request_outcomes.popleft() if self.request_outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        self.requested = reviewers
        return outcome or ReviewerRequestReceipt(reviewers, "post_response")

    def get_requested_reviewers(self, ref):
        assert ref == REF
        self.reads.append("requested")
        if isinstance(self.requested, BaseException):
            raise self.requested
        return self.requested


def test_complete_two_stage_once_and_fresh_serializable_output(captured):
    run, verdict = captured
    client = Escalator(run)
    receipt = post(run, verdict, client=client)
    assert receipt.verdict == "escalated"
    assert receipt.review.state == "COMMENTED"
    assert receipt.reviewer_request == ReviewerRequestReceipt(
        ("Alice", "service-bot"), "post_response")
    assert client.creates[0].event == "COMMENT"
    assert "@Alice" in client.creates[0].body and "@service-bot" in client.creates[0].body
    assert client.requests == [("Alice", "service-bot")]
    assert client.reads == ["pr", "pr"]
    output = receipt.to_dict()
    assert output["verdict"] == "escalated"
    assert output["review"]["review_id"] == 42
    assert output["reviewer_request"] == {
        "reviewers": ["Alice", "service-bot"], "provenance": "post_response"}
    json.dumps(output)
    output["reviewer_request"]["reviewers"].clear()
    output["review"]["body"] = "changed"
    assert receipt.to_dict()["reviewer_request"]["reviewers"] == ["Alice", "service-bot"]
    with pytest.raises(FrozenInstanceError):
        receipt.review = None
    assert post(run, verdict, client=client) == receipt
    assert len(client.creates) == len(client.requests) == 1
    assert client.reads == ["pr", "pr"]


def replace_config(run, text):
    raw = b"models: {default: {model: test-model}}\n" + text.encode()
    (run / "config.yaml").write_bytes(raw)
    save(run, parse_config(raw).to_dict(), "config.json")


def test_no_targets_still_comments_and_skips_request(captured):
    run, verdict = captured
    replace_config(run, "github: {human_reviewers: []}\n")
    client = Escalator(run)
    receipt = post(run, verdict, client=client)
    assert receipt.reviewer_request == ReviewerRequestReceipt((), "no_targets")
    assert state(run)["status"] == "skipped" and state(run)["attempts"] == 0
    assert len(client.creates) == 1 and client.requests == []
    assert "no escalation targets" in receipt.review.body
    assert post(run, verdict, client=client) == receipt
    assert client.reads == ["pr"]


@pytest.mark.parametrize("entry,kind", [
    (post, "normal"), (post, "strategy"), (post, "bound"), (post, "target"),
    (post_review, "strategy"), (post_review, "bound"), (post_review, "target"),
])
def test_invalid_escalation_inputs_fail_before_network(captured, kind, entry):
    run, verdict = captured
    if kind == "normal":
        verdict = derive_verdict([], verdict.config)
    elif kind == "strategy":
        replace_config(run, "strategy: debate\n")
    elif kind == "bound":
        replace_config(run, "rounds: {max: 4}\n")
    else:
        replace_config(run, "github: {human_reviewers: ['@secret']}\n")
    client = Escalator(run)
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.creates == client.requests == client.reads == []


def test_low_level_comment_can_be_completed_by_escalation(captured):
    run, verdict = captured
    client = Escalator(run)
    review = post_review(run, verdict, client=client)
    assert client.requests == []
    assert post(run, verdict, client=client).review == review
    assert len(client.creates) == len(client.requests) == 1


@pytest.mark.parametrize("requested", [(), ("Alice",), ("service-bot",),
                                        ["Alice", "service-bot"], ("@bad",),
                                        PostingError("read failed")])
def test_uncertain_absence_partial_malformed_or_failed_read_never_resends(captured, requested):
    run, verdict = captured
    client = Escalator(run, [PostingUncertain("timeout")])
    client.requested = requested
    for _ in range(2):
        with pytest.raises(PostingUncertain):
            post(run, verdict, client=client)
        assert state(run)["status"] == "unknown"
    assert len(client.creates) == len(client.requests) == 1


def test_positive_membership_recovers_without_claiming_ownership(captured):
    run, verdict = captured
    client = Escalator(run, [KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        post(run, verdict, client=client)
    assert state(run)["status"] == "sending"
    client.requested = ("ALICE", "Service-Bot", "Other")
    receipt = post(run, verdict, client=client)
    assert receipt.reviewer_request.provenance == "observed_requested"
    assert receipt.reviewer_request.reviewers == ("Alice", "service-bot")
    assert len(client.requests) == len(client.creates) == 1


@pytest.mark.parametrize("stage", ["review", "request"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_receipt_persistence_failure_recovers_without_duplicate(
    captured, monkeypatch, stage, after_replace,
):
    import scrutare.poster.escalation as escalation
    import scrutare.poster.posting as posting
    run, verdict = captured
    client = Escalator(run)
    module = posting if stage == "review" else escalation
    filename = "posting.json" if stage == "review" else "escalation.json"
    original = module.atomic_write

    def fail(path, data):
        if path.name == filename and json.loads(data).get("status") == "posted":
            if after_replace:
                original(path, data)
            raise PostingError("disk failure")
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail)
        with pytest.raises(PostingError):
            post(run, verdict, client=client)
    receipt = post(run, verdict, client=client)
    assert len(client.creates) == len(client.requests) == 1
    expected = "observed_requested" if stage == "request" and not after_replace else "post_response"
    assert receipt.reviewer_request.provenance == expected


def test_crash_during_review_delivery_recovers_then_requests(captured):
    run, verdict = captured
    client = Escalator(run)
    client.outcomes.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        post(run, verdict, client=client)
    assert state(run, "posting.json")["status"] == "sending"
    assert client.requests == []
    assert post(run, verdict, client=client).verdict == "escalated"
    assert len(client.creates) == len(client.requests) == 1


def test_request_deadline_survives_resume_and_budget_stays_three(captured, monkeypatch):
    import scrutare.poster.posting as posting
    run, verdict = captured
    now = [100.0]
    monkeypatch.setattr(posting, "_now", lambda: now[0])
    client = Escalator(run, [PostingRateLimited(status=429, retry_after=90),
                             PostingRateLimited(status=403, retry_after=10),
                             PostingRateLimited(status=429, retry_after=10)])
    with pytest.raises(PostingRateLimited) as error:
        post(run, verdict, client=client)
    assert error.value.retry_after == 90
    assert state(run)["retry_at"] == 190
    with pytest.raises(PostingRateLimited):
        post(run, verdict, client=client)
    assert len(client.requests) == 1
    now[0] = 150
    waits = []

    def advance(delay):
        waits.append(delay)
        now[0] += delay

    with pytest.raises(PostingRejected):
        post(run, verdict, client=client, sleeper=advance)
    assert waits == [40, 10]
    with pytest.raises(PostingRejected):
        post(run, verdict, client=client, sleeper=advance)
    assert len(client.requests) == 3 and len(client.creates) == 1


@pytest.mark.parametrize("unsent", [False, True])
def test_rejection_only_proven_unsent_can_resume(captured, unsent):
    run, verdict = captured
    client = Escalator(run, [PostingRejected("rejected", unsent=unsent,
                                            status=None if unsent else 422)])
    with pytest.raises(PostingRejected):
        post(run, verdict, client=client)
    if unsent:
        assert post(run, verdict, client=client).verdict == "escalated"
    else:
        with pytest.raises(PostingRejected):
            post(run, verdict, client=client)
    assert len(client.requests) == (2 if unsent else 1)
    assert len(client.creates) == 1


@pytest.mark.parametrize("between", [False, True])
@pytest.mark.parametrize("change", [{"state": "closed"}, {"merged": True}])
def test_closed_or_merged_stops_remaining_writes(captured, between, change):
    run, verdict = captured
    client = Escalator(run)
    if between:
        original = client.create_review

        def close(ref, payload):
            receipt = original(ref, payload)
            client.current = metadata(**change)
            return receipt

        client.create_review = close
    else:
        client.current = metadata(**change)
    with pytest.raises(GitHubError):
        post(run, verdict, client=client)
    assert len(client.creates) == int(between) and client.requests == []
    if between:
        assert state(run, "posting.json")["status"] == "posted"
        client.current = metadata()
        assert post(run, verdict, client=client).verdict == "escalated"
        assert len(client.creates) == 1


def test_force_push_keeps_captured_sha(captured):
    run, verdict = captured
    client = Escalator(run, current=metadata(head={"sha": "c" * 40}))
    assert post(run, verdict, client=client).review.commit_id == SHA
    assert state(run)["head_sha"] == SHA


@pytest.mark.parametrize("artifact", ["escalation.json", "reviewer-request.json"])
@pytest.mark.parametrize("entry", [post, post_review])
def test_orphan_future_artifact_blocks_first_network(captured, artifact, entry):
    run, verdict = captured
    save(run, {}, artifact)
    client = Escalator(run)
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.creates == client.requests == client.reads == []


@pytest.mark.parametrize("artifact,field,value", [
    ("escalation.json", "run_id", "b" * 32),
    ("escalation.json", "review_id", 43),
    ("escalation.json", "review_id", True),
    ("escalation.json", "reviewers", ["Other"]),
    ("escalation.json", "attempts", True),
    ("escalation.json", "attempts", 0),
    ("escalation.json", "status", "skipped"),
    ("escalation.json", "failure", "unsent"),
    ("escalation.json", "extra", "bad"),
    ("escalation.json", "receipt", {"reviewers": ["Alice", "service-bot"],
                                       "provenance": "no_targets"}),
    ("reviewer-request.json", "reviewers", ["Other"]),
    ("posting.json", "receipt", "tampered"),
])
@pytest.mark.parametrize("entry", [post, post_review])
def test_cross_stage_tampering_blocks_both_entrypoints(captured, artifact, field, value, entry):
    run, verdict = captured
    client = Escalator(run)
    post(run, verdict, client=client)
    data = state(run, artifact)
    data[field] = value
    save(run, data, artifact)
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.reads == [] and len(client.creates) == len(client.requests) == 1


@pytest.mark.parametrize("entry", [post, post_review])
def test_changed_valid_review_receipt_is_not_trusted(captured, entry):
    run, verdict = captured
    client = Escalator(run)
    post(run, verdict, client=client)
    data = state(run, "posting.json")
    data["receipt"]["login"] = "another-bot"
    save(run, data, "posting.json")
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.reads == []


@pytest.mark.parametrize("entry", [post, post_review])
def test_single_outer_lock_remains_held_during_request(captured, entry):
    run, verdict = captured
    client = Escalator(run)
    original = client.request_reviewers

    def competing(ref, reviewers):
        with pytest.raises(PostingError, match="locked"):
            entry(run, verdict, client=client)
        return original(ref, reviewers)

    client.request_reviewers = competing
    with run_lock(run):
        with pytest.raises(PostingError, match="locked"):
            post(run, verdict, client=client)
    assert client.reads == []
    assert post(run, verdict, client=client).verdict == "escalated"
    assert len(client.creates) == len(client.requests) == 1


@pytest.mark.parametrize("entry", [post, post_review])
@pytest.mark.parametrize("change", ["diff", "config", "targets", "exhaustion", "run_id",
                                    "verdict", "findings", "ordinary"])
def test_changed_evidence_cannot_bypass_pending_requests(captured, entry, change):
    run, verdict = captured
    client = Escalator(run, [PostingRejected("unsent", unsent=True)])
    with pytest.raises(PostingRejected):
        post(run, verdict, client=client)
    if change == "diff":
        with (run / "diff.patch").open("a") as stream:
            stream.write("\n")
    elif change == "config":
        replace_config(run, "rounds: {max: 4}\n")
    elif change == "targets":
        replace_config(run, "github: {human_reviewers: [Other]}\n")
    elif change == "exhaustion":
        verdict = derive_verdict([], verdict.config, exhaustion=Exhaustion("panel", 2, 2))
    elif change == "ordinary":
        verdict = derive_verdict([], verdict.config)
    elif change == "run_id":
        data = state(run, "posting.json")
        data["run_id"] = "c" * 32
        save(run, data, "posting.json")
    else:
        save(run, {}, f"{change}.json")
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.reads == [] and len(client.creates) == len(client.requests) == 1


@pytest.mark.parametrize("entry", [post, post_review])
@pytest.mark.parametrize("artifact", ["posting.json", "review-payload.json", "escalation.json",
                                      "reviewer-request.json"])
def test_missing_linked_artifact_blocks_network(captured, entry, artifact):
    run, verdict = captured
    client = Escalator(run)
    post(run, verdict, client=client)
    (run / artifact).unlink()
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.reads == [] and len(client.creates) == len(client.requests) == 1


@pytest.mark.parametrize("entry", [post, post_review])
def test_future_stage_with_unknown_review_is_rejected_before_recovery_reads(captured, entry):
    run, verdict = captured
    client = Escalator(run)
    post(run, verdict, client=client)
    data = state(run, "posting.json")
    data.update(status="unknown", receipt=None)
    save(run, data, "posting.json")
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.reads == []


@pytest.mark.parametrize("stage", ["review", "request"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_sending_persistence_failure_never_blindly_retries(
    captured, monkeypatch, stage, after_replace,
):
    import scrutare.poster.escalation as escalation
    import scrutare.poster.posting as posting
    run, verdict = captured
    client = Escalator(run)
    module = posting if stage == "review" else escalation
    filename = "posting.json" if stage == "review" else "escalation.json"
    original = module.atomic_write

    def fail(path, data):
        if path.name == filename and json.loads(data).get("status") == "sending":
            if after_replace:
                original(path, data)
            raise PostingError("disk failure")
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail)
        with pytest.raises(PostingError):
            post(run, verdict, client=client)
    assert client.requests == []
    assert len(client.creates) == int(stage == "request")
    if after_replace:
        with pytest.raises(PostingUncertain):
            post(run, verdict, client=client)
        assert client.requests == []
        assert len(client.creates) == int(stage == "request")
    else:
        assert post(run, verdict, client=client).verdict == "escalated"
        assert len(client.creates) == len(client.requests) == 1


@pytest.mark.parametrize("entry", [post, post_review])
def test_partial_request_preparation_is_preserved_and_blocks_network(captured, monkeypatch, entry):
    import scrutare.poster.escalation as escalation
    run, verdict = captured
    client = Escalator(run)
    original = escalation.atomic_write

    def fail(path, data):
        if path.name == "reviewer-request.json":
            raise PostingError("disk failure")
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(escalation, "atomic_write", fail)
        with pytest.raises(PostingError):
            post(run, verdict, client=client)
    journal = (run / "escalation.json").read_bytes()
    assert state(run)["status"] == "prepared"
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert (run / "escalation.json").read_bytes() == journal
    assert client.reads == client.requests == [] and len(client.creates) == 1


@pytest.mark.parametrize("updates", [
    {"status": "prepared", "attempts": 1},
    {"status": "sending", "attempts": 0},
    {"status": "unknown", "retry_at": 10},
    {"status": "rejected", "failure": "throttle", "http_status": 422, "retry_at": 10},
    {"status": "rejected", "failure": "throttle", "http_status": 429, "retry_at": -1},
    {"status": "rejected", "failure": "unsent", "http_status": 500},
    {"status": "rejected", "failure": "permanent", "http_status": 500},
    {"status": "rejected", "failure": "other"},
    {"status": "rejected", "failure": "unsent", "attempts": 4},
    {"status": "posted", "receipt": {"reviewers": ["alice", "service-bot"],
                                       "provenance": "post_response"}},
    {"status": "posted", "receipt": {"reviewers": ["Alice", "service-bot"],
                                       "provenance": "unknown"}},
    {"status": "posted", "receipt": {"reviewers": ["Alice", "service-bot"],
                                       "provenance": "post_response", "extra": True}},
    {"schema_version": True},
    {"pr_number": True},
])
@pytest.mark.parametrize("entry", [post, post_review])
def test_invalid_request_state_cannot_reach_network(captured, updates, entry):
    run, verdict = captured
    client = Escalator(run, [PostingUncertain("timeout")])
    with pytest.raises(PostingUncertain):
        post(run, verdict, client=client)
    save(run, state(run) | updates)
    client.reads.clear()
    with pytest.raises(PostingError):
        entry(run, verdict, client=client)
    assert client.reads == [] and len(client.requests) == 1


def test_nonadvancing_sleeper_cannot_bypass_request_deadline(captured, monkeypatch):
    import scrutare.poster.posting as posting
    run, verdict = captured
    monkeypatch.setattr(posting, "_now", lambda: 100)
    client = Escalator(run, [PostingRateLimited(status=429, retry_after=10)])
    waits = []
    with pytest.raises(PostingRateLimited):
        post(run, verdict, client=client, sleeper=waits.append)
    assert waits == [10] and len(client.requests) == len(client.creates) == 1


def test_unsent_request_budget_is_shared_across_restarts(captured):
    run, verdict = captured
    client = Escalator(run, [PostingRejected("unsent", unsent=True) for _ in range(4)])
    for _ in range(4):
        with pytest.raises(PostingRejected):
            post(run, verdict, client=client)
    assert len(client.requests) == 3 and len(client.creates) == 1


@pytest.mark.parametrize("receipt", [object(),
                                    ReviewerRequestReceipt(("Other",), "post_response"),
                                    ReviewerRequestReceipt(("Alice", "service-bot"),
                                                           "observed_requested")])
def test_invalid_direct_response_cannot_claim_success(captured, receipt):
    run, verdict = captured
    client = Escalator(run)

    def invalid(ref, reviewers):
        client.requests.append(reviewers)
        return receipt

    client.request_reviewers = invalid
    with pytest.raises(PostingUncertain):
        post(run, verdict, client=client)
    assert state(run)["status"] == "unknown" and len(client.requests) == 1


def test_second_stage_failure_retains_confirmed_review(captured):
    run, verdict = captured
    client = Escalator(run, outcomes=[PostingRejected("Reviewer request rejected", status=422)])
    with pytest.raises(PostingRejected) as error:
        post(run, verdict, client=client)
    assert error.value.confirmed_posting
    assert error.value.confirmed_review == client.receipt
    assert state(run, "posting.json")["status"] == "posted"
    assert state(run)["status"] == "rejected"
