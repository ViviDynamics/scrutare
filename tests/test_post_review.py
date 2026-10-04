"""Durable posting uses real captured artifacts and a network-only fake."""

import json
from collections import deque
from dataclasses import asdict

import pytest

from scrutare.config import parse_config
from scrutare.engine.github import GitHubClient, PullRequestRef
from scrutare.engine.ingestion import ingest_pr
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict
from scrutare.poster import PostedReview, PostingError, PostingRejected

REF = PullRequestRef("owner", "repo", 12)
SHA = "a" * 40
DIFF = ("diff --git a/example.py b/example.py\n--- a/example.py\n+++ b/example.py\n"
        "@@ -1 +1 @@\n-old\n+new\n")
RAW_CONFIG = b"# preserved snapshot\r\nmodels: {default: {model: test-model}}\r\n"


def metadata(**changes):
    return {
        "number": 12, "state": "open", "merged": False,
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
        "changed_files": 1, "head": {"sha": SHA}, "title": "Change", "body": "Context",
        **changes,
    }


class CaptureClient(GitHubClient):
    def get_pr(self, ref):
        assert ref == REF
        return metadata()

    def get_diff(self, ref):
        return DIFF

    def get_files(self, ref):
        return [{"filename": "example.py", "status": "modified"}]

    def get_reviews(self, ref):
        return []

    get_comments = get_reviews
    get_review_comments = get_reviews


@pytest.fixture
def captured(tmp_path):
    config = parse_config(RAW_CONFIG)
    run = ingest_pr(CaptureClient(), REF, tmp_path, config_bytes=RAW_CONFIG,
                    config_data=config.to_dict())
    verdict = derive_verdict(dedupe_findings([
        Finding(Anchor("example.py", 1), "security", "Problem", "Risk", "security"),
        Finding(Anchor("example.py", 1), "style", "Problem", "Clarity", "junior-dev"),
    ]), config.verdict)
    return run, verdict


class FakePoster:
    def __init__(self, outcomes=(), *, current=None):
        self.outcomes = deque(outcomes)
        self.current = current or metadata()
        self.creates = []
        self.reads = []
        self.reviews = []

    def get_pr(self, ref):
        assert ref == REF
        self.reads.append("pr")
        return self.current

    def create_review(self, ref, payload):
        assert ref == REF
        self.creates.append(payload)
        self.receipt = PostedReview(42, "https://github.com/owner/repo/pull/12#pullrequestreview-42",
                                   SHA, payload.body, {"APPROVE": "APPROVED",
                                       "REQUEST_CHANGES": "CHANGES_REQUESTED",
                                       "COMMENT": "COMMENTED"}[payload.event], "scrutare[bot]")
        self.reviews = [candidate(self.receipt)]
        outcome = self.outcomes.popleft() if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return self.receipt

    def get_reviews(self, ref):
        assert ref == REF
        self.reads.append("reviews")
        return self.reviews

    def get_login(self):
        self.reads.append("login")
        return "scrutare[bot]"


def candidate(receipt):
    return {"id": receipt.review_id, "html_url": receipt.html_url,
            "commit_id": receipt.commit_id, "body": receipt.body, "state": receipt.state,
            "user": {"login": receipt.login}}


def post(*args, **kwargs):
    from scrutare.poster import post_review
    return post_review(*args, **kwargs)


def test_complete_review_receipt_and_replay_without_second_create(captured):
    run, verdict = captured
    original = {p.name: p.read_bytes() for p in run.iterdir()}
    client = FakePoster()
    receipt = post(run, verdict, client=client)
    assert receipt.review_id == 42
    assert client.reads == ["pr"]
    assert len(client.creates) == 1
    payload = client.creates[0]
    assert payload.event == "REQUEST_CHANGES"
    assert payload.head_sha == SHA
    assert len(payload.comments) == 1
    assert "Risk" in payload.comments[0].body and "Clarity" in payload.comments[0].body
    assert (run / "review-payload.json").read_bytes() == payload.to_bytes()
    journal = json.loads((run / "posting.json").read_bytes())
    assert journal["status"] == "posted"
    assert journal["receipt"] == asdict(receipt)
    assert (run / "verdict.json").read_bytes() == verdict.to_bytes()
    assert json.loads((run / "findings.json").read_bytes()) == [
        f.to_dict() for f in verdict.findings
    ]
    assert all((run / name).read_bytes() == data for name, data in original.items())
    assert post(run, verdict, client=client) == receipt
    assert len(client.creates) == 1 and client.reads == ["pr"]


