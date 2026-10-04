"""Exercise review transport with injected runners and no live requests."""

import importlib
import json
import subprocess
from unittest.mock import Mock

import pytest

from scrutare.engine.github import GitHubError, PullRequestRef
from scrutare.findings import Anchor
from scrutare.poster import ReviewComment, ReviewPayload

SHA = "a" * 40
REF = PullRequestRef("owner", "repo", 12)


def client_module():
    return importlib.import_module("scrutare.poster.client")


def payload(event="COMMENT", body="Review body"):
    return ReviewPayload(SHA, body, event, ())


def receipt(**changes):
    return {
        "id": 123,
        "html_url": "https://github.com/owner/repo/pull/12#pullrequestreview-123",
        "commit_id": SHA,
        "body": "Review body",
        "state": "COMMENTED",
        "user": {"login": "reviewer"},
        **changes,
    }


def framed(status, value, *, headers=None, newline="\r\n", suffix=""):
    lines = [f"HTTP/2.0 {status} Response"]
    lines.extend(f"{key}: {value}" for key, value in (headers or {}).items())
    return (newline.join(lines) + newline * 2 + json.dumps(value) + suffix).encode()


def response(status=200, value=None, **options):
    content = framed(status, receipt() if value is None else value, **options)
    if 200 <= status < 300:
        return subprocess.CompletedProcess([], 0, content, b"")
    return subprocess.CompletedProcess([], 1, b"", content)


def test_create_uses_one_stdin_write_and_preserves_authentication(monkeypatch):
    mod = client_module()
    monkeypatch.setenv("GH_HOST", "evil.example")
    monkeypatch.setenv("GH_TOKEN", "test-auth")
    hostile = "secret ; $(touch /tmp/should-not-run) `gh auth token` --repo evil\n"
    request = ReviewPayload(
        SHA, hostile, "COMMENT", (ReviewComment(Anchor("x.py", 1, "RIGHT"), hostile),)
    )
    runner = Mock(return_value=response(value=receipt(body=hostile)))
    result = mod.ReviewClient(runner=runner).create_review(REF, request)
    assert result == mod.PostedReview(
        123, "https://github.com/owner/repo/pull/12#pullrequestreview-123",
        SHA, hostile, "COMMENTED", "reviewer",
    )
    assert runner.call_count == 1
    args = runner.call_args.args[0]
    assert args == [
        "gh", "api", "--method", "POST", "repos/owner/repo/pulls/12/reviews",
        "--include", "--input", "-",
        "--header", "Accept: application/vnd.github+json",
        "--header", "X-GitHub-Api-Version: 2022-11-28",
        "--header", "Content-Type: application/json",
    ]
    assert hostile not in args
    options = runner.call_args.kwargs
    assert options["input"] == request.to_bytes()
    assert options["shell"] is False
    assert options["capture_output"] is True
    assert options["text"] is False
    assert options["timeout"] == 60
    assert options["env"]["GH_HOST"] == "github.com"
    assert options["env"]["GH_TOKEN"] == "test-auth"


@pytest.mark.parametrize("ref", [
    PullRequestRef("owner;secret", "repo", 12), PullRequestRef("owner", "..", 12),
    PullRequestRef("owner", "repo", 0), PullRequestRef("owner", "repo", True),
])
def test_invalid_targets_never_launch_a_subprocess(ref):
    runner = Mock()
    with pytest.raises(GitHubError):
        client_module().ReviewClient(runner=runner).create_review(ref, payload())
    runner.assert_not_called()


@pytest.mark.parametrize("status, headers, expected, delay", [
    (400, {}, "PostingRejected", None), (401, {}, "PostingRejected", None),
    (403, {}, "PostingRejected", None), (404, {}, "PostingRejected", None),
    (422, {}, "PostingRejected", None), (429, {}, "PostingRateLimited", 60),
    (429, {"Retry-After": "90"}, "PostingRateLimited", 90),
    (403, {"Retry-After": "10"}, "PostingRateLimited", 10),
    (403, {"X-RateLimit-Remaining": "0"}, "PostingRateLimited", 60),
    (500, {}, "PostingUncertain", None), (502, {}, "PostingUncertain", None),
    (503, {}, "PostingUncertain", None),
])
def test_status_classification_is_safe_and_never_retries_writes(status, headers, expected, delay):
    mod = client_module()
    runner = Mock(return_value=response(
        status, {"message": "secret ghp_sensitive"}, headers=headers,
        suffix="\ngh: secret ghp_sensitive (HTTP 404)", newline="\n",
    ))
    sleeper = Mock()
    with pytest.raises(getattr(mod, expected)) as caught:
        mod.ReviewClient(runner=runner, sleeper=sleeper).create_review(REF, payload())
    error = caught.value
    assert "secret" not in str(error)
    assert "ghp_sensitive" not in str(error)
    assert "ghp_sensitive" not in repr(vars(error))
    if expected != "PostingUncertain":
        assert error.status == status
        assert error.unsent is False
    if delay is not None:
        assert error.retry_after == delay
    runner.assert_called_once()
    sleeper.assert_not_called()


