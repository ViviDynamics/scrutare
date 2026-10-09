"""Read-only GitHub transport using gh's existing authentication."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha1
from typing import Any

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
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
            text=False,
            shell=False,
            timeout=_TIMEOUT_SECONDS,
            env={**os.environ, "GH_HOST": "github.com"},
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
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise GitHubError("GitHub returned invalid UTF-8; refetch the pull request.") from None


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
        or type(metadata.get("changed_files")) is not int
        or metadata["changed_files"] < 0
        or metadata.get("state") not in ("open", "closed")
        or type(metadata.get("merged")) is not bool
        or not isinstance(metadata.get("head"), dict)
        or not isinstance(metadata["head"].get("sha"), str)
        or not metadata["head"]["sha"].strip()
        or not isinstance(metadata.get("base"), dict)
        or not isinstance(metadata["base"].get("ref"), str)
        or not metadata["base"]["ref"].strip()
        or not isinstance(metadata["base"].get("sha"), str)
        or not metadata["base"]["sha"].strip()
        or not isinstance(metadata["base"].get("repo"), dict)
        or not isinstance(metadata["base"]["repo"].get("full_name"), str)
    ):
        raise GitHubError("GitHub returned malformed PR metadata; refetch the pull request.")
    try:
        _repository(metadata["base"]["repo"]["full_name"])
    except GitHubError:
        raise GitHubError(
            "GitHub returned malformed PR metadata; refetch the pull request."
        ) from None
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

    def _object(self, repository: str, kind: str, sha: str) -> dict[str, Any]:
        owner, repo = _repository(repository)
        if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise GitHubError("Repository context requires an exact commit or object SHA.")
        data = _json(_run(["gh", "api", f"repos/{owner}/{repo}/git/{kind}/{sha}"],
                          self._runner))
        if not isinstance(data, dict) or data.get("sha") != sha:
            raise GitHubError("GitHub object identity disagrees with the requested revision.")
        return data

    def get_commit(self, repository: str, sha: str) -> str:
        """Resolve only a pinned commit to its tree object, never a branch."""
        data = self._object(repository, "commits", sha)
        tree = data.get("tree")
        if (not isinstance(tree, dict) or not isinstance(tree.get("sha"), str)
                or re.fullmatch(r"[0-9a-f]{40}", tree["sha"]) is None):
            raise GitHubError("GitHub returned malformed commit tree metadata.")
        return str(tree["sha"])

    def get_tree(self, repository: str, sha: str) -> tuple[dict[str, Any], ...]:
        """Read one nonrecursive tree and reject incomplete or unsafe entries."""
        data = self._object(repository, "trees", sha)
        if data.get("truncated") is not False or not isinstance(data.get("tree"), list):
            raise GitHubError("GitHub returned an incomplete repository tree.")
        seen: set[str] = set()
        entries = []
        for entry in data["tree"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                    or entry["path"] in ("", ".", "..")
                    or any(char in entry["path"] for char in "/\\\x00")
                    or entry["path"] in seen
                    or not isinstance(entry.get("sha"), str)
                    or re.fullmatch(r"[0-9a-f]{40}", entry["sha"]) is None
                    or (entry.get("mode"), entry.get("type")) not in {
                        ("100644", "blob"), ("100755", "blob"), ("120000", "blob"),
                        ("040000", "tree"), ("160000", "commit")}
                    or entry["type"] == "blob" and (
                        type(entry.get("size")) is not int or entry["size"] < 0)):
                raise GitHubError("GitHub returned malformed repository tree entries.")
            seen.add(entry["path"])
            entries.append(entry)
        return tuple(entries)

    def get_blob(self, repository: str, sha: str, *, max_bytes: int) -> bytes:
        """Decode bounded blob bytes and verify their Git identity before retaining them."""
        data = self._object(repository, "blobs", sha)
        try:
            if (data.get("encoding") != "base64" or type(data.get("size")) is not int
                    or not 0 <= data["size"] <= max_bytes
                    or not isinstance(data.get("content"), str)
                    or len(data["content"]) > (max_bytes * 2 + 256)):
                raise ValueError
            content = base64.b64decode("".join(data["content"].split()), validate=True)
            identity = sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            if len(content) != data["size"] or identity != sha:
                raise ValueError
            return content
        except (ValueError, binascii.Error):
            raise GitHubError("GitHub returned invalid or oversized blob content.") from None