def test_changed_evidence_for_started_run_fails_before_network(captured):
    run, verdict = captured
    client = FakePoster([PostingRejected("Rejected", status=422)])
    with pytest.raises(PostingRejected):
        post(run, verdict, client=client)
    changed = derive_verdict([], verdict.config)
    with pytest.raises(PostingError):
        post(run, changed, client=client)
    assert len(client.creates) == 1


@pytest.mark.parametrize("changes", [{"state": "closed"}, {"merged": True}])
def test_closure_before_write_has_no_create(captured, changes):
    from scrutare.engine.github import PullRequestUnavailable
    run, verdict = captured
    client = FakePoster(current=metadata(**changes))
    with pytest.raises(PullRequestUnavailable):
        post(run, verdict, client=client)
    assert client.creates == []
    assert json.loads((run / "posting.json").read_bytes())["status"] == "prepared"


def test_force_push_keeps_captured_sha_and_permanent_rejection(captured):
    run, verdict = captured
    client = FakePoster([PostingRejected("Rejected", status=422)],
                        current=metadata(head={"sha": "c" * 40}))
    for _ in range(2):
        with pytest.raises(PostingRejected):
            post(run, verdict, client=client)
    assert len(client.creates) == 1
    assert client.creates[0].head_sha == SHA


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def clocked(monkeypatch):
    from scrutare.poster import posting
    clock = Clock()
    monkeypatch.setattr(posting, "_now", clock.time)
    return clock


def test_explicit_throttle_then_success_rechecks_pr_and_persists_sending(captured, monkeypatch):
    from scrutare.poster import PostingRateLimited
    run, verdict = captured
    clock = clocked(monkeypatch)
    client = FakePoster([PostingRateLimited(status=429, retry_after=10)])
    original_create = client.create_review

    def observe(ref, payload):
        journal = json.loads((run / "posting.json").read_bytes())
        assert journal["status"] == "sending"
        assert journal["attempts"] == len(client.creates) + 1
        assert journal["retry_at"] is None
        return original_create(ref, payload)

    client.create_review = observe
    assert post(run, verdict, client=client, sleeper=clock.sleep).review_id == 42
    assert clock.waits == [10]
    assert client.reads == ["pr", "pr"]
    assert len(client.creates) == 2
    assert client.creates[0] == client.creates[1]


def test_retry_deadline_survives_restart_without_sending_early(captured, monkeypatch):
    from scrutare.poster import PostingRateLimited
    run, verdict = captured
    clock = clocked(monkeypatch)
    client = FakePoster([PostingRateLimited(status=403, retry_after=120)])
    with pytest.raises(PostingRateLimited):
        post(run, verdict, client=client, sleeper=clock.sleep)
    journal = json.loads((run / "posting.json").read_bytes())
    assert journal["retry_at"] == 1120
    assert journal["attempts"] == 1 and journal["status"] == "rejected"
    with pytest.raises(PostingRateLimited):
        post(run, verdict, client=client, sleeper=clock.sleep)
    assert len(client.creates) == 1 and clock.waits == []
    clock.now = 1070
    assert post(run, verdict, client=client, sleeper=clock.sleep).review_id == 42
    assert clock.waits == [50] and len(client.creates) == 2


def test_retry_budget_survives_restart(captured, monkeypatch):
    from scrutare.poster import PostingRateLimited
    run, verdict = captured
    clock = clocked(monkeypatch)
    client = FakePoster([PostingRateLimited(status=429, retry_after=0) for _ in range(4)])
    for _ in range(2):
        with pytest.raises(PostingRejected):
            post(run, verdict, client=client, sleeper=clock.sleep)
    assert len(client.creates) == 3
    assert json.loads((run / "posting.json").read_bytes())["attempts"] == 3


