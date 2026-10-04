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
        "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
        "changed_files": 1,
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
        return_value=subprocess.CompletedProcess([], 0, b'{"nameWithOwner":"owner/repo"}', b"")
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
        subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, payload.encode("utf-8"), b"")),
    )
    mod = github()
    with pytest.raises(mod.GitHubError):
        mod.resolve_pr("12")


def test_pr_preserves_github_fields():
    mod = github()
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(metadata()).encode("utf-8"), b"")
    )
    result = mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))
    assert result == metadata()
    assert runner.call_args.args[0] == ["gh", "api", "repos/owner/repo/pulls/12"]
    assert runner.call_args.kwargs["shell"] is False
    assert runner.call_args.kwargs["capture_output"] is True
    assert runner.call_args.kwargs["text"] is False
    assert 0 < runner.call_args.kwargs["timeout"] <= 120


def test_diff_uses_exact_accept_header():
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, b"diff --git a/x b/x\n", b""))
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
            [], 0, b'[[{"id":1,"body":"first"}],[],[{"id":2,"body":"second"}]]', b""
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
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, payload.encode("utf-8"), b""))
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
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode("utf-8"), b"")
    )
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
            subprocess.CompletedProcess([], 1, b"secret ghp_sensitive", b"secret ghp_sensitive"),
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
        return_value=subprocess.CompletedProcess(
            [], 0, json.dumps(metadata(state=state)).encode("utf-8"), b""
        )
    )
    with pytest.raises(mod.GitHubError):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))


def test_invalid_metadata_json_is_reported_safely():
    mod = github()
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, b"secret malformed response", b"")
    )
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
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, b"[[]]", b""))
    assert (
        mod.GitHubClient(runner=runner).get_comments(mod.PullRequestRef("pulls", "pulls", 12)) == []
    )
    assert runner.call_args.args[0][2] == "repos/pulls/pulls/issues/12/comments"


@pytest.mark.parametrize(
    "base",
    [
        None,
        {},
        {"ref": None, "sha": "base123", "repo": {"full_name": "owner/repo"}},
        {"ref": " ", "sha": "base123", "repo": {"full_name": "owner/repo"}},
        {"ref": "main", "sha": None, "repo": {"full_name": "owner/repo"}},
        {"ref": "main", "sha": " ", "repo": {"full_name": "owner/repo"}},
        {"ref": "main", "sha": "base123", "repo": None},
        {"ref": "main", "sha": "base123", "repo": {}},
        {"ref": "main", "sha": "base123", "repo": {"full_name": "secret invalid"}},
        {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/.."}},
    ],
)
def test_malformed_base_metadata_is_reported_safely(base):
    mod = github()
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, json.dumps(metadata(base=base)).encode("utf-8"), b""
        )
    )
    with pytest.raises(mod.GitHubError, match="malformed PR metadata") as caught:
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))
    assert "secret" not in str(caught.value)


def test_missing_base_metadata_is_reported_safely():
    mod = github()
    payload = metadata()
    del payload["base"]
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode("utf-8"), b"")
    )
    with pytest.raises(mod.GitHubError, match="malformed PR metadata"):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))


def test_real_subprocess_preserves_diff_crlf(fake_gh):
    mod = github()
    diff = mod.GitHubClient().get_diff(mod.PullRequestRef("owner", "repo", 12))
    assert diff.encode("utf-8") == fake_gh


def test_invalid_utf8_response_is_reported_safely():
    mod = github()
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, b"secret ghp_sensitive\xff", b""))
    with pytest.raises(mod.GitHubError, match="UTF-8") as caught:
        mod.GitHubClient(runner=runner).get_diff(mod.PullRequestRef("owner", "repo", 12))
    assert "secret" not in str(caught.value)
    assert "ghp_sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("changed_files", [None, -1, True, False, "1", 1.5])
def test_malformed_changed_files_count_is_reported_safely(changed_files):
    mod = github()
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, json.dumps(metadata(changed_files=changed_files)).encode("utf-8"), b""
        )
    )
    with pytest.raises(mod.GitHubError, match="malformed PR metadata"):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))


def test_missing_changed_files_count_is_reported_safely():
    mod = github()
    payload = metadata()
    del payload["changed_files"]
    runner = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b"")
    )
    with pytest.raises(mod.GitHubError, match="malformed PR metadata"):
        mod.GitHubClient(runner=runner).get_pr(mod.PullRequestRef("owner", "repo", 12))


@pytest.mark.parametrize(
    "value, repository",
    [("https://github.com/owner/repo/pull/12", None), ("12", None), ("12", "owner/repo")],
)
def test_all_reads_pin_github_com_despite_parent_host(monkeypatch, value, repository):
    import os

    monkeypatch.setenv("GH_HOST", "alternate.example.com")
    monkeypatch.setenv("GH_TOKEN", "test-auth-preserved")

    def run(args, **kwargs):
        child_env = kwargs.get("env", os.environ)
        assert child_env["GH_HOST"] == "github.com"
        assert child_env["GH_TOKEN"] == "test-auth-preserved"
        if args[1] == "repo":
            output = b'{"nameWithOwner":"owner/repo"}'
        elif "--header" in args:
            output = b"diff --git a/x b/x\n"
        elif "--paginate" in args:
            output = b"[[]]"
        else:
            output = json.dumps(metadata()).encode("utf-8")
        return subprocess.CompletedProcess(args, 0, output, b"")

    monkeypatch.setattr(subprocess, "run", run)
    mod = github()
    ref = mod.resolve_pr(value, repository)
    client = mod.GitHubClient()
    assert client.get_pr(ref) == metadata()
    assert client.get_diff(ref) == "diff --git a/x b/x\n"
    for method in (
        client.get_files,
        client.get_reviews,
        client.get_comments,
        client.get_review_comments,
    ):
        assert method(ref) == []
    assert os.environ["GH_HOST"] == "alternate.example.com"