@pytest.mark.parametrize("failure, expected, unsent", [
    (FileNotFoundError("secret"), "PostingRejected", True),
    (PermissionError("secret"), "PostingRejected", True),
    (OSError("secret"), "PostingUncertain", None),
    (subprocess.TimeoutExpired(["gh", "secret"], 60, stderr=b"secret"),
     "PostingUncertain", None),
    (subprocess.CalledProcessError(1, ["gh", "secret"], stderr=b"secret"),
     "PostingUncertain", None),
])
def test_launch_and_uncertain_failures_are_safe(failure, expected, unsent):
    mod = client_module()
    runner = Mock(side_effect=failure)
    with pytest.raises(getattr(mod, expected)) as caught:
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    assert "secret" not in str(caught.value)
    assert "secret" not in repr(vars(caught.value))
    assert caught.value.__suppress_context__
    if unsent is not None:
        assert caught.value.unsent is unsent
        assert caught.value.status is None
    runner.assert_called_once()


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess([], 1, b"", b"gh: secret (HTTP 404)"),
    subprocess.CompletedProcess([], 1, b"", b"diagnostic\nHTTP/2.0 404 Nope\n\n{}"),
    subprocess.CompletedProcess([], 0, b'HTTP/2.0 nope\n\n{}', b""),
    subprocess.CompletedProcess([], 0, b'HTTP/2.0 200 OK\n\nsecret\xff', b""),
    subprocess.CompletedProcess([], 0, b'HTTP/2.0 200 OK\n\nsecret invalid json', b""),
    subprocess.CompletedProcess([], 0, b'HTTP/2.0 200 OK\nBad header\n\n{}', b""),
    subprocess.CompletedProcess([], 0, b'HTTP/2.0 200 OK\n\n{}\nsecret suffix', b""),
    subprocess.CompletedProcess([], 0, json.dumps(receipt()).encode(), b""),
    subprocess.CompletedProcess([], 1, framed(200, receipt()), b""),
    subprocess.CompletedProcess([], 0, framed(403, {}), b""),
    subprocess.CompletedProcess([], 0, framed(200, receipt()), framed(404, {})),
    subprocess.CompletedProcess([], 1, framed(200, receipt()), framed(403, {})),
])
def test_missing_malformed_or_contradictory_framing_is_uncertain(result):
    mod = client_module()
    runner = Mock(return_value=result)
    with pytest.raises(mod.PostingUncertain) as caught:
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    assert "secret" not in str(caught.value)
    runner.assert_called_once()


@pytest.mark.parametrize("changes", [
    {"id": 0}, {"id": True}, {"id": "123"},
    {"html_url": "https://evil.example/secret"},
    {"html_url": "https://github.com@evil.example/secret"},
    {"html_url": "https://github.com/owner/repo/pull/13#pullrequestreview-123"},
    {"html_url": "https://github.com/owner/repo/pull/12#pullrequestreview-999"},
    {"html_url": "https://github.com/owner/repo/pull/12#pullrequestreview-123?secret"},
    {"commit_id": "b" * 40}, {"commit_id": None},
    {"body": "secret wrong body"}, {"body": None},
    {"state": "APPROVED"}, {"state": None},
    {"user": None}, {"user": {}}, {"user": {"login": "secret\ninvalid"}},
])
def test_malformed_or_mismatched_receipt_is_uncertain(changes):
    mod = client_module()
    runner = Mock(return_value=response(value=receipt(**changes)))
    with pytest.raises(mod.PostingUncertain) as caught:
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    assert "secret" not in str(caught.value)
    runner.assert_called_once()


@pytest.mark.parametrize("event, state", [
    ("APPROVE", "APPROVED"), ("REQUEST_CHANGES", "CHANGES_REQUESTED"),
    ("COMMENT", "COMMENTED"),
])
@pytest.mark.parametrize("version, newline", [("HTTP/1.1", "\r\n"), ("HTTP/2.0", "\n")])
def test_receipt_confirms_each_event_and_header_framing(event, state, version, newline):
    content = framed(200, receipt(state=state), newline=newline).replace(
        b"HTTP/2.0", version.encode()
    )
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, content, b""))
    review = client_module().ReviewClient(runner=runner).create_review(REF, payload(event))
    assert review.state == state