def test_known_unsent_failure_can_resume_later(captured):
    run, verdict = captured
    client = FakePoster([PostingRejected("Cannot launch", unsent=True)])
    with pytest.raises(PostingRejected):
        post(run, verdict, client=client)
    assert json.loads((run / "posting.json").read_bytes())["failure"] == "unsent"
    assert post(run, verdict, client=client).review_id == 42
    assert len(client.creates) == 2


def test_uncertain_write_recovers_exact_own_review_without_resending(captured):
    from scrutare.poster import PostingUncertain
    run, verdict = captured
    client = FakePoster([PostingUncertain("Delivery uncertain")])
    assert post(run, verdict, client=client).review_id == 42
    assert len(client.creates) == 1
    assert client.reads == ["pr", "reviews", "login"]
    assert json.loads((run / "posting.json").read_bytes())["status"] == "posted"


@pytest.mark.parametrize("bad", ["absent", "duplicate", "author", "body", "commit", "state",
                                  "id", "url", "read", "login"])
def test_unresolved_ambiguity_never_resends(captured, bad):
    from scrutare.poster import PostingUncertain
    run, verdict = captured
    client = FakePoster([PostingUncertain("Delivery uncertain")])
    original_create = client.create_review

    def uncertain(ref, payload):
        try:
            original_create(ref, payload)
        finally:
            if bad == "absent":
                client.reviews = []
            elif bad == "duplicate":
                client.reviews *= 2
            elif bad in ("body", "commit", "state", "id", "url"):
                field = {"commit": "commit_id", "url": "html_url"}.get(bad, bad)
                client.reviews[0][field] = "wrong"
            elif bad == "author":
                client.reviews[0]["user"] = {"login": "another"}

    client.create_review = uncertain
    if bad in ("read", "login"):
        def fail(*args):
            raise PostingError("Read unavailable")
        setattr(client, "get_reviews" if bad == "read" else "get_login", fail)
    for _ in range(2):
        with pytest.raises(PostingUncertain):
            post(run, verdict, client=client)
    assert len(client.creates) == 1
    assert json.loads((run / "posting.json").read_bytes())["status"] == "unknown"


def test_restart_from_sending_reconciles_only(captured):
    from scrutare.poster import PostingUncertain
    run, verdict = captured
    client = FakePoster([PostingUncertain("Delivery uncertain")])
    client.get_reviews = lambda ref: []
    with pytest.raises(PostingUncertain):
        post(run, verdict, client=client)
    journal = json.loads((run / "posting.json").read_bytes())
    journal["status"] = "sending"
    (run / "posting.json").write_text(json.dumps(journal))
    client.get_reviews = lambda ref: client.reviews
    assert post(run, verdict, client=client).review_id == 42
    assert len(client.creates) == 1


def test_receipt_write_failure_preserves_sending_and_reconciles(captured, monkeypatch):
    from scrutare.poster import posting
    run, verdict = captured
    client = FakePoster()
    write = posting.atomic_write

    def fail_receipt(path, data):
        if path.name == "posting.json" and json.loads(data)["status"] == "posted":
            raise PostingError("Cannot persist")
        return write(path, data)

    monkeypatch.setattr(posting, "atomic_write", fail_receipt)
    with pytest.raises(PostingError):
        post(run, verdict, client=client)
    assert json.loads((run / "posting.json").read_bytes())["status"] == "sending"
    monkeypatch.setattr(posting, "atomic_write", write)
    assert post(run, verdict, client=client).review_id == 42
    assert len(client.creates) == 1


@pytest.mark.parametrize("change", ["status", "attempts", "schema_version", "extra", "receipt"])
def test_journal_corruption_rejects_before_network(captured, change):
    run, verdict = captured
    client = FakePoster()
    post(run, verdict, client=client)
    state = json.loads((run / "posting.json").read_bytes())
    state[change] = "secret-corrupt"
    (run / "posting.json").write_text(json.dumps(state))
    with pytest.raises(PostingError) as error:
        post(run, verdict, client=client)
    assert "secret" not in str(error.value)
    assert len(client.creates) == 1 and client.reads == ["pr"]


@pytest.mark.parametrize("artifact", ["metadata.json", "config.yaml", "config.json", "diff.patch",
                                       "verdict.json", "findings.json", "review-payload.json"])
