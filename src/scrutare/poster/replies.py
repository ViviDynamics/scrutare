"""One threaded response per contested finding, journaled before every write."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from scrutare.config import ReviewConfig
from scrutare.engine.github import GitHubError, assert_pr_open, resolve_pr
from scrutare.engine.replies import RESPONSE_MARKER, author, own_reviews
from scrutare.poster.client import ReviewClient, ThreadReply
from scrutare.poster.errors import (
    PostingError,
    PostingRateLimited,
    PostingRejected,
    PostingUncertain,
)
from scrutare.poster.journal import atomic_write, canonical, read_json, run_lock
from scrutare.poster.payload import _field

_UNCERTAIN = "Reply delivery is uncertain; inspect reply-posting.json before any manual reply."
_OUTCOMES = {
    "reassessed": "Reassessed with this thread's replies as evidence.",
    "incomplete": "The reassessment did not cover this finding completely, so it stands "
                  "pending a complete review.",
    "exhausted": "Not reassessed: this pull request has used all {limit} review rounds, and "
                 "the review is escalated to human reviewers.",
    "recorded": "No reassessment was needed.",
}
_DISPOSITIONS = {
    "upheld": "The finding is still supported and stands.",
    "withdrawn": "The unchanged code no longer yields the finding, so it is withdrawn and no "
                 "longer counts toward the verdict.",
    "fixed": "The changed code no longer yields the finding.",
}
_FINAL = ("posted", "observed", "rejected")


@dataclass(frozen=True)
class PostedReply:
    """A threaded response this run delivered, or found already delivered."""

    key: str
    thread: int
    comment_id: int
    html_url: str | None
    disposition: str
    status: str


def render_reply(response: dict[str, Any]) -> str:
    """Render code-derived fields and quoted model text; never any reply text."""
    try:
        key, disposition, outcome = response["key"], response["disposition"], response["outcome"]
        head, completed, limit = response["head_sha"], response["round"], response["round_limit"]
        if (not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{32}", key) is None
                or outcome not in _OUTCOMES or disposition not in _DISPOSITIONS
                or not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{4,64}", head) is None
                or type(completed) is not int or type(limit) is not int
                or not 0 <= completed <= limit):
            raise ValueError
        parts = [f"Scrutare reassessment\n\nDisposition: {disposition}\n"
                 f"Outcome: {_OUTCOMES[outcome].format(limit=limit)}"
                 # A partial wave establishes nothing about a finding it did not reach.
                 + ("" if (outcome, disposition) == ("incomplete", "upheld")
                    else f" {_DISPOSITIONS[disposition]}") + "\n"
                 f"Head SHA: {head}\nRound: {completed} of {limit}"]
        for finding in response["findings"]:
            for label in ("problem", "persona", "category", "reason"):
                parts.append(_field(label.capitalize(), finding[label], neutralize_mentions=True))
        parts.append(f"{RESPONSE_MARKER}{key} -->")
        return "\n\n".join(parts)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PostingError("Reply evidence is invalid; inspect the saved run.") from None


def post_replies(run_dir: Path, config: ReviewConfig, *,
                 client: ReviewClient | None = None) -> tuple[PostedReply, ...]:
    """Deliver every pending response of this pull request exactly once.

    A response is marked `sending` in the history and the run journal before its
    POST. One left `sending` or `unknown` is only ever reconciled against a later
    capture, by marker and Scrutare's own login; it is never sent again. The run in
    which a response becomes uncertain fails; later runs are not failed by it.
    """
    if not config.replies.enabled:
        return ()
    # The history lock belongs to the engine; delivery follows it, never overlaps it.
    from scrutare.engine.iterative import _history, _save

    with run_lock(run_dir), _history(run_dir, config) as (state, path):
        try:
            metadata = read_json(run_dir / "metadata.json")
            ref = resolve_pr(str(metadata["pr_number"]), repository=metadata["repository"])
            comments = read_json(run_dir / "review_comments.json")
            responses = state.get("responses", [])
            logins = set(own_reviews(run_dir.parent, state.get("runs"), metadata["repository"],
                                     metadata["pr_number"]).values())
            if not isinstance(responses, list) or not isinstance(comments, list):
                raise ValueError
            journal_path = run_dir / "reply-posting.json"
            journal: dict[str, dict[str, Any]] = {}
            if journal_path.exists():
                journal = {entry["key"]: entry for entry in read_json(journal_path)["responses"]}
            bodies: dict[str, str] = {}
            for response in responses:
                if (not isinstance(response, dict) or not isinstance(response["key"], str)
                        or not isinstance(response["status"], str)
                        or type(response["thread"]) is not int or response["thread"] <= 0
                        or not isinstance(response["disposition"], str)):
                    raise ValueError
                if response["status"] in _FINAL:
                    continue
                try:
                    bodies[response["key"]] = render_reply(response)
                except PostingError:
                    # Set aside, so one bad record cannot hold back every other thread.
                    response["status"] = "rejected"
                    _save(path, state)
        except (KeyError, TypeError, ValueError, GitHubError) as error:
            if isinstance(error, PostingError):
                raise
            raise PostingError("Reply evidence is invalid; inspect the saved run.") from None

        def record(response: dict[str, Any], **changes: Any) -> None:
            response.update(changes)
            _save(path, state)
            journal[response["key"]] = {
                "key": response["key"], "thread": response["thread"],
                "disposition": response["disposition"], "status": response["status"],
                "attempts": response.get("attempts", 0),
                "comment_id": response.get("comment_id"), "html_url": response.get("html_url"),
                "http_status": response.get("http_status"),
                "body_sha256": sha256(bodies[response["key"]].encode("utf-8")).hexdigest()}
            atomic_write(journal_path, canonical({
                "schema_version": 1, "repository": metadata["repository"],
                "pr_number": metadata["pr_number"],
                "responses": sorted(journal.values(), key=lambda entry: entry["thread"])}))

        transport = client if client is not None else ReviewClient()
        failure: PostingError | None = None
        open_checked = False
        for response in sorted(responses, key=lambda item: item["thread"]):
            if response["status"] in _FINAL:
                continue
            body = bodies[response["key"]]
            marker = f"{RESPONSE_MARKER}{response['key']} -->"
            seen = [comment for comment in comments
                    if isinstance(comment, dict) and type(comment.get("id")) is int
                    and comment.get("in_reply_to_id") == response["thread"]
                    and isinstance(comment.get("body"), str) and marker in comment["body"]
                    and (author(comment) or ("", True))[0] in logins]
            if len(seen) == 1:
                record(response, status="observed", comment_id=seen[0]["id"],
                       html_url=seen[0].get("html_url")
                       if isinstance(seen[0].get("html_url"), str) else None)
                continue
            if response["status"] == "unknown":
                continue  # Reported when it became uncertain; only a capture resolves it.
            if seen or response["status"] == "sending":
                # A reply may exist. Its absence from one capture proves nothing.
                record(response, status="unknown")
                failure = PostingUncertain(_UNCERTAIN)
                continue
            if isinstance(failure, PostingRateLimited):
                continue
            if response.get("attempts", 0) >= 3:
                record(response, status="rejected")
                continue
            if not open_checked:
                try:
                    assert_pr_open(transport.get_pr(ref))
                except PostingError:
                    raise
                except GitHubError:
                    raise PostingError(
                        "Pull request is closed or merged; no reply was sent.") from None
                open_checked = True
            record(response, status="sending", attempts=response.get("attempts", 0) + 1)
            try:
                receipt = transport.create_reply(ref, response["thread"], body)
                if not isinstance(receipt, ThreadReply) or receipt.thread != response["thread"]:
                    raise PostingUncertain(_UNCERTAIN)
            except PostingRateLimited as error:
                record(response, status="pending")
                failure = error
            except PostingRejected as error:
                if error.unsent:
                    record(response, status="pending")
                else:
                    record(response, status="rejected", http_status=error.status)
                failure = failure if isinstance(failure, PostingUncertain) else error
            except Exception:
                record(response, status="unknown")
                failure = PostingUncertain(_UNCERTAIN)
            else:
                record(response, status="posted", comment_id=receipt.comment_id,
                       html_url=receipt.html_url)
        if failure is not None:
            raise failure
        return tuple(
            PostedReply(entry["key"], entry["thread"], entry["comment_id"], entry["html_url"],
                        entry["disposition"], entry["status"])
            for entry in sorted(journal.values(), key=lambda entry: entry["thread"])
            if entry["status"] in ("posted", "observed"))

