"""Reviewer request protocol tests with network only replaced by an injected runner."""

import importlib
import json
import subprocess
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from scrutare.engine.github import GitHubError, PullRequestRef
from scrutare.poster import PostingError, PostingRateLimited, PostingRejected, PostingUncertain

REF = PullRequestRef("owner", "repo", 12)
TARGETS = ("Alice", "service-bot")


def transport():
    return importlib.import_module("scrutare.poster.client")


def receipt_type():
    return importlib.import_module("scrutare.poster.reviewers").ReviewerRequestReceipt


def pr_response(**changes):
    return {
        "number": 12,
        "html_url": "https://github.com/owner/repo/pull/12",
        "head": {"sha": "b" * 40},
        "base": {"repo": {"full_name": "owner/repo"}},
        "requested_reviewers": [{"login": "alice"}, {"login": "service-bot"}],
        **changes,
    }


def response(status=201, value=None, *, headers=None, suffix="", failed=None):
    lines = [f"HTTP/2.0 {status} Response"]
    lines.extend(f"{key}: {value}" for key, value in (headers or {}).items())
    body = pr_response() if value is None else value
    content = ("\r\n".join(lines) + "\r\n\r\n" + json.dumps(body) + suffix).encode()
    code = int(not 200 <= status < 300) if failed is None else int(failed)
    return subprocess.CompletedProcess([], code, b"" if code else content, content if code else b"")


def test_request_reviewers_uses_exact_one_attempt201_pr_shape(monkeypatch):
    monkeypatch.setenv("GH_HOST", "evil.example")
    monkeypatch.setenv("GH_TOKEN", "test-auth")
    runner = Mock(return_value=response())
    sleeper = Mock()
    result = transport().ReviewClient(runner=runner, sleeper=sleeper).request_reviewers(
        REF, ("Alice", "alice", "service-bot"),
    )
    assert result.reviewers == TARGETS
    assert result.provenance == "post_response"
    assert isinstance(result, receipt_type())
    runner.assert_called_once()
    sleeper.assert_not_called()
    assert runner.call_args.args[0] == [
        "gh", "api", "--method", "POST", "repos/owner/repo/pulls/12/requested_reviewers",
        "--include", "--input", "-",
        "--header", "Accept: application/vnd.github+json",
        "--header", "X-GitHub-Api-Version: 2022-11-28",
        "--header", "Content-Type: application/json",
    ]
    options = runner.call_args.kwargs
    assert json.loads(options["input"]) == {"reviewers": ["Alice", "service-bot"]}
    assert isinstance(options["input"], bytes)
    assert options["shell"] is False
    assert options["capture_output"] is True
    assert options["text"] is False
    assert options["timeout"] == 60
    assert options["env"]["GH_HOST"] == "github.com"
    assert options["env"]["GH_TOKEN"] == "test-auth"


@pytest.mark.parametrize("reviewers", [
    ["alice"], "alice", None, (None,), (True,), ("@alice",), ("org/team",),
    ("alice[bot]",), ("a--b",), ("x" * 40,), ("secret\ninvalid",),
    ("alice", "secret;pwd"),
])
def test_invalid_targets_before_transport(reviewers):
    runner = Mock()
    with pytest.raises(PostingError) as caught:
        transport().ReviewClient(runner=runner).request_reviewers(REF, reviewers)
    assert "github.human_reviewers" in str(caught.value)
    assert "secret" not in str(caught.value)
    runner.assert_not_called()


@pytest.mark.parametrize("method, args", [
    ("request_reviewers", (TARGETS,)), ("request_reviewers", ((),)),
    ("get_requested_reviewers", ()),
])
@pytest.mark.parametrize("ref", [
    PullRequestRef("secret;pwd", "repo", 12), PullRequestRef("owner", "..", 12),
    PullRequestRef("owner", "repo", 0), PullRequestRef("owner", "repo", True), None,
])
def test_invalid_pr_target_before_transport(method, args, ref):
    runner = Mock()
    with pytest.raises(GitHubError):
        getattr(transport().ReviewClient(runner=runner), method)(ref, *args)
    runner.assert_not_called()


