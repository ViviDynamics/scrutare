"""One-attempt review writes and bounded read-only GitHub requests."""

import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

from scrutare.engine.github import GitHubClient, GitHubError, PullRequestRef, Runner, resolve_pr
from scrutare.poster.errors import (
    PostingError,
    PostingRateLimited,
    PostingRejected,
    PostingUncertain,
)
from scrutare.poster.payload import ReviewPayload

_T = TypeVar("_T")

_STATES = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}
_LOGIN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:\[bot\])?")
_STATUS = re.compile(r"HTTP/(?:1\.[01]|2(?:\.0)?) ([1-5][0-9]{2})(?: [^\r\n]*)?")
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+:([^\r\n]*)")
_UNCERTAIN = "Review delivery is uncertain; reconcile the run before sending another review."


@dataclass(frozen=True)
class PostedReview:
    """A confirmed published review receipt."""

    review_id: int
    html_url: str
    commit_id: str
    body: str
    state: str
    login: str


@dataclass(frozen=True)
class _Response:
    status: int
    value: Any
    rate_limited: bool
    retry_after: float


def _validated_ref(ref: PullRequestRef) -> PullRequestRef:
    if (
        not isinstance(ref, PullRequestRef)
        or not isinstance(ref.owner, str)
        or not isinstance(ref.repo, str)
    ):
        raise PostingError("Use a valid pull request reference.")
    return resolve_pr(str(ref.number), f"{ref.owner}/{ref.repo}")


def _login(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value.removesuffix("[bot]")) <= 39
        and _LOGIN.fullmatch(value) is not None
    )


def _timing(value: str, *, date: bool = False) -> float | None:
    try:
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            seconds = float(value)
        elif date:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return None
            seconds = max(0.0, parsed.timestamp() - time.time())
        else:
            return None
    except (ValueError, OverflowError, TypeError):
        return None
    if not math.isfinite(seconds):
        raise PostingUncertain(_UNCERTAIN)
    return seconds


def _parse_response(output: bytes, *, failed: bool) -> _Response:
    """Parse only a status block at stream start, never diagnostic prose."""
    try:
        text = output.decode("utf-8")
        head, body = re.split(r"\r?\n\r?\n", text, maxsplit=1)
        lines = re.split(r"\r?\n", head)
        match = _STATUS.fullmatch(lines[0])
        if match is None:
            raise ValueError
        status = int(match[1])
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if _HEADER.fullmatch(line) is None:
                raise ValueError
            name, value = line.split(":", 1)
            name = name.lower()
            if name in {"retry-after", "x-ratelimit-reset", "x-ratelimit-remaining"}:
                if name in headers:
                    raise ValueError
                headers[name] = value.strip()
        body = body.lstrip()
        value, end = json.JSONDecoder().raw_decode(body)
        if not failed and body[end:].strip():
            raise ValueError
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise PostingUncertain(_UNCERTAIN) from None
    retry = _timing(headers["retry-after"], date=True) if "retry-after" in headers else None
    primary_exhausted = headers.get("x-ratelimit-remaining") == "0"
    reset = (
        _timing(headers["x-ratelimit-reset"])
        if primary_exhausted and "x-ratelimit-reset" in headers else None
    )
    delays = [max(0.0, reset - time.time())] if reset is not None else []
    if retry is not None:
        delays.append(retry)
    malformed_timing = (
        ("retry-after" in headers and retry is None)
        or (primary_exhausted and "x-ratelimit-reset" in headers and reset is None)
    )
    if not delays or malformed_timing:
        delays.append(60.0)
    secondary_limited = (
        isinstance(value, dict) and isinstance(value.get("message"), str)
        and re.match(r"you have exceeded a secondary rate limit\b", value["message"],
                     re.IGNORECASE) is not None
    )
    limited = status == 429 or (
        status == 403 and (retry is not None or primary_exhausted or secondary_limited)
    )
    return _Response(status, value, limited, max(delays))


def _http_result(result: subprocess.CompletedProcess[bytes]) -> _Response:
    failed = result.returncode != 0
    # Two structured streams conflict even when their status lines agree.
    if result.stdout.startswith(b"HTTP/") and result.stderr.startswith(b"HTTP/"):
        raise PostingUncertain(_UNCERTAIN)
    stream = result.stderr if failed and result.stderr.startswith(b"HTTP/") else result.stdout
    response = _parse_response(stream, failed=failed)
    if failed == (200 <= response.status < 300):
        raise PostingUncertain(_UNCERTAIN)
    return response


