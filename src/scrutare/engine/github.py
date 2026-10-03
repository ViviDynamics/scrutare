"""Read-only GitHub transport using gh's existing authentication."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[str]]
_TIMEOUT_SECONDS = 60
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_REPO = r"[A-Za-z0-9_.-]+"
_REPOSITORY = re.compile(rf"({_OWNER})/({_REPO})")
_PR_URL = re.compile(rf"https://github\.com/({_OWNER})/({_REPO})/pull/([0-9]+)/?")


class GitHubError(Exception):
    """GitHub input or transport failed with a safe, actionable explanation."""


class PullRequestUnavailable(GitHubError):
    """The pull request has closed or merged and must not be reviewed."""


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int


def _repository(value: str) -> tuple[str, str]:
    match = _REPOSITORY.fullmatch(value)
    if match is None or match[2] in {".", ".."}:
        raise GitHubError("Use a repository in owner/repo form.")
    return match[1], match[2]


def _run(args: list[str], runner: Runner) -> str:
    try:
        result = runner(
            args,
            capture_output=True,
            text=True,
            shell=False,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise GitHubError("Install the GitHub CLI (gh) and run gh auth login.") from None
    except subprocess.TimeoutExpired:
        raise GitHubError("GitHub request timed out; check connectivity and retry.") from None
    except OSError:
        raise GitHubError("Could not execute gh; check the GitHub CLI installation.") from None
    if result.returncode != 0:
        raise GitHubError(
            "GitHub request failed; check gh auth status, repository access, and connectivity."
        ) from None
    return result.stdout


def _json(output: str) -> Any:
    try:
        return json.loads(output)
    except (ValueError, TypeError):
        raise GitHubError("gh returned invalid JSON; check the GitHub CLI and retry.") from None


def resolve_pr(value: str, repository: str | None = None) -> PullRequestRef:
    """Resolve a GitHub PR URL or positive number, inferring the repo via gh if needed."""
    context = _repository(repository) if repository is not None else None
    match = _PR_URL.fullmatch(value)
    if match is not None:
        owner, repo = _repository(f"{match[1]}/{match[2]}")
        number = int(match[3])
    elif re.fullmatch(r"[0-9]+", value):
        number = int(value)
        if number <= 0:
            raise GitHubError("Pull request number must be positive.")
        if context is None:
            result = _json(_run(["gh", "repo", "view", "--json", "nameWithOwner"], subprocess.run))
            if not isinstance(result, dict) or not isinstance(result.get("nameWithOwner"), str):
                raise GitHubError(
                    "gh could not resolve the repository; provide owner/repo explicitly."
                )
            context = _repository(result["nameWithOwner"])
        owner, repo = context
    else:
        raise GitHubError("Use a github.com pull request URL or a positive pull request number.")
    if number <= 0:
        raise GitHubError("Pull request number must be positive.")
    return PullRequestRef(owner, repo, number)


def _validate_metadata(metadata: Any) -> dict[str, Any]:
    if (
        not isinstance(metadata, dict)
        or type(metadata.get("number")) is not int
        or metadata["number"] <= 0
        or metadata.get("state") not in ("open", "closed")
        or type(metadata.get("merged")) is not bool
        or not isinstance(metadata.get("head"), dict)
        or not isinstance(metadata["head"].get("sha"), str)
        or not metadata["head"]["sha"].strip()
    ):
        raise GitHubError("GitHub returned malformed PR metadata; refetch the pull request.")
    return metadata


def assert_pr_open(metadata: dict[str, Any]) -> None:
    """Abort on closed or merged PRs, including state changes during a review."""
    _validate_metadata(metadata)
    if metadata["state"] != "open" or metadata["merged"]:
        raise PullRequestUnavailable("Pull request is closed or merged; abort the review.")


class GitHubClient:
    """Fetch PR metadata, exact diff text, and all prior discussion without mutation."""

    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner if runner is not None else subprocess.run

    def _endpoint(self, ref: PullRequestRef) -> str:
        _repository(f"{ref.owner}/{ref.repo}")
        if type(ref.number) is not int or ref.number <= 0:
            raise GitHubError("Pull request number must be positive.")
        return f"repos/{ref.owner}/{ref.repo}/pulls/{ref.number}"

    def get_pr(self, ref: PullRequestRef) -> dict[str, Any]:
        return _validate_metadata(_json(_run(["gh", "api", self._endpoint(ref)], self._runner)))

    def get_diff(self, ref: PullRequestRef) -> str:
        return _run(
            [
                "gh",
                "api",
                self._endpoint(ref),
                "--header",
                "Accept: application/vnd.github.v3.diff",
            ],
            self._runner,
        )

    def _list(self, endpoint: str) -> list[dict[str, Any]]:
        pages = _json(_run(["gh", "api", endpoint, "--paginate", "--slurp"], self._runner))
        if not isinstance(pages, list):
            raise GitHubError("GitHub returned malformed paginated data; refetch the pull request.")
        items: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise GitHubError(
                    "GitHub returned malformed paginated data; refetch the pull request."
                )
            items.extend(page)
        return items

    def get_files(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        return self._list(f"{self._endpoint(ref)}/files")

    def get_reviews(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        return self._list(f"{self._endpoint(ref)}/reviews")

    def get_comments(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        endpoint = self._endpoint(ref).rsplit("/pulls/", 1)[0] + f"/issues/{ref.number}"
        return self._list(f"{endpoint}/comments")

    def get_review_comments(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        return self._list(f"{self._endpoint(ref)}/comments")