def test_no_targets_receipt_performs_no_request_or_identity_read():
    runner = Mock()
    result = transport().ReviewClient(runner=runner).request_reviewers(REF, ())
    assert result.reviewers == ()
    assert result.provenance == "no_targets"
    runner.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"number": 13}, {"number": True}, {"number": "12"}, {"number": None},
    {"html_url": "https://github.com/other/repo/pull/12"},
    {"html_url": "https://github.com/owner/other/pull/12"},
    {"html_url": "https://github.com/owner/repo/pull/13"},
    {"html_url": "https://github.com@evil.example/owner/repo/pull/12"},
    {"html_url": "https://github.com/owner/repo/pull/12?secret"}, {"html_url": None},
    {"requested_reviewers": []}, {"requested_reviewers": [{"login": "alice"}]},
    {"requested_reviewers": None}, {"requested_reviewers": {}},
    {"requested_reviewers": [None]}, {"requested_reviewers": [{}]},
    {"requested_reviewers": [{"login": "alice"}, {"login": "service-bot"},
                             {"login": "secret\ninvalid"}]},
])
def test_wrong_pr_missing_targets_or_malformed_receipt_uncertain(changes):
    runner = Mock(return_value=response(value=pr_response(**changes)))
    with pytest.raises(PostingUncertain) as caught:
        transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    assert "secret" not in str(caught.value)
    runner.assert_called_once()


@pytest.mark.parametrize("status, value", [
    (200, pr_response()), (202, pr_response()), (204, pr_response()),
    (201, []), (201, {}),
    (201, {"users": [{"login": "alice"}, {"login": "service-bot"}], "teams": []}),
])
def test_invalid_success_status_or_get_shaped_post_response_is_uncertain(status, value):
    runner = Mock(return_value=response(status, value))
    with pytest.raises(PostingUncertain):
        transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    runner.assert_called_once()


def test_additional_users_and_force_push_allowed():
    runner = Mock(return_value=response(value=pr_response(
        html_url="https://github.com/Owner/Repo/pull/12",
        head={"sha": "c" * 40},
        requested_reviewers=[{"login": "ALICE"}, {"login": "service-bot"},
                             {"login": "extra-human"}, {"login": "github-actions[bot]"}],
    )))
    result = transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    assert result.reviewers == TARGETS
    assert result.provenance == "post_response"
    runner.assert_called_once()


@pytest.mark.parametrize("base", [
    {"repo": {"full_name": "other/repo"}}, {"repo": {"full_name": "owner/other"}},
    None, {}, {"repo": None}, {"repo": {}}, {"repo": {"full_name": None}},
    {"repo": {"full_name": "secret;pwd/repo"}},
])
def test_present_base_identity_cannot_contradict_matching_pr_url(base):
    runner = Mock(return_value=response(value=pr_response(base=base)))
    with pytest.raises(PostingUncertain) as caught:
        transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    assert "secret" not in str(caught.value)
    runner.assert_called_once()


@pytest.mark.parametrize("include_base", [True, False])
def test_base_identity_is_optional_and_accepts_canonical_repository_casing(include_base):
    value = pr_response(base={"repo": {"full_name": "Owner/Repo"}})
    if not include_base:
        del value["base"]
    runner = Mock(return_value=response(value=value))
    result = transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    assert result.reviewers == TARGETS
    runner.assert_called_once()


