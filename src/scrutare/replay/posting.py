"""Inspect original local posting evidence without executing posting or resolving a PR."""

import re
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from scrutare.engine.github import PullRequestRef
from scrutare.findings import Anchor, FindingError
from scrutare.poster.client import PostedReview
from scrutare.poster.errors import PostingError
from scrutare.poster.escalation import _saved_request
from scrutare.poster.journal import canonical
from scrutare.poster.payload import ReviewComment, ReviewEvent, ReviewPayload, _head_sha
from scrutare.poster.posting import _saved_receipt, _validate_state
from scrutare.poster.reviewers import normalize_human_reviewers
from scrutare.replay.artifacts import decode_artifact, read_artifact
from scrutare.replay.models import AuditIssue, PostingAudit, ReplayError

_INTENT_FIELDS = (
    "schema_version", "repository", "pr_number", "head_sha", "event", "run_id",
    "payload_sha256", "verdict_sha256", "diff_sha256", "config_sha256",
)
_INVALID = (ReplayError, PostingError, FindingError, ValueError, TypeError, KeyError)


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise ReplayError("artifact: cannot inspect artifact file") from None
    return True


def _object(value: object, fields: tuple[str, ...] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or (fields is not None and set(value) != set(fields)):
        raise ReplayError("artifact: invalid object fields")
    return cast(dict[str, Any], value)


def _document(run_dir: Path, name: str) -> dict[str, Any]:
    return _object(decode_artifact(read_artifact(run_dir / name), artifact=name))


def _intent(state: dict[str, Any]) -> tuple[dict[str, Any], PullRequestRef]:
    intent = {field: state[field] for field in _INTENT_FIELDS}
    if type(intent["schema_version"]) is not int or intent["schema_version"] != 1:
        raise ReplayError("posting.schema_version: unsupported version")
    number = intent["pr_number"]
    repository = intent["repository"]
    if type(number) is not int or number <= 0 or not isinstance(repository, str):
        raise ReplayError("posting: invalid pull request identity")
    match = re.fullmatch(r"([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9_.-]+)",
                         repository)
    if match is None or match[2] in (".", ".."):
        raise ReplayError("posting.repository: invalid repository identity")
    _head_sha(intent["head_sha"])
    for field in ("payload_sha256", "verdict_sha256", "diff_sha256", "config_sha256", "run_id"):
        length = 32 if field == "run_id" else 64
        if (not isinstance(intent[field], str)
                or re.fullmatch(rf"[0-9a-f]{{{length}}}", intent[field]) is None):
            raise ReplayError("posting: invalid digest or run identity")
    if intent["event"] not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise ReplayError("posting.event: invalid event")
    _validate_state(state, intent)
    return intent, PullRequestRef(match[1], match[2], number)


def _payload(run_dir: Path, intent: dict[str, Any]) -> ReviewPayload:
    raw = read_artifact(run_dir / "review-payload.json")
    if sha256(raw).hexdigest() != intent["payload_sha256"]:
        raise ReplayError("review-payload: original hash disagrees")
    document = _object(decode_artifact(raw, artifact="review-payload.json"),
                       ("commit_id", "body", "event", "comments"))
    if not isinstance(document["comments"], list):
        raise ReplayError("review-payload.comments: invalid comments")
    comments = []
    for value in document["comments"]:
        comment = _object(value, ("path", "line", "side", "body"))
        comments.append(ReviewComment(Anchor(comment["path"], comment["line"], comment["side"]),
                                      comment["body"]))
    payload = ReviewPayload(document["commit_id"], document["body"],
                            cast(ReviewEvent, document["event"]), tuple(comments))
    marker = f"<!-- scrutare-run:{intent['run_id']} -->"
    if (payload.to_bytes() != raw or payload.head_sha != intent["head_sha"]
            or payload.event != intent["event"] or not payload.body.endswith(marker)
            or payload.body.count(marker) != 1):
        raise ReplayError("review-payload: original intent disagrees")
    return payload


def _original_targets(payload: ReviewPayload) -> tuple[str, ...]:
    """Read original escalation targets from the immutable rendered header."""
    header, separator, _ = payload.body.partition("\n\nUnresolved findings:\n\n")
    if (payload.event != "COMMENT" or not header.startswith("Scrutare escalation\n\n")
            or "\nVerdict: escalated\n" not in header
            or "\nRule: rounds_exhausted_without_convergence\n" not in header or not separator):
        raise ReplayError("escalation: expected original escalation payload")
    targets = header.rsplit("\n\n", 1)[-1]
    if targets == "This repository has no escalation targets configured.":
        return ()
    prefix = "Escalation targets for human review: "
    if not targets.startswith(prefix):
        raise ReplayError("escalation: original targets unavailable")
    mentions = targets[len(prefix):].split(" ")
    if any(not mention.startswith("@") for mention in mentions):
        raise ReplayError("escalation: invalid original targets")
    values = tuple(mention[1:] for mention in mentions)
    if normalize_human_reviewers(values) != values:
        raise ReplayError("escalation: expected normalized original targets")
    return values


def _request_status(
    run_dir: Path, intent: dict[str, Any], payload: ReviewPayload, review: PostedReview | None,
) -> str:
    journal = _present(run_dir / "escalation.json")
    request = _present(run_dir / "reviewer-request.json")
    if not journal and not request:
        return "absent"
    if not journal or not request or review is None:
        raise ReplayError("escalation: incomplete original request pair")
    reviewers = _original_targets(payload)
    raw = read_artifact(run_dir / "reviewer-request.json")
    # Exact canonical bytes reject duplicate fields and noncanonical request shape too.
    if raw != canonical({"reviewers": list(reviewers)}):
        raise ReplayError("reviewer-request: original targets disagree")
    state = _document(run_dir, "escalation.json")
    request_intent = intent | {
        "review_id": review.review_id,
        "review_receipt_sha256": sha256(canonical(asdict(review))).hexdigest(),
        "reviewers": list(reviewers), "request_sha256": sha256(raw).hexdigest(),
    }
    if type(state.get("review_id")) is not int:
        raise ReplayError("escalation.review_id: invalid review identity")
    if state.get("status") == "skipped":
        _validate_state(state | {"status": "prepared", "receipt": None}, request_intent)
        if reviewers or _saved_request(state["receipt"], reviewers).provenance != "no_targets":
            raise ReplayError("escalation: invalid skipped request")
        return "skipped"
    _validate_state(state, request_intent)
    if not reviewers:
        raise ReplayError("escalation: missing original targets")
    if state["status"] == "posted":
        receipt = _saved_request(state["receipt"], reviewers)
        return "observed_requested" if receipt.provenance == "observed_requested" else "posted"
    return cast(str, state["status"])


def _capture_issues(run_dir: Path, intent: dict[str, Any]) -> tuple[AuditIssue, ...]:
    issues = []
    for name, field, code in (
        ("diff.patch", "diff_sha256", "diff_hash_disagrees"),
        ("config.json", "config_sha256", "config_hash_disagrees"),
    ):
        try:
            if not _present(run_dir / name):
                continue
            raw = read_artifact(run_dir / name)
            if name == "config.json":
                raw = canonical(decode_artifact(raw, artifact=name))
            if sha256(raw).hexdigest() != intent[field]:
                issues.append(AuditIssue(code, name, "difference"))
        except _INVALID:
            issues.append(AuditIssue("capture_unreadable", name, "incomplete"))
    try:
        if _present(run_dir / "metadata.json"):
            metadata = _document(run_dir, "metadata.json")
            nested = _object(metadata["pull_request"])
            if (type(metadata["schema_version"]) is not int or metadata["schema_version"] != 1
                    or type(metadata["pr_number"]) is not int
                    or type(nested["number"]) is not int
                    or metadata["pr_number"] != intent["pr_number"]
                    or nested["number"] != intent["pr_number"]
                    or metadata["head_sha"] != intent["head_sha"]
                    or nested["head"]["sha"] != intent["head_sha"]
                    or not isinstance(metadata["repository"], str)
                    or metadata["repository"].lower() != intent["repository"].lower()
                    or not isinstance(nested["base"]["repo"]["full_name"], str)
                    or nested["base"]["repo"]["full_name"].lower() != intent["repository"].lower()):
                issues.append(AuditIssue("capture_identity_disagrees", "metadata.json",
                                         "difference"))
    except _INVALID:
        issues.append(AuditIssue("capture_unreadable", "metadata.json", "incomplete"))
    return tuple(issues)


def inspect_posting(run_dir: Path) -> PostingAudit:
    """Validate original recorded intent and receipts, never current verdict bytes.

    This proves consistency of local recorded evidence, not authentic execution or
    continued remote state. Missing optional capture artifacts do not remove the
    original digest; changed captures are separate diagnostic differences.
    """
    try:
        journal = _present(run_dir / "posting.json")
        payload_present = _present(run_dir / "review-payload.json")
        if not journal and not payload_present:
            if _present(run_dir / "escalation.json") or _present(run_dir / "reviewer-request.json"):
                return PostingAudit("invalid", None, "invalid", (
                    AuditIssue("request_provenance_invalid", "escalation.json", "invalid"),
                ))
            return PostingAudit("absent", None, "absent", ())
        if not journal or not payload_present:
            raise ReplayError("posting: incomplete original review pair")
        state = _document(run_dir, "posting.json")
        intent, ref = _intent(state)
        payload = _payload(run_dir, intent)
        review = (_saved_receipt(state["receipt"], ref, payload)
                  if state["status"] == "posted" else None)
    except _INVALID:
        return PostingAudit("invalid", None, "absent", (
            AuditIssue("posting_provenance_invalid", "posting.json", "invalid"),
        ))
    try:
        request_status = _request_status(run_dir, intent, payload, review)
    except _INVALID:
        return PostingAudit("invalid", None, "invalid", (
            AuditIssue("request_provenance_invalid", "escalation.json", "invalid"),
        ))
    issues = _capture_issues(run_dir, intent)
    if review is None:
        return PostingAudit(state["status"], None, request_status, issues + (
            AuditIssue("review_not_posted", "posting.status", "incomplete"),
        ))
    return PostingAudit("posted", intent["verdict_sha256"], request_status, issues)
