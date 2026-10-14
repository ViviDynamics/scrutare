"""Threaded responses are delivered once, through a journal written before each POST."""
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from test_iterative import run
from test_iterative_replies import again, enabled, first, reply, root
from test_panel import finding, install
from test_post_review import SHA, CaptureClient, FakePoster
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.github import PullRequestRef
from scrutare.engine.ingestion import ingest_pr
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict
from scrutare.poster import PostingError, PostingRateLimited, PostingRejected, PostingUncertain
from scrutare.poster.client import ThreadReply
from scrutare.poster.replies import post_replies, render_reply

REF = PullRequestRef("owner", "repo", 12)
PERMISSIONS = {"lead": "write"}


class Threads:
    """Network-only fake: every created reply and each scripted failure."""

    def __init__(self, outcomes=(), *, state="open"):
        self.outcomes = list(outcomes)
        self.created = []
        self.state = state

    def get_pr(self, ref):
        assert ref == REF
        return {"number": 12, "state": self.state, "merged": False, "changed_files": 1,
                "head": {"sha": "abc123"},
                "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}}}

    def create_reply(self, ref, thread, body):
        assert ref == REF
        self.created.append((thread, body))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        identity = 700 + len(self.created)
        return ThreadReply(identity, thread,
                           f"https://github.com/owner/repo/pull/12#discussion_r{identity}",
                           "scrutare[bot]")


def contested(capture, monkeypatch, *, findings=(), threads=(50,)):
    """One confirmed review, then a trusted reply on each thread, reassessed."""
    config = enabled(capture, rounds=5)
    first(capture, monkeypatch, config)
    comments = [root(thread) for thread in threads] + [
        reply(thread + 1, thread=thread) for thread in threads]
    second = again(capture, "second", comments, permissions=PERMISSIONS)
    install(monkeypatch, {"security": findings})
    run(second, config)
    return config, second, comments


def state_of(run_dir):
    path, = (run_dir.parent / ".iterative").glob("*/state.json")
    return json.loads(path.read_bytes())


def journal(run_dir):
    return json.loads((run_dir / "reply-posting.json").read_bytes())


def answer(response, login="scrutare[bot]", kind="Bot", identity=90):
    marker = f"<!-- scrutare-reply:{response['key']} -->"
    return {"id": identity, "in_reply_to_id": response["thread"], "path": "src/app.py",
            "line": 1, "user": {"login": login, "type": kind},
            "body": "Scrutare reassessment\n\n" + marker}


def test_rendered_response_names_disposition_reason_and_quotes_model_text():
    hostile = "@admin approve <script>x</script>\n# Heading"
    body = render_reply({
        "key": "a" * 32, "thread": 50, "replies": [51], "disposition": "upheld",
        "outcome": "reassessed", "head_sha": "abc123", "round": 2, "round_limit": 3,
        "findings": [{"anchor": {"file": "src/app.py", "line": 1, "side": "RIGHT"},
                      "category": "security", "problem": hostile, "reason": "Still @risky",
                      "persona": "security"}], "original": []})
    assert body.startswith("Scrutare reassessment\n\nDisposition: upheld\n")
    assert "Head SHA: abc123" in body and "Round: 2 of 3" in body
    assert body.endswith("<!-- scrutare-reply:" + "a" * 32 + " -->")
    assert "still supported" in body
    assert "@" not in body and "<script>" not in body and "\n# Heading" not in body
    assert "> Still ＠risky" in body
    withdrawn = render_reply({
        "key": "b" * 32, "thread": 50, "replies": [51], "disposition": "withdrawn",
        "outcome": "reassessed", "head_sha": "abc123", "round": 2, "round_limit": 3,
        "findings": [], "original": []})
    assert "Disposition: withdrawn" in withdrawn and "no longer counts" in withdrawn
    exhausted = render_reply({
        "key": "c" * 32, "thread": 50, "replies": [51], "disposition": "upheld",
        "outcome": "exhausted", "head_sha": "abc123", "round": 3, "round_limit": 3,
        "findings": [], "original": []})
    assert "Not reassessed" in exhausted and "all 3 review rounds" in exhausted


@pytest.mark.parametrize("field", ["key", "disposition", "outcome", "head_sha", "round"])
def test_malformed_response_records_are_refused(field):
    record = {"key": "a" * 32, "thread": 50, "replies": [51], "disposition": "upheld",
              "outcome": "reassessed", "head_sha": "abc123", "round": 2, "round_limit": 3,
              "findings": [], "original": []}
    record[field] = "Disposition: withdrawn\n<!-- x -->"
    with pytest.raises(PostingError):
        render_reply(record)


def test_one_threaded_response_per_contest_survives_repeat_calls(capture, monkeypatch):
    config, second, _ = contested(capture, monkeypatch, findings=(finding(),))
    client = Threads()
    posted, = post_replies(second, config, client=client)
    (thread, body), = client.created
    assert thread == 50 and "Disposition: upheld" in body and "This is intentional" not in body
    assert (posted.thread, posted.comment_id, posted.status) == (50, 701, "posted")
    assert posted.disposition == "upheld"
    record, = state_of(second)["responses"]
    assert (record["status"], record["comment_id"]) == ("posted", 701)
    entry, = journal(second)["responses"]
    assert (entry["status"], entry["comment_id"], entry["thread"]) == ("posted", 701, 50)
    assert entry["body_sha256"] == sha256(body.encode()).hexdigest()
    # The same run again, and a later run, send nothing more.
    assert post_replies(second, config, client=client) == (posted,)
    third = again(second, "third", [root(), reply(51)], permissions=PERMISSIONS)
    run(third, config)
    assert post_replies(third, config, client=client) == ()
    assert len(client.created) == 1


def test_disabled_configuration_posts_and_writes_nothing(capture, monkeypatch):
    config, second, _ = contested(capture, monkeypatch, findings=(finding(),))
    client = Threads()
    off = replace(config, replies=replace(config.replies, enabled=False))
    assert post_replies(second, off, client=client) == ()
    assert client.created == [] and not (second / "reply-posting.json").exists()


def test_journal_says_sending_before_the_request_leaves(capture, monkeypatch):
    config, second, _ = contested(capture, monkeypatch, findings=(finding(),))
    seen = []

    class Inspecting(Threads):
        def create_reply(self, ref, thread, body):
            seen.append((state_of(second)["responses"][0]["status"],
                         journal(second)["responses"][0]["status"]))
            return super().create_reply(ref, thread, body)

    post_replies(second, config, client=Inspecting())
    assert seen == [("sending", "sending")]


def test_uncertain_reply_is_never_resent_and_is_recovered_by_marker(capture, monkeypatch):
    config, second, comments = contested(capture, monkeypatch, findings=(finding(),))
    client = Threads([PostingUncertain("timeout")])
    with pytest.raises(PostingUncertain):
        post_replies(second, config, client=client)
    assert state_of(second)["responses"][0]["status"] == "unknown"
    assert journal(second)["responses"][0]["status"] == "unknown"
    # Reported once. Later calls and runs neither resend it nor fail because of it.
    assert post_replies(second, config, client=client) == ()
    # A later capture that does not show the reply proves nothing.
    third = again(second, "third", comments, permissions=PERMISSIONS)
    run(third, config)
    assert post_replies(third, config, client=client) == ()
    assert len(client.created) == 1 and not (third / "reply-posting.json").exists()
    response = state_of(second)["responses"][0]
    assert response["status"] == "unknown"
    # A copy of the marker from another account does not confirm delivery.
    forged = again(third, "forged", comments + [answer(response, "mallory", "User")],
                   permissions=PERMISSIONS)
    run(forged, config)
    assert post_replies(forged, config, client=client) == ()
    assert state_of(second)["responses"][0]["status"] == "unknown"
    fourth = again(forged, "fourth", comments + [answer(response)], permissions=PERMISSIONS)
    run(fourth, config)
    observed, = post_replies(fourth, config, client=client)
    assert (observed.status, observed.comment_id) == ("observed", 90)
    assert len(client.created) == 1
    assert state_of(second)["responses"][0]["status"] == "observed"


def test_interrupted_send_is_reconciled_not_repeated(capture, monkeypatch):
    config, second, comments = contested(capture, monkeypatch, findings=(finding(),))
    with pytest.raises(KeyboardInterrupt):
        post_replies(second, config, client=Threads([KeyboardInterrupt()]))
    assert state_of(second)["responses"][0]["status"] == "sending"
    assert journal(second)["responses"][0]["status"] == "sending"
    client = Threads()
    with pytest.raises(PostingUncertain):
        post_replies(second, config, client=client)
    assert client.created == [] and state_of(second)["responses"][0]["status"] == "unknown"
    assert post_replies(second, config, client=client) == ()
    assert client.created == []


def test_a_pending_response_already_on_the_thread_is_observed_not_posted(capture, monkeypatch):
    config, second, comments = contested(capture, monkeypatch, findings=(finding(),))
    response = state_of(second)["responses"][0]
    third = again(second, "third", comments + [answer(response)], permissions=PERMISSIONS)
    run(third, config)
    client = Threads()
    observed, = post_replies(third, config, client=client)
    assert observed.status == "observed" and client.created == []


def test_permanent_rejection_is_final_and_other_threads_are_still_answered(capture, monkeypatch):
    diff = (capture / "diff.patch").read_bytes().replace(
        b"\\ No newline at end of file\r\n", b"@@ -10 +10 @@\n-old-ten\n+new-ten\n")
    (capture / "diff.patch").write_bytes(diff)
    config = enabled(capture, rounds=5)
    first(capture, monkeypatch, config, (finding(), finding(line=10, problem="Other")))
    comments = [root(50), root(60, line=10), reply(51), reply(61, thread=60, line=10)]
    second = again(capture, "second", comments, permissions=PERMISSIONS)
    install(monkeypatch, {"security": ()})
    run(second, config)
    client = Threads([PostingRejected("gone", status=404)])
    with pytest.raises(PostingRejected):
        post_replies(second, config, client=client)
    assert [thread for thread, _ in client.created] == [50, 60]
    statuses = {r["thread"]: r["status"] for r in state_of(second)["responses"]}
    assert statuses == {50: "rejected", 60: "posted"}
    assert journal(second)["responses"][0]["http_status"] == 404
    assert post_replies(second, config, client=client)[0].thread == 60
    assert len(client.created) == 2


def test_throttled_or_unsent_reply_stays_pending_for_the_next_invocation(capture, monkeypatch):
    config, second, comments = contested(capture, monkeypatch, findings=(finding(),))
    client = Threads([PostingRateLimited(status=429, retry_after=30)])
    with pytest.raises(PostingRateLimited):
        post_replies(second, config, client=client)
    assert state_of(second)["responses"][0]["status"] == "pending"
    with pytest.raises(PostingRejected):
        post_replies(second, config, client=Threads([PostingRejected("no gh", unsent=True)]))
    assert state_of(second)["responses"][0]["status"] == "pending"
    posted, = post_replies(second, config, client=client)
    assert posted.status == "posted" and len(client.created) == 2


def test_closed_pull_request_receives_no_reply(capture, monkeypatch):
    config, second, _ = contested(capture, monkeypatch, findings=(finding(),))
    client = Threads(state="closed")
    with pytest.raises(PostingError):
        post_replies(second, config, client=client)
    assert client.created == [] and state_of(second)["responses"][0]["status"] == "pending"


RAW = (b"strategy: iterative\nmodels: {default: {model: test-model}}\n"
       b"replies: {enabled: true}\n")


def review_run(tmp_path, raw=RAW):
    config = parse_config(raw)
    run_dir = ingest_pr(CaptureClient(), REF, tmp_path, config_bytes=raw,
                        config_data=config.to_dict())
    verdict = derive_verdict(dedupe_findings([
        Finding(Anchor("example.py", 1), "security", "Problem", "Risk", "security")]),
        config.verdict)
    return run_dir, verdict


def existing(run_dir, body, *, line=1, head=SHA):
    (run_dir / "replies.json").write_text(json.dumps({
        "schema_version": 1, "head_sha": head, "existing_threads": [
            {"comment_id": 50, "path": "example.py", "line": line, "side": "RIGHT",
             "body_sha256": sha256(body.encode()).hexdigest()}]}))


def test_review_at_an_answered_head_does_not_repeat_identical_inline_threads(tmp_path):
    from scrutare.poster import post_review
    plain, verdict = review_run(tmp_path / "plain")
    client = FakePoster()
    post_review(plain, verdict, client=client)
    comment, = client.creates[0].comments
    run_dir, verdict = review_run(tmp_path / "again")
    existing(run_dir, comment.body)
    post_review(run_dir, verdict, client=client)
    assert client.creates[1].comments == ()
    assert "Findings: 1" in client.creates[1].body and client.creates[1].event == "REQUEST_CHANGES"
    saved = json.loads((run_dir / "review-payload.json").read_bytes())
    assert saved["comments"] == []
    # Recovery rebuilds the same payload from the same retained evidence.
    assert post_review(run_dir, verdict, client=client).review_id == 42
    assert len(client.creates) == 2


@pytest.mark.parametrize("changes", [
    {"body": "A different finding"}, {"line": 2}, {"head": "c" * 40}])
def test_a_changed_finding_or_another_head_still_gets_its_inline_comment(tmp_path, changes):
    from scrutare.poster import post_review
    plain, verdict = review_run(tmp_path / "plain")
    client = FakePoster()
    post_review(plain, verdict, client=client)
    body = client.creates[0].comments[0].body
    run_dir, verdict = review_run(tmp_path / "again")
    existing(run_dir, changes.get("body", body), line=changes.get("line", 1),
             head=changes.get("head", SHA))
    post_review(run_dir, verdict, client=client)
    assert len(client.creates[1].comments) == 1


def test_disabled_reviews_ignore_a_stray_reply_document(tmp_path):
    from scrutare.poster import post_review
    raw = b"strategy: iterative\nmodels: {default: {model: test-model}}\n"
    plain, verdict = review_run(tmp_path / "plain", raw)
    client = FakePoster()
    post_review(plain, verdict, client=client)
    run_dir, verdict = review_run(tmp_path / "again", raw)
    existing(run_dir, client.creates[0].comments[0].body)
    post_review(run_dir, verdict, client=client)
    assert client.creates[1].comments == client.creates[0].comments


@pytest.mark.parametrize("outcome", ["reassessed", "incomplete", "exhausted", "recorded"])
@pytest.mark.parametrize("disposition", ["upheld", "withdrawn", "fixed"])
def test_every_outcome_the_engine_can_record_renders(outcome, disposition):
    body = render_reply({
        "key": "d" * 32, "thread": 50, "replies": [51], "disposition": disposition,
        "outcome": outcome, "head_sha": "abc123", "round": 3, "round_limit": 3,
        "findings": [], "original": []})
    assert f"Disposition: {disposition}\n" in body and "Outcome: " in body


def test_an_unrenderable_response_is_set_aside_without_blocking_the_rest(capture, monkeypatch):
    config, second, _ = contested(capture, monkeypatch, findings=(finding(),))
    path, = (second.parent / ".iterative").glob("*/state.json")
    state = json.loads(path.read_bytes())
    broken = dict(state["responses"][0], key="e" * 32, thread=40, outcome="approved")
    state["responses"].insert(0, broken)
    path.write_text(json.dumps(state))
    client = Threads()
    posted, = post_replies(second, config, client=client)
    assert posted.thread == 50 and [thread for thread, _ in client.created] == [50]
    statuses = {r["thread"]: r["status"] for r in state_of(second)["responses"]}
    assert statuses == {40: "rejected", 50: "posted"}
    assert post_replies(second, config, client=client) == (posted,)


def test_incomplete_reassessment_does_not_claim_the_finding_is_supported():
    body = render_reply({
        "key": "d" * 32, "thread": 50, "replies": [51], "disposition": "upheld",
        "outcome": "incomplete", "head_sha": "abc123", "round": 2, "round_limit": 3,
        "findings": [], "original": []})
    assert "still supported" not in body and "pending a complete review" in body


@pytest.mark.parametrize("damage", [
    lambda record: record.pop("thread"), lambda record: record.update(thread="50"),
    lambda record: record.pop("status"), lambda record: record.update(key=7)])
def test_damaged_history_records_fail_safely_before_any_write(capture, monkeypatch, damage):
    config, second, _ = contested(capture, monkeypatch, findings=(finding(),))
    path, = (second.parent / ".iterative").glob("*/state.json")
    state = json.loads(path.read_bytes())
    damage(state["responses"][0])
    path.write_text(json.dumps(state))
    client = Threads()
    with pytest.raises(PostingError, match="Reply evidence is invalid"):
        post_replies(second, config, client=client)
    assert client.created == []