@pytest.mark.parametrize("status, headers, value, expected, delay", [
    (400, {}, {}, PostingRejected, None), (401, {}, {}, PostingRejected, None),
    (403, {}, {"message": "secret forbidden"}, PostingRejected, None),
    (404, {}, {}, PostingRejected, None), (422, {}, {}, PostingRejected, None),
    (429, {}, {}, PostingRateLimited, 60),
    (429, {"Retry-After": "90"}, {}, PostingRateLimited, 90),
    (403, {"Retry-After": "10"}, {}, PostingRateLimited, 10),
    (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1120"}, {},
     PostingRateLimited, 120),
    (403, {}, {"message": "You have exceeded a secondary rate limit. secret"},
     PostingRateLimited, 60),
    (429, {"Retry-After": "invalid"}, {}, PostingRateLimited, 60),
    (500, {}, {}, PostingUncertain, None), (502, {}, {}, PostingUncertain, None),
    (503, {}, {}, PostingUncertain, None),
])
def test_post_classifications_never_retry_or_expose_remote_diagnostics(
    monkeypatch, status, headers, value, expected, delay,
):
    monkeypatch.setattr(transport().time, "time", lambda: 1000)
    runner = Mock(return_value=response(
        status, value, headers=headers, suffix="\ngh: secret ghp_sensitive",
    ))
    sleeper = Mock()
    with pytest.raises(expected) as caught:
        transport().ReviewClient(runner=runner, sleeper=sleeper).request_reviewers(REF, TARGETS)
    assert type(caught.value) is expected
    if isinstance(caught.value, PostingRejected):
        assert caught.value.status == status
        assert caught.value.unsent is False
    if delay is not None:
        assert caught.value.retry_after == delay
    assert "secret" not in str(caught.value)
    assert "ghp_sensitive" not in repr(vars(caught.value))
    runner.assert_called_once()
    sleeper.assert_not_called()


@pytest.mark.parametrize("failure, expected, unsent", [
    (FileNotFoundError("secret"), PostingRejected, True),
    (PermissionError("secret"), PostingRejected, True),
    (OSError("secret"), PostingUncertain, None),
    (subprocess.TimeoutExpired(["gh", "secret"], 60), PostingUncertain, None),
    (subprocess.SubprocessError("secret"), PostingUncertain, None),
])
def test_launch_failure_and_timeout_preserve_uncertainty(failure, expected, unsent):
    runner = Mock(side_effect=failure)
    with pytest.raises(expected) as caught:
        transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    if unsent is not None:
        assert caught.value.unsent is unsent
        assert caught.value.status is None
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__
    runner.assert_called_once()


@pytest.mark.parametrize("result", [
    subprocess.CompletedProcess([], 0, b"secret invalid JSON", b""),
    subprocess.CompletedProcess([], 0, b"HTTP/2.0 201 Created\n\nsecret\xff", b""),
    subprocess.CompletedProcess([], 1, b"", b"gh: secret (HTTP 422)"),
    response(failed=True), response(422, {}, failed=False),
    subprocess.CompletedProcess([], 0, b"HTTP/2.0 201 OK\nBad header\n\n{}", b""),
    response(suffix="secret suffix"),
    subprocess.CompletedProcess([], 0, response().stdout, response(422, {}).stderr),
])
def test_invalid_framing_or_process_ambiguity_is_uncertain(result):
    runner = Mock(return_value=result)
    with pytest.raises(PostingUncertain) as caught:
        transport().ReviewClient(runner=runner).request_reviewers(REF, TARGETS)
    assert "secret" not in str(caught.value)
    runner.assert_called_once()


