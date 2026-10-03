"""Exercise reference validation and the real transport at its subprocess boundary."""

import importlib
import json
import subprocess
from unittest.mock import Mock

import pytest


def github():
    return importlib.import_module("scrutare.engine.github")


def metadata(**changes):
    return {
        "number": 12,
        "state": "open",
        "merged": False,
        "head": {"sha": "abc123"},
        "body": "Prior context",
        **changes,
    }


@pytest.mark.parametrize(
    "value, repository",
    [
        ("https://github.com/owner/repo/pull/12", None),
        ("12", "owner/repo"),
    ],
)
def test_resolve_reference(value, repository):
    mod = github()
    assert mod.resolve_pr(value, repository) == mod.PullRequestRef("owner", "repo", 12)


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "",
        "1.2",
        "+12",
        "https://example.com/owner/repo/pull/12",
        "http://github.com/owner/repo/pull/12",
        "https://github.com/owner/repo/issues/12",
        "https://github.com/owner/repo/pull/0",
        "https://github.com/owner/repo/pull/-1",
        "https://github.com/owner/repo/pull/12?x=1",
        "owner/repo#12",
        "https://github.com/owner/repo/pull/12/extra",
    ],
)
def test_reject_invalid_reference(value):
    mod = github()
    with pytest.raises(mod.GitHubError):
        mod.resolve_pr(value, "owner/repo")


@pytest.mark.parametrize(
    "repository", ["owner", "owner/repo/extra", "-bad/repo", "owner/..", "owner/repo;pwd", ""]
)
def test_reject_invalid_repository_context(repository):
    mod = github()
    with pytest.raises(mod.GitHubError):
        mod.resolve_pr("12", repository)


def test_number_uses_current_repository(monkeypatch):
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, '{"nameWithOwner":"owner/repo"}', "")
    )
    monkeypatch.setattr(subprocess, "run", runner)
    mod = github()
    assert mod.resolve_pr("12") == mod.PullRequestRef("owner", "repo", 12)
    args, kwargs = runner.call_args
    assert args[0] == ["gh", "repo", "view", "--json", "nameWithOwner"]
    assert kwargs["shell"] is False
    assert 0 < kwargs["timeout"] <= 120


@pytest.mark.parametrize("payload", ["no json", "[]", "{}", '{"nameWithOwner":"bad"}'])
def test_invalid_current_repository_response(monkeypatch, payload):
    monkeypatch.setattr(
        subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, payload, ""))
    )
    mod = github()
    with pytest.raises(mod.GitHubError):
        mod.resolve_pr("12")


def test_pr_preserves_github_fields():
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(metadata()), ""))
    result = mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))
    assert result == metadata()
    assert runner.call_args.args[0] == ["gh", "api", "repos/owner/repo/pulls/12"]
    assert runner.call_args.kwargs["shell"] is False
    assert runner.call_args.kwargs["capture_output"] is True
    assert runner.call_args.kwargs["text"] is True
    assert 0 < runner.call_args.kwargs["timeout"] <= 120


def test_diff_uses_exact_accept_header():
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, "diff --git a/x b/x\n", ""))
    assert (
        mod.GitHubClient(runner=runner).get_diff(mod.PullRequestRef("owner", "repo", 12))
        == "diff --git a/x b/x\n"
    )
    assert runner.call_args.args[0] == [
        "gh",
        "api",
        "repos/owner/repo/pulls/12",
        "--header",
        "Accept: application/vnd.github.v3.diff",
    ]


@pytest.mark.parametrize(
    "method, endpoint",
    [
        ("get_files", "pulls/12/files"),
        ("get_reviews", "pulls/12/reviews"),
        ("get_comments", "issues/12/comments"),
        ("get_review_comments", "pulls/12/comments"),
    ],
)
def test_paginated_lists_preserve_discussion(method, endpoint):
    mod = github()
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, '[[{"id":1,"body":"first"}],[],[{"id":2,"body":"second"}]]', ""
        )
    )
    result = getattr(mod.GitHubClient(runner=runner), method)(
        mod.PullRequestRef("owner", "repo", 12)
    )
    assert result == [{"id": 1, "body": "first"}, {"id": 2, "body": "second"}]
    assert runner.call_args.args[0] == [
        "gh",
        "api",
        f"repos/owner/repo/{endpoint}",
        "--paginate",
        "--slurp",
    ]


@pytest.mark.parametrize("payload", ["invalid", "{}", '[{"id":1}]', "[[1]]", "[null]"])
def test_invalid_paginated_payload(payload):
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, payload, ""))
    with pytest.raises(mod.GitHubError):
        mod.GitHubClient(runner=runner).get_files(mod.PullRequestRef("owner", "repo", 12))


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        metadata(number=0),
        metadata(number=True),
        metadata(state="unknown"),
        metadata(merged="false"),
        metadata(head={}),
        metadata(head={"sha": ""}),
    ],
)
def test_malformed_metadata(payload):
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
    with pytest.raises(mod.GitHubError):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))


@pytest.mark.parametrize("changes", [{"state": "closed"}, {"merged": True}])
def test_closed_or_merged_pr_is_unavailable(changes):
    mod = github()
    with pytest.raises(mod.PullRequestUnavailable):
        mod.assert_pr_open(metadata(**changes))


def test_open_pr_is_available():
    github().assert_pr_open(metadata())


@pytest.mark.parametrize(
    "failure, hint",
    [
        (FileNotFoundError("secret ghp_sensitive"), "install"),
        (subprocess.TimeoutExpired(["gh"], 60, stderr="secret ghp_sensitive"), "timed out"),
        (OSError("secret ghp_sensitive"), "execute"),
        (
            subprocess.CompletedProcess([], 1, "secret ghp_sensitive", "secret ghp_sensitive"),
            "auth",
        ),
    ],
)
def test_subprocess_errors_are_actionable_and_hide_secrets(failure, hint):
    mod = github()
    runner = (
        Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    )
    with pytest.raises(mod.GitHubError) as caught:
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))
    assert hint in str(caught.value).lower()
    assert "secret" not in str(caught.value)
    assert "ghp_sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("state", [[], {}])
def test_structured_state_is_reported_as_malformed(state):
    mod = github()
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(metadata(state=state)), "")
    )
    with pytest.raises(mod.GitHubError):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))


def test_invalid_metadata_json_is_reported_safely():
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, "secret malformed response", ""))
    with pytest.raises(mod.GitHubError, match="invalid JSON") as caught:
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "owner, repo, number",
    [("owner;pwd", "repo", 12), ("owner", "..", 12), ("owner", "repo", 0), ("owner", "repo", True)],
)
def test_direct_reference_is_validated_before_transport(owner, repo, number):
    mod = github()
    runner = Mock()
    with pytest.raises(mod.GitHubError):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef(owner, repo, number))
    runner.assert_not_called()


def test_issue_comments_endpoint_preserves_owner_and_repository_named_pulls():
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, "[[]]", ""))
    assert (
        mod.GitHubClient(runner=runner).get_comments(mod.PullRequestRef("pulls", "pulls", 12)) == []
    )
    assert runner.call_args.args[0][2] == "repos/pulls/pulls/issues/12/comments"