@pytest.mark.parametrize("headers, expected", [
    ({"Retry-After": "90", "X-RateLimit-Reset": "1120"}, 120),
    ({"Retry-After": "Sat, 03 Oct 2026 16:00:00 GMT"}, 1791042200),
    ({"Retry-After": "999999999999999"}, 999999999999999),
    ({"Retry-After": "nan"}, 60), ({"Retry-After": "-1"}, 60),
    ({"Retry-After": "Infinity"}, 60), ({"Retry-After": "secret"}, 60),
    ({"X-RateLimit-Reset": "nan", "X-RateLimit-Remaining": "0"}, 60),
])
def test_rate_limit_delay_respects_safe_server_timing(monkeypatch, headers, expected):
    mod = client_module()
    monkeypatch.setattr(mod.time, "time", lambda: 1000)
    runner = Mock(return_value=response(429, {}, headers=headers))
    with pytest.raises(mod.PostingRateLimited) as caught:
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    assert caught.value.retry_after == expected


def test_structured_failure_on_stdout_is_supported():
    mod = client_module()
    runner = Mock(return_value=subprocess.CompletedProcess([], 1, framed(422, {}), b"gh: secret"))
    with pytest.raises(mod.PostingRejected) as caught:
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    assert caught.value.status == 422
    assert "secret" not in str(caught.value)


def metadata(**changes):
    return {
        "number": 12, "state": "open", "merged": False, "changed_files": 1,
        "head": {"sha": SHA},
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
        **changes,
    }


def unframed(value):
    return subprocess.CompletedProcess([], 0, json.dumps(value).encode(), b"")


@pytest.mark.parametrize("method, value, expected, args", [
    ("get_pr", metadata(), metadata(), ["gh", "api", "repos/owner/repo/pulls/12"]),
    ("get_reviews", [[{"id": 1}], [], [{"id": 2}]], [{"id": 1}, {"id": 2}],
     ["gh", "api", "repos/owner/repo/pulls/12/reviews", "--paginate", "--slurp"]),
])
def test_reads_reuse_metadata_and_pagination_and_retry_transient_failures(
    method, value, expected, args,
):
    runner = Mock(side_effect=[OSError("secret"), unframed(value)])
    delays = []
    client = client_module().ReviewClient(runner=runner, sleeper=delays.append)
    assert getattr(client, method)(REF) == expected
    assert runner.call_count == 2
    assert runner.call_args.args[0] == args
    assert delays == [1.0]


@pytest.mark.parametrize("method, response", [
    ("get_pr", unframed({})), ("get_pr", unframed(metadata(number=True))),
    ("get_pr", subprocess.CompletedProcess([], 0, b"secret\xff", b"")),
    ("get_reviews", unframed({})), ("get_reviews", unframed([{}])),
    ("get_reviews", unframed([[1]])),
    ("get_reviews", subprocess.CompletedProcess([], 1, b"", b"secret ghp_sensitive")),
])
def test_reads_bound_failures_and_preserve_existing_validation(method, response):
    runner = Mock(return_value=response)
    delays = []
    with pytest.raises(client_module().PostingError) as caught:
        getattr(client_module().ReviewClient(runner=runner, sleeper=delays.append), method)(REF)
    assert runner.call_count == 3
    assert delays == [1.0, 2.0]
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("method", ["get_pr", "get_reviews"])
def test_read_invalid_target_never_launches_or_retries(method):
    runner = Mock()
    with pytest.raises(GitHubError):
        getattr(client_module().ReviewClient(runner=runner), method)(
            PullRequestRef("secret;pwd", "repo", 12)
        )
    runner.assert_not_called()


def test_read_closed_pr_metadata_is_left_for_lifecycle_validation():
    runner = Mock(return_value=unframed(metadata(state="closed")))
    assert client_module().ReviewClient(runner=runner).get_pr(REF)["state"] == "closed"
    runner.assert_called_once()


@pytest.mark.parametrize("login", ["reviewer", "review-user", "github-actions[bot]"])
def test_login_is_validated_using_framed_get(login, monkeypatch):
    monkeypatch.setenv("GH_HOST", "evil.example")
    monkeypatch.setenv("GH_TOKEN", "test-auth")
    runner = Mock(return_value=response(value={"login": login}))
    assert client_module().ReviewClient(runner=runner).get_login() == login
    assert runner.call_args.args[0] == [
        "gh", "api", "--method", "GET", "user", "--include",
        "--header", "Accept: application/vnd.github+json",
        "--header", "X-GitHub-Api-Version: 2022-11-28",
    ]
    options = runner.call_args.kwargs
    assert "input" not in options
    assert options["shell"] is False
    assert options["capture_output"] is True
    assert options["text"] is False
    assert options["timeout"] == 60
    assert options["env"]["GH_HOST"] == "github.com"
    assert options["env"]["GH_TOKEN"] == "test-auth"


