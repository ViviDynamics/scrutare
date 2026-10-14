"""Who replied on which Scrutare thread, and whether that reply may contest a finding."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Container
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from scrutare.config import ReviewConfig
from scrutare.engine.github import GitHubClient, GitHubError, PullRequestRef, human_login
from scrutare.findings import Anchor, FindingError

MAX_PERMISSION_LOOKUPS = 32
# A thread is hostile input of any length; the prompt takes a bounded sample of it.
MAX_DISCUSSION = 16
MAX_DISCUSSION_BODY = 2000
RESPONSE_MARKER = "<!-- scrutare-reply:"


def author(comment: object) -> tuple[str, bool] | None:
    """A comment's lowercase login and whether GitHub marks the account as a bot."""
    user = comment.get("user") if isinstance(comment, dict) else None
    if not isinstance(user, dict):
        return None
    login = user.get("login")
    if not isinstance(login, str) or not login:
        return None
    bot = user.get("type") != "User" or login.endswith("[bot]")
    return login.lower(), bot


def capture_reply_permissions(client: GitHubClient, ref: PullRequestRef, run_dir: Path,
                              config: ReviewConfig, comments: list[dict[str, Any]]) -> None:
    """Retain each recent human commenter's verified permission for the trust policy.

    A lookup that fails is recorded as unknown, which the policy never trusts.
    """
    if not config.replies.enabled or config.replies.trust != "write":
        return
    permissions: dict[str, str] = {}
    complete = True
    ordered = sorted((comment for comment in comments if type(comment.get("id")) is int),
                     key=lambda comment: -comment["id"])
    for comment in ordered:
        identity = author(comment)
        if identity is None or identity[1] or identity[0] in permissions:
            continue
        if not human_login(identity[0]):
            continue
        if len(permissions) == MAX_PERMISSION_LOOKUPS:
            complete = False
            break
        try:
            permissions[identity[0]] = client.get_permission(ref, identity[0])
        except GitHubError:
            permissions[identity[0]] = "unknown"
    (run_dir / "reply-permissions.json").write_text(json.dumps({
        "schema_version": 1, "policy": "write", "complete": complete,
        "permissions": permissions}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _identity(value: object) -> bool:
    return type(value) is int and value > 0


def own_reviews(runs_root: Path, names: object, repository: str, number: int) -> dict[int, str]:
    """Reviews this pull request's earlier runs confirmed posting, with their author.

    Only a persisted receipt counts. A review body or marker can be copied by anyone.
    """
    found: dict[int, str] = {}
    for name in names if isinstance(names, list) else []:
        if (not isinstance(name, str) or name in (".", "..")
                or re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None):
            continue
        path = runs_root / name / "posting.json"
        try:
            if path.is_symlink():
                continue
            journal = json.loads(path.read_bytes())
            receipt = journal["receipt"]
            if (journal["status"] == "posted" and journal["pr_number"] == number
                    and journal["repository"].lower() == repository.lower()
                    and _identity(receipt["review_id"]) and isinstance(receipt["login"], str)):
                found[receipt["review_id"]] = receipt["login"].lower()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return found


@dataclass(frozen=True)
class ReplyView:
    """Every inline comment that is not Scrutare's own, with its thread and trust."""

    policy: str
    reviews: dict[int, str]
    threads: dict[int, Anchor | None]
    comments: tuple[dict[str, Any], ...]
    existing: tuple[dict[str, Any], ...]

    def fresh(self, consumed: list[str]) -> list[dict[str, Any]]:
        return [comment for comment in self.comments if comment["digest"] not in consumed]

    def matches(self, comment: dict[str, Any], anchor: Anchor) -> bool:
        """A thread reply contests its thread's anchor; any other comment, its line."""
        if comment["thread"] is not None:
            return self.threads[comment["thread"]] == anchor
        return bool(comment["path"] == anchor.file
                    and (comment["line"] is None or comment["line"] == anchor.line))

    def discussion(self, fresh: list[dict[str, Any]],
                   paths: Container[str]) -> tuple[list[dict[str, Any]], set[str]]:
        """A bounded projection for the prompt: trusted first, newest first."""
        chosen = sorted((comment for comment in fresh if comment["path"] in paths),
                        key=lambda comment: (not comment["trusted"], -comment["id"]))
        chosen = chosen[:MAX_DISCUSSION]
        return ([{"id": comment["id"], "in_reply_to_id": comment["in_reply_to_id"],
                  "path": comment["path"], "line": comment["line"],
                  "author": comment["author"], "trusted": comment["trusted"],
                  "body": comment["body"][:MAX_DISCUSSION_BODY]} for comment in chosen],
                {comment["digest"] for comment in chosen})

    def settle(self, state: dict[str, Any], before: list[dict[str, Any]],
               fresh: list[dict[str, Any]], *, anchor_of: Callable[[dict[str, Any]], Anchor],
               reviewed: bool, complete: bool, supplied: set[str], head: str,
               limit: int, repeated: bool) -> dict[str, Any]:
        """Consume answered contests and record one pending response per thread.

        The disposition is read from the pool the reassessment left, never from a reply.
        """
        contests = []
        answered: dict[int, list[dict[str, Any]]] = {}
        for comment in fresh:
            earlier = [entry for entry in before if self.matches(comment, anchor_of(entry))]
            upheld = any(entry["disposition"] == "upheld" for entry in earlier)
            status = "retained"
            if comment["trusted"] and upheld:
                status = "contested"
            elif comment["trusted"] and comment["thread"] is not None and earlier:
                status = "answered"
            elif reviewed and comment["digest"] in supplied:
                status = "evidence"
            if status != "retained":
                state["contests"].append(comment["digest"])
            if status in ("contested", "answered") and comment["thread"] is not None:
                answered.setdefault(comment["thread"], []).append(comment)
            contests.append(comment | {"status": status,
                                       "targets": [entry["id"] for entry in earlier]})
        responses = []
        known = {response["key"] for response in state["responses"]}
        for thread, comments in sorted(answered.items()):
            anchor = self.threads[thread]
            earlier = [entry for entry in before if anchor_of(entry) == anchor]
            current = [entry for entry in state["pool"] if anchor_of(entry) == anchor]
            dispositions = {entry["disposition"] for entry in current}
            disposition = next(name for name in ("upheld", "withdrawn", "fixed")
                               if name in dispositions or name == "fixed")
            if not any(entry["disposition"] == "upheld" for entry in earlier):
                outcome = "recorded"
            elif not reviewed:
                outcome = "exhausted"
            else:
                outcome = "reassessed" if complete else "incomplete"
            key = sha256(_encode([thread, sorted(comment["digest"] for comment in comments)])
                         ).hexdigest()[:32]
            if key in known:
                continue
            response = {
                "key": key, "thread": thread,
                "replies": sorted(comment["id"] for comment in comments),
                "disposition": disposition, "outcome": outcome, "head_sha": head,
                "round": state["rounds_completed"], "round_limit": limit,
                "findings": [entry["finding"] for entry in current
                             if entry["disposition"] == "upheld"],
                "original": [{"id": entry["id"], "finding": entry["finding"],
                              "disposition": entry["disposition"]} for entry in earlier],
            }
            responses.append(response)
            state["responses"].append(response | {"status": "pending"})
        return {"schema_version": 1, "policy": self.policy, "head_sha": head,
                "review_required": not repeated, "own_reviews": sorted(self.reviews),
                "existing_threads": list(self.existing), "contests": contests,
                "responses": responses}


def reply_view(run: Path, config: ReviewConfig, runs: object, comments: object,
               generated: set[Any], *, repository: str, number: int, head: str) -> ReplyView:
    """Classify captured inline comments under the configured trust policy."""
    reviews = own_reviews(run.parent, runs, repository, number)
    logins = set(reviews.values())
    permissions: dict[str, Any] = {}
    if config.replies.trust == "write":
        try:
            saved = json.loads((run / "reply-permissions.json").read_bytes())
            if isinstance(saved, dict) and isinstance(saved.get("permissions"), dict):
                permissions = saved["permissions"]
        except (OSError, ValueError):
            permissions = {}
    threads: dict[int, Anchor | None] = {}
    existing = []
    valid = []
    for comment in comments if isinstance(comments, list) else []:
        if (not isinstance(comment, dict) or not _identity(comment.get("id"))
                or not isinstance(comment.get("body"), str) or not comment["body"]
                or not isinstance(comment.get("path"), str)
                or not (comment.get("line") is None or _identity(comment["line"]))
                or not (comment.get("in_reply_to_id") is None
                        or _identity(comment["in_reply_to_id"]))):
            continue
        valid.append(comment)
        if comment.get("in_reply_to_id") is None and comment.get(
                "pull_request_review_id") in reviews:
            line = comment["line"] if comment["line"] is not None else comment.get("original_line")
            anchor: Anchor | None = None
            if type(line) is int and line > 0:
                try:
                    side = comment.get("side")
                    anchor = Anchor(comment["path"], line, side if side is not None else "RIGHT")
                except (FindingError, TypeError):
                    anchor = None
            threads[comment["id"]] = anchor
            if anchor is not None and comment.get("original_commit_id") == head and _identity(
                    comment.get("original_line")):
                existing.append({"comment_id": comment["id"], "path": anchor.file,
                                 "line": comment["original_line"], "side": anchor.side})
    entries = []
    for comment in valid:
        identity = author(comment)
        if comment["id"] in threads or (
                comment.get("in_reply_to_id") is None
                and comment.get("pull_request_review_id") in generated):
            continue  # A posted finding never contests itself.
        if identity is not None and identity[0] in logins and RESPONSE_MARKER in comment["body"]:
            continue  # Scrutare's own threaded response.
        login, bot = identity if identity is not None else ("", True)
        permission = permissions.get(login) if isinstance(permissions.get(login), str) else None
        trusted = not bot and (config.replies.trust == "anyone"
                               or permission in ("admin", "write"))
        parent = comment.get("in_reply_to_id")
        entries.append({
            "id": comment["id"], "in_reply_to_id": parent, "path": comment["path"],
            "line": comment["line"], "author": login, "bot": bot, "permission": permission,
            "trusted": trusted, "body": comment["body"],
            "thread": parent if parent in threads else None,
            "digest": sha256(_encode([comment["id"], comment["body"]])).hexdigest()})
    return ReplyView(config.replies.trust, reviews, threads,
                     tuple(sorted(entries, key=lambda entry: entry["id"])),
                     tuple(sorted(existing, key=lambda entry: entry["comment_id"])))
