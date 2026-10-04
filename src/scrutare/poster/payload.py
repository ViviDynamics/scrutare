"""Pure rendering of captured verdicts into immutable GitHub REST reviews."""

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from scrutare.config import PostMode, Strategy
from scrutare.findings import Anchor, FindingError, Verdict, parse_diff
from scrutare.poster.errors import PostingError

ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]


@dataclass(frozen=True)
class ReviewComment:
    """One inline comment at an exact captured diff anchor."""

    anchor: Anchor
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, Anchor):
            raise PostingError("anchor: expected an Anchor")
        _utf8_text(self.anchor.file, "anchor.file")
        _utf8_text(self.body, "body")


@dataclass(frozen=True)
class ReviewPayload:
    """A complete review pinned to the captured commit."""

    head_sha: str
    body: str
    event: ReviewEvent
    comments: tuple[ReviewComment, ...]

    def __post_init__(self) -> None:
        _head_sha(self.head_sha)
        _utf8_text(self.body, "body")
        if not isinstance(self.event, str) or self.event not in (
            "APPROVE", "REQUEST_CHANGES", "COMMENT"
        ):
            raise PostingError("event: expected APPROVE, REQUEST_CHANGES or COMMENT")
        if not isinstance(self.comments, tuple) or any(
            not isinstance(comment, ReviewComment) for comment in self.comments
        ):
            raise PostingError("comments: expected a tuple of ReviewComment values")

    def to_dict(self) -> dict[str, Any]:
        """Return fresh JSON-compatible GitHub create-review fields."""
        return {
            "commit_id": self.head_sha,
            "body": self.body,
            "event": self.event,
            "comments": [
                {"path": comment.anchor.file, "line": comment.anchor.line,
                 "side": comment.anchor.side, "body": comment.body}
                for comment in self.comments
            ],
        }

    def to_bytes(self) -> bytes:
        """Encode canonical sorted UTF-8 JSON with a final newline."""
        data = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        return (data + "\n").encode("utf-8")


def _head_sha(value: object) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value) is None
    ):
        raise PostingError("head_sha: expected a complete 40 or 64 hexadecimal commit SHA")


def _utf8_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PostingError(f"{field}: expected nonempty UTF-8 text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PostingError(f"{field}: expected nonempty UTF-8 text") from None


def _field(label: str, text: str) -> str:
    # Every physical line stays in the same quoted field. Escape formatting,
    # HTML, and invisible controls without hiding ordinary Unicode text.
    quoted = []
    for line in text.splitlines():
        visible = "".join(
            f"\\u{ord(char):04x}" if unicodedata.category(char) in ("Cc", "Cf", "Cs") else char
            for char in line
        )
        escaped = re.sub(r"([\\`*_{}\[\]()#+.!|\-])", r"\\\1", html.escape(visible, quote=False))
        quoted.append(f"> {escaped}")
    return f"{label}:\n\n" + "\n".join(quoted)


def build_review_payload(
    verdict: Verdict, diff: bytes | str, *, head_sha: str, strategy: Strategy,
    post_mode: PostMode, run_id: str,
) -> ReviewPayload:
    """Render the supplied code verdict and every source without external work."""
    if not isinstance(verdict, Verdict):
        raise PostingError("verdict: expected a code-derived Verdict")
    _head_sha(head_sha)
    if not isinstance(strategy, str) or strategy not in ("panel", "iterative", "debate"):
        raise PostingError("strategy: expected panel, iterative or debate")
    if not isinstance(post_mode, str) or post_mode not in ("review", "comment"):
        raise PostingError("post_mode: expected review or comment")
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise PostingError("run_id: expected 32 lowercase hexadecimal characters")
    try:
        anchors = parse_diff(diff)
    except FindingError:
        raise PostingError("diff: expected a valid captured Git patch") from None
    if any(finding.anchor not in anchors for finding in verdict.findings):
        raise PostingError("anchor: verdict findings must all belong to the captured diff")
    body = (
        f"Scrutare review\n\nStrategy: {strategy}\nVerdict: {verdict.verdict}\n"
        f"Rule: {verdict.rule}\nFindings: {len(verdict.findings)}\nHead SHA: {head_sha}\n\n"
        f"<!-- scrutare-run:{run_id} -->"
    )
    event: ReviewEvent = "COMMENT" if post_mode == "comment" else (
        "APPROVE" if verdict.verdict == "approve" else "REQUEST_CHANGES"
    )
    comments = []
    for finding in verdict.findings:
        fields = [_field("Problem", finding.problem)]
        for source in finding.sources:
            fields.extend((
                _field("Persona", source.persona), _field("Category", source.category),
                _field("Reason", source.reason),
            ))
        comments.append(ReviewComment(finding.anchor, "\n\n".join(fields)))
    return ReviewPayload(head_sha, body, event, tuple(comments))