def test_changed_artifacts_reject_before_network(captured, artifact):
    run, verdict = captured
    client = FakePoster()
    post(run, verdict, client=client)
    (run / artifact).write_bytes(b"secret-corrupt")
    with pytest.raises(PostingError) as error:
        post(run, verdict, client=client)
    assert "secret" not in str(error.value)
    assert len(client.creates) == 1 and client.reads == ["pr"]


def test_concurrent_os_lock_rejects_without_unlinking_inode(captured):
    import fcntl
    run, verdict = captured
    client = FakePoster()
    lock = run / ".posting.lock"
    with lock.open("a+b") as stream:
        inode = lock.stat().st_ino
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PostingError, match="lock"):
            post(run, verdict, client=client)
        assert lock.stat().st_ino == inode
        assert client.creates == [] and client.reads == []
    assert post(run, verdict, client=client).review_id == 42
    assert lock.stat().st_ino == inode


@pytest.mark.parametrize("field,value", [
    ("repository", "wrong/repo"), ("pr_number", 13), ("pr_number", True),
    ("head_sha", "c" * 40),
])
def test_top_level_metadata_must_match_nested_capture(captured, field, value):
    run, verdict = captured
    data = json.loads((run / "metadata.json").read_bytes())
    data[field] = value
    (run / "metadata.json").write_text(json.dumps(data))
    client = FakePoster()
    with pytest.raises(PostingError):
        post(run, verdict, client=client)
    assert not (run / "posting.json").exists()
    assert client.reads == [] and client.creates == []


@pytest.mark.parametrize("change", ["mode", "strategy", "policy"])
def test_changed_valid_configuration_cannot_change_started_run(captured, change):
    run, verdict = captured
    client = FakePoster([PostingRejected("Rejected", status=422)])
    with pytest.raises(PostingRejected):
        post(run, verdict, client=client)
    raw = RAW_CONFIG + {"mode": b"github: {post_mode: comment}\n",
                        "strategy": b"strategy: debate\n",
                        "policy": b"verdict:\n  blocking_categories: [security]\n"
                                  b"  advisory_categories: [correctness, regression, style, "
                                  b"consistency, docs]\n"}[change]
    (run / "config.yaml").write_bytes(raw)
    (run / "config.json").write_text(json.dumps(parse_config(raw).to_dict()))
    with pytest.raises(PostingError):
        post(run, verdict, client=client)
    assert len(client.creates) == 1 and client.reads == ["pr"]


@pytest.mark.parametrize("status", ["prepared", "sending"])
def test_state_persistence_failure_stops_before_create(captured, monkeypatch, status):
    from scrutare.poster import posting
    run, verdict = captured
    client = FakePoster()
    write = posting.atomic_write

    def fail(path, data):
        if path.name == "posting.json" and json.loads(data)["status"] == status:
            raise PostingError("Cannot persist")
        return write(path, data)

    monkeypatch.setattr(posting, "atomic_write", fail)
    with pytest.raises(PostingError):
        post(run, verdict, client=client)
    assert client.creates == []
    monkeypatch.setattr(posting, "atomic_write", write)
    assert post(run, verdict, client=client).review_id == 42
    assert len(client.creates) == 1


def test_interrupted_retry_wait_keeps_deadline_and_never_sends_early(captured, monkeypatch):
    from scrutare.poster import PostingRateLimited
    run, verdict = captured
    clock = clocked(monkeypatch)
    client = FakePoster([PostingRateLimited(status=403, retry_after=60)])

    def interrupted(seconds):
        assert seconds == 60
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        post(run, verdict, client=client, sleeper=interrupted)
    assert len(client.creates) == 1
    assert json.loads((run / "posting.json").read_bytes())["retry_at"] == 1060
    with pytest.raises(PostingRateLimited):
        post(run, verdict, client=client, sleeper=lambda seconds: None)
    assert len(client.creates) == 1
    assert post(run, verdict, client=client, sleeper=clock.sleep).review_id == 42
    assert clock.waits == [60]


def test_closure_during_throttle_wait_aborts_retry(captured, monkeypatch):
    from scrutare.engine.github import PullRequestUnavailable
    from scrutare.poster import PostingRateLimited
    run, verdict = captured
    clock = clocked(monkeypatch)
    client = FakePoster([PostingRateLimited(status=403, retry_after=60)])

    def closes(seconds):
        clock.sleep(seconds)
        client.current = metadata(state="closed")

    with pytest.raises(PullRequestUnavailable):
        post(run, verdict, client=client, sleeper=closes)
    assert len(client.creates) == 1
    assert client.reads == ["pr", "pr"]


