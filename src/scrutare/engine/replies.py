"""Who replied on which Scrutare thread, and whether that reply may contest a finding."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scrutare.config import ReviewConfig
from scrutare.engine.github import GitHubClient, GitHubError, PullRequestRef, human_login

MAX_PERMISSION_LOOKUPS = 32


def author(comment: object) -> tuple[str, bool] | None:
    """A comment's lowercase login and whether GitHub marks the account as a bot."""
    user = comment.get("user") if isinstance(comment, dict) else None
    login = user.get("login") if isinstance(user, dict) else None
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