def test_get_requested_reviewers_uses_users_teams_shape(monkeypatch):
    monkeypatch.setenv("GH_HOST", "evil.example")
    monkeypatch.setenv("GH_TOKEN", "test-auth")
    runner = Mock(return_value=response(200, {
        "users": [{"login": "Alice"}, {"login": "alice"}, {"login": "service-bot"},
                  {"login": "extra-human"}, {"login": "github-actions[bot]"}],
        "teams": [{"slug": "security", "id": 1}],
    }))
    result = transport().ReviewClient(runner=runner).get_requested_reviewers(REF)
    assert result == ("Alice", "service-bot", "extra-human")
    runner.assert_called_once()
    assert runner.call_args.args[0] == [
        "gh", "api", "--method", "GET", "repos/owner/repo/pulls/12/requested_reviewers",
        "--include", "--header", "Accept: application/vnd.github+json",
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


def test_get_empty_users_is_observation_without_mutation():
    runner = Mock(return_value=response(200, {"users": [], "teams": []}))
    assert transport().ReviewClient(runner=runner).get_requested_reviewers(REF) == ()
    runner.assert_called_once()
    assert "POST" not in runner.call_args.args[0]


@pytest.mark.parametrize("value", [
    [], {}, pr_response(), {"users": [], "teams": None}, {"users": []},
    {"users": None, "teams": []}, {"users": {}, "teams": []},
    {"users": [None], "teams": []}, {"users": [{}], "teams": []},
    {"users": [{"login": True}], "teams": []},
    {"users": [{"login": "secret\ninvalid"}], "teams": []},
    {"users": [{"login": "x" * 40}], "teams": []},
    {"users": [{"login": "x" * 40 + "[bot]"}], "teams": []},
    {"users": [{"login": "alice"}, {"login": "secret--bad"}], "teams": []},
    {"users": [], "teams": [None]},
])
def test_malformed_get_retries_with_safe_bounded_failure(value):
    runner = Mock(return_value=response(200, value))
    sleeps = []
    with pytest.raises(PostingError) as caught:
        transport().ReviewClient(runner=runner, sleeper=sleeps.append).get_requested_reviewers(REF)
    assert runner.call_count == 3
    assert sleeps == [1.0, 2.0]
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("headers, metadata", [
    ({"Link": '<https://api.github.com/example?page=2>; rel="next"'}, {}),
    ({"Link": '<https://api.github.com/example?page=1>; rel="last"'}, {}),
    ({}, {"next_page": 2}), ({}, {"pagination": {"next": 2}}),
    ({}, {"total_count": 200}),
])
def test_unexpected_pagination_cannot_claim_complete_requested_membership(headers, metadata):
    runner = Mock(return_value=response(200, {
        "users": [{"login": "alice"}], "teams": [], **metadata,
    }, headers=headers))
    with pytest.raises(PostingError):
        transport().ReviewClient(runner=runner, sleeper=lambda _: None).get_requested_reviewers(REF)
    assert runner.call_count == 3


@pytest.mark.parametrize("failure", [
    OSError("secret"), subprocess.TimeoutExpired(["gh", "secret"], 60),
    response(503, {}), subprocess.CompletedProcess([], 0, b"secret\xff", b""),
])
def test_get_transient_failure_can_recover_on_final_bounded_read(failure):
    success = response(200, {"users": [{"login": "alice"}], "teams": []})
    runner = Mock(side_effect=[failure, failure, success])
    sleeps = []
    client = transport().ReviewClient(runner=runner, sleeper=sleeps.append)
    result = client.get_requested_reviewers(REF)
    assert result == ("alice",)
    assert runner.call_count == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize("delay, count, sleeps", [(10, 3, [10, 10]), (90, 1, [])])
def test_get_throttle_respects_existing_read_budget(delay, count, sleeps):
    runner = Mock(return_value=response(429, {}, headers={"Retry-After": str(delay)}))
    observed = []
    client = transport().ReviewClient(runner=runner, sleeper=observed.append)
    with pytest.raises(PostingRateLimited) as caught:
        client.get_requested_reviewers(REF)
    assert caught.value.status == 429
    assert caught.value.retry_after == delay
    assert runner.call_count == count
    assert observed == sleeps


@pytest.mark.parametrize("reviewers, provenance", [
    (TARGETS, "post_response"), (TARGETS, "observed_requested"), ((), "no_targets"),
])
def test_valid_receipt_is_frozen_and_preserves_evidence(reviewers, provenance):
    receipt = receipt_type()(reviewers, provenance)
    assert receipt.reviewers == reviewers
    assert receipt.provenance == provenance
    with pytest.raises(FrozenInstanceError):
        receipt.provenance = "no_targets"


@pytest.mark.parametrize("reviewers, provenance", [
    ((), "post_response"), ((), "observed_requested"), (TARGETS, "no_targets"),
    (TARGETS, "secret wrong"), (TARGETS, None), (TARGETS, ["post_response"]),
    (["alice"], "post_response"), (("alice", "Alice"), "post_response"),
    (("alice[bot]",), "post_response"), (("secret\nbad",), "post_response"),
])
def test_receipt_rejects_invalid_types_provenance_and_unnormalized_targets(reviewers, provenance):
    with pytest.raises(PostingError) as caught:
        receipt_type()(reviewers, provenance)
    assert "secret" not in str(caught.value)


def test_public_receipt_export():
    assert importlib.import_module("scrutare.poster").ReviewerRequestReceipt is receipt_type()