@pytest.mark.parametrize("invalid", [True, -1, float("nan"), float("inf"), 10 ** 1000],
                         ids=["boolean", "negative", "nan", "infinite", "overflow"])
def test_corrupt_retry_deadline_is_safe_and_never_resends(captured, monkeypatch, invalid):
    from scrutare.poster import PostingRateLimited
    run, verdict = captured
    clock = clocked(monkeypatch)
    client = FakePoster([PostingRateLimited(status=429, retry_after=120)])
    with pytest.raises(PostingRateLimited):
        post(run, verdict, client=client, sleeper=clock.sleep)
    state = json.loads((run / "posting.json").read_bytes())
    state["retry_at"] = invalid
    (run / "posting.json").write_text(json.dumps(state))
    with pytest.raises(PostingError):
        post(run, verdict, client=client, sleeper=clock.sleep)
    assert len(client.creates) == 1


@pytest.mark.parametrize("kind", ["timeout", "5xx", "malformed"])
def test_actual_transport_uncertainty_recovers_via_paginated_reviews(captured, kind):
    import subprocess

    from scrutare.poster import ReviewClient
    run, verdict = captured
    requests = []
    review = None

    def runner(args, **kwargs):
        nonlocal review
        requests.append(args)
        if "POST" in args:
            body = json.loads(kwargs["input"])
            assert body["commit_id"] == SHA and body["event"] == "REQUEST_CHANGES"
            review = {"id": 42,
                      "html_url": "https://github.com/owner/repo/pull/12#pullrequestreview-42",
                      "commit_id": SHA, "body": body["body"], "state": "CHANGES_REQUESTED",
                      "user": {"login": "scrutare[bot]"}}
            if kind == "timeout":
                raise subprocess.TimeoutExpired(args, 60, output=b"secret")
            output = b"HTTP/2 503\n\n{}" if kind == "5xx" else b"HTTP/2 200\n\n{}"
            return subprocess.CompletedProcess(args, int(kind == "5xx"), output, b"")
        if "user" in args:
            return subprocess.CompletedProcess(args, 0,
                                               b'HTTP/2 200\n\n{"login":"scrutare[bot]"}', b"")
        data = [[], [review]] if "--paginate" in args else metadata()
        return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")

    receipt = post(run, verdict, client=ReviewClient(runner=runner, sleeper=lambda s: None))
    assert receipt.review_id == 42
    assert sum("POST" in args for args in requests) == 1
    assert post(run, verdict, client=FakePoster()) == receipt


def test_atomic_write_failure_cleans_temporary_and_preserves_journal(captured, monkeypatch):
    from pathlib import Path

    from scrutare.poster.journal import atomic_write
    run, verdict = captured
    journal = run / "posting.json"
    journal.write_bytes(b"original")

    def fail(source, destination):
        assert source.parent == run and source.read_bytes() == b"replacement"
        assert destination.read_bytes() == b"original"
        raise OSError("secret-details")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PostingError) as error:
        atomic_write(journal, b"replacement")
    assert "secret" not in str(error.value) and error.value.__suppress_context__
    assert journal.read_bytes() == b"original"
    assert not list(run.glob(".posting-*.tmp"))


def test_documented_python_example_executes_with_fake_client(captured, capsys):
    import re
    from pathlib import Path
    run, verdict = captured
    client = FakePoster()
    docs = (Path(__file__).parents[1] / "docs" / "posting.md").read_text()
    example, = re.findall(r"```python\n(.*?)```", docs, flags=re.DOTALL)
    namespace = {"run_dir": run, "verdict": verdict, "client": client}
    exec(compile(example, "docs/posting.md", "exec"), namespace)
    assert capsys.readouterr().out == (
        "https://github.com/owner/repo/pull/12#pullrequestreview-42\n"
    )
    exec(compile(example, "docs/posting.md", "exec"), namespace)
    assert namespace["receipt"].review_id == 42
    assert len(client.creates) == 1