@pytest.mark.parametrize("value", [
    {}, [], {"login": None}, {"login": ""}, {"login": "secret\ninvalid"},
    {"login": "-secret"}, {"login": "secret--user"}, {"login": "x" * 45},
])
def test_invalid_login_is_safe_after_bounded_reads(value):
    runner = Mock(return_value=response(value=value))
    delays = []
    with pytest.raises(client_module().PostingError) as caught:
        client_module().ReviewClient(runner=runner, sleeper=delays.append).get_login()
    assert runner.call_count == 3
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("failure", [
    FileNotFoundError("secret"), PermissionError("secret"),
    subprocess.TimeoutExpired(["gh", "secret"], 60),
    response(403, {"message": "secret"}, suffix="\ngh: secret (HTTP 403)"),
    subprocess.CompletedProcess([], 0, b"secret\xff", b""),
])
def test_login_errors_hide_secrets_and_bound_read_attempts(failure):
    runner = (
        Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    )
    with pytest.raises(client_module().PostingError) as caught:
        client_module().ReviewClient(runner=runner, sleeper=lambda _: None).get_login()
    assert runner.call_count == 3
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_public_poster_exports_transport_and_errors():
    mod = importlib.import_module("scrutare.poster")
    for name in ("ReviewClient", "PostedReview", "PostingRejected", "PostingRateLimited",
                 "PostingUncertain"):
        assert getattr(mod, name) is getattr(client_module(), name)


def test_unrepresentable_server_delay_cannot_allow_a_short_write_retry():
    mod = client_module()
    runner = Mock(return_value=response(429, {}, headers={"Retry-After": "9" * 400}))
    with pytest.raises(mod.PostingUncertain):
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    runner.assert_called_once()


@pytest.mark.parametrize("delay", [float("nan"), float("inf"), -1, True])
def test_public_rate_limit_error_cannot_carry_unsafe_timing(delay):
    mod = client_module()
    with pytest.raises(mod.PostingError):
        mod.PostingRateLimited(status=429, retry_after=delay)


@pytest.mark.parametrize("delay, count, sleeps", [(10, 3, [10, 10]), (90, 1, [])])
def test_login_reads_respect_throttling_delays(delay, count, sleeps):
    mod = client_module()
    runner = Mock(return_value=response(429, {}, headers={"Retry-After": str(delay)}))
    observed = []
    with pytest.raises(mod.PostingError):
        mod.ReviewClient(runner=runner, sleeper=observed.append).get_login()
    assert runner.call_count == count
    assert observed == sleeps


def test_reads_can_succeed_on_the_third_and_final_attempt():
    runner = Mock(side_effect=[OSError(), OSError(), unframed(metadata())])
    delays = []
    client = client_module().ReviewClient(runner=runner, sleeper=delays.append)
    assert client.get_pr(REF) == metadata()
    assert runner.call_count == 3
    assert delays == [1.0, 2.0]


def test_invalid_non_bot_username_length_is_not_accepted():
    runner = Mock(return_value=response(value={"login": "x" * 40}))
    with pytest.raises(client_module().PostingError):
        client_module().ReviewClient(runner=runner, sleeper=lambda _: None).get_login()


def test_login_rate_limit_exposes_server_delay_when_reads_stop():
    mod = client_module()
    runner = Mock(return_value=response(429, {}, headers={"Retry-After": "90"}))
    with pytest.raises(mod.PostingRateLimited) as caught:
        mod.ReviewClient(runner=runner, sleeper=lambda _: None).get_login()
    assert caught.value.retry_after == 90
    assert caught.value.status == 429


def test_malformed_timing_does_not_reduce_known_server_delay(monkeypatch):
    mod = client_module()
    monkeypatch.setattr(mod.time, "time", lambda: 1000)
    runner = Mock(return_value=response(
        429, {}, headers={"Retry-After": "secret malformed", "X-RateLimit-Reset": "1120"},
    ))
    with pytest.raises(mod.PostingRateLimited) as caught:
        mod.ReviewClient(runner=runner).create_review(REF, payload())
    assert caught.value.retry_after == 120


def test_receipt_accepts_github_canonical_repository_casing():
    runner = Mock(return_value=response(value=receipt(
        html_url="https://github.com/Owner/Repo/pull/12#pullrequestreview-123",
    )))
    review = client_module().ReviewClient(runner=runner).create_review(REF, payload())
    assert review.review_id == 123
    assert review.html_url == "https://github.com/Owner/Repo/pull/12#pullrequestreview-123"