def _receipt(value: Any, ref: PullRequestRef, payload: ReviewPayload) -> PostedReview:
    if not isinstance(value, dict):
        raise PostingUncertain(_UNCERTAIN)
    review_id = value.get("id")
    user = value.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    html_url = value.get("html_url")
    expected_url = (
        rf"https://github\.com/(?i:{re.escape(ref.owner)}/{re.escape(ref.repo)})/pull/{ref.number}"
        f"#pullrequestreview-{review_id}"
    )
    if (
        type(review_id) is not int or review_id <= 0
        or not isinstance(html_url, str)
        or re.fullmatch(expected_url, html_url) is None
        or value.get("commit_id") != payload.head_sha
        or value.get("body") != payload.body
        or value.get("state") != _STATES[payload.event]
        or not _login(login)
    ):
        raise PostingUncertain(_UNCERTAIN)
    assert isinstance(login, str)
    return PostedReview(
        review_id, value["html_url"], value["commit_id"], value["body"], value["state"], login,
    )


class ReviewClient:
    """Create exactly once; leave every write retry to posting orchestration."""

    def __init__(
        self, runner: Runner | None = None, *, sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._runner = runner if runner is not None else subprocess.run
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self._reads = GitHubClient(runner=self._runner)

    def create_review(self, ref: PullRequestRef, payload: ReviewPayload) -> PostedReview:
        ref = _validated_ref(ref)
        if not isinstance(payload, ReviewPayload):
            raise PostingError("Use a validated review payload.")
        try:
            result = self._runner(
                [
                    "gh", "api", "--method", "POST",
                    f"repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews",
                    "--include", "--input", "-",
                    "--header", "Accept: application/vnd.github+json",
                    "--header", "X-GitHub-Api-Version: 2022-11-28",
                    "--header", "Content-Type: application/json",
                ],
                input=payload.to_bytes(), capture_output=True, text=False, shell=False,
                timeout=60, env={**os.environ, "GH_HOST": "github.com"},
            )
        except FileNotFoundError:
            raise PostingRejected(
                "Install the GitHub CLI (gh) and check authentication before retrying.",
                unsent=True,
            ) from None
        except PermissionError:
            raise PostingRejected(
                "Could not launch gh; check executable permissions before retrying.", unsent=True,
            ) from None
        except (OSError, subprocess.SubprocessError):
            raise PostingUncertain(_UNCERTAIN) from None
        response = _http_result(result)
        if response.rate_limited:
            raise PostingRateLimited(status=response.status, retry_after=response.retry_after)
        if 400 <= response.status < 500:
            raise PostingRejected(
                "GitHub rejected the review; check authentication, access, and captured commit.",
                status=response.status,
            )
        if not 200 <= response.status < 300:
            raise PostingUncertain(_UNCERTAIN)
        return _receipt(response.value, ref, payload)

    def _read(self, operation: Callable[[], _T]) -> _T:
        for attempt in range(3):
            try:
                return operation()
            except PostingRateLimited as error:
                if attempt == 2 or error.retry_after > 60:
                    raise error from None
                self._sleeper(error.retry_after)
                continue
            except GitHubError:
                if attempt == 2:
                    break
            self._sleeper(float(attempt + 1))
        raise PostingError(
            "GitHub read failed; check gh authentication, access, and connectivity before retrying."
        ) from None

    def get_pr(self, ref: PullRequestRef) -> dict[str, Any]:
        """Read validated metadata; orchestration owns the open-state check."""
        ref = _validated_ref(ref)
        return self._read(lambda: self._reads.get_pr(ref))

    def get_reviews(self, ref: PullRequestRef) -> list[dict[str, Any]]:
        """Read every review page using the existing pagination validator."""
        ref = _validated_ref(ref)
        return self._read(lambda: self._reads.get_reviews(ref))

    def get_login(self) -> str:
        """Read current identity for reconciliation; never a write precondition."""
        return self._read(self._get_login)

    def _get_login(self) -> str:
        try:
            result = self._runner(
                [
                    "gh", "api", "--method", "GET", "user", "--include",
                    "--header", "Accept: application/vnd.github+json",
                    "--header", "X-GitHub-Api-Version: 2022-11-28",
                ],
                capture_output=True, text=False, shell=False, timeout=60,
                env={**os.environ, "GH_HOST": "github.com"},
            )
        except (OSError, subprocess.SubprocessError):
            raise PostingError(
                "Could not read GitHub identity; check gh installation and access."
            ) from None
        response = _http_result(result)
        if response.rate_limited:
            raise PostingRateLimited(status=response.status, retry_after=response.retry_after)
        if not 200 <= response.status < 300 or not isinstance(response.value, dict):
            raise PostingError("Could not read GitHub identity; check authentication and access.")
        login = response.value.get("login")
        if not _login(login):
            raise PostingError("GitHub returned an invalid authenticated login.")
        assert isinstance(login, str)
        return login