def test_unencodable_evidence_fails_safely_before_network(captured):
    run, verdict = captured
    invalid = derive_verdict(dedupe_findings([
        Finding(Anchor("example.py", 1), "security", "secret\ud800", "Risk", "security"),
    ]), verdict.config)
    client = FakePoster()
    with pytest.raises(PostingError) as error:
        post(run, invalid, client=client)
    assert "secret" not in str(error.value)
    assert client.creates == [] and client.reads == []


@pytest.mark.parametrize("outcome", ["posted", "rejected", "unknown"])
def test_existing_evidence_bytes_remain_identical_through_outcomes(captured, outcome):
    from scrutare.poster import PostingUncertain
    run, verdict = captured
    (run / "verdict.json").write_bytes(verdict.to_bytes())
    findings_bytes = json.dumps([f.to_dict() for f in verdict.findings], indent=4).encode()
    (run / "findings.json").write_bytes(findings_bytes)
    original = {p.name: p.read_bytes() for p in run.iterdir()}
    error = {"posted": None, "rejected": PostingRejected("Rejected", status=422),
             "unknown": PostingUncertain("Uncertain")}[outcome]
    client = FakePoster([error])
    if outcome == "unknown":
        client.get_reviews = lambda ref: []
    if error:
        with pytest.raises(type(error)):
            post(run, verdict, client=client)
    else:
        post(run, verdict, client=client)
    assert all((run / name).read_bytes() == data for name, data in original.items())


@pytest.mark.parametrize("mode,event,state", [("review", "APPROVE", "APPROVED"),
                                               ("comment", "COMMENT", "COMMENTED")])
def test_durable_empty_verdict_posts_exact_configured_event(captured, mode, event, state):
    run, verdict = captured
    raw = RAW_CONFIG + f"github: {{post_mode: {mode}}}\n".encode()
    (run / "config.yaml").write_bytes(raw)
    (run / "config.json").write_text(json.dumps(parse_config(raw).to_dict()))
    empty = derive_verdict([], verdict.config)
    client = FakePoster()
    receipt = post(run, empty, client=client)
    assert receipt.state == state
    assert client.creates[0].event == event and client.creates[0].comments == ()
    assert post(run, empty, client=client) == receipt and len(client.creates) == 1


def test_unsupported_lock_fails_before_network(captured, monkeypatch):
    import fcntl
    run, verdict = captured
    client = FakePoster()

    def unsupported(*args):
        raise OSError("secret-lock-details")

    monkeypatch.setattr(fcntl, "flock", unsupported)
    with pytest.raises(PostingError) as error:
        post(run, verdict, client=client)
    assert "secret" not in str(error.value)
    assert client.creates == [] and client.reads == []


def test_good_candidate_with_another_malformed_marker_remains_uncertain(captured):
    from scrutare.poster import PostingUncertain
    run, verdict = captured
    client = FakePoster([PostingUncertain("Uncertain")])
    create = client.create_review

    def ambiguous(ref, payload):
        try:
            create(ref, payload)
        finally:
            client.reviews.append(client.reviews[0] | {"body": payload.body + " altered"})

    client.create_review = ambiguous
    with pytest.raises(PostingUncertain):
        post(run, verdict, client=client)
    assert len(client.creates) == 1
    assert json.loads((run / "posting.json").read_bytes())["status"] == "unknown"


@pytest.mark.parametrize("repository", [None, 12, True, [], {}, "", " \t\n"],
                         ids=["null", "number", "boolean", "array", "object", "empty", "blank"])
def test_invalid_captured_repository_never_resolves_ambient_checkout(
    captured, monkeypatch, repository,
):
    import subprocess
    run, verdict = captured
    data = json.loads((run / "metadata.json").read_bytes())
    data["repository"] = repository
    (run / "metadata.json").write_text(json.dumps(data))
    calls = []

    def forbidden_subprocess(args, **kwargs):
        calls.append(args)
        raise OSError("secret-ambient-resolution")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    client = FakePoster()
    with pytest.raises(PostingError) as error:
        post(run, verdict, client=client)
    assert "secret" not in str(error.value)
    assert calls == []
    assert client.reads == [] and client.creates == []
    assert not (run / "posting.json").exists()
