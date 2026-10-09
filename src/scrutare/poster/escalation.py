"""Durable human escalation after a confirmed captured-head COMMENT review."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import sleep
from typing import Any, Literal

from scrutare.engine.github import GitHubError, PullRequestRef, assert_pr_open, resolve_pr
from scrutare.findings import Verdict
from scrutare.poster import posting
from scrutare.poster.client import PostedReview, ReviewClient
from scrutare.poster.errors import (
    PostingError,
    PostingInterruptedError,
    PostingRateLimited,
    PostingRejected,
    PostingUncertain,
)
from scrutare.poster.journal import atomic_write, canonical, read_json, run_lock
from scrutare.poster.reviewers import ReviewerRequestReceipt, normalize_human_reviewers

_UNCERTAIN = ("Reviewer request delivery is uncertain; "
              "reconcile the saved run before requesting again.")
_INVALID = "Escalation artifacts are invalid or differ from evidence; inspect the saved run."
_STAGE_FIELDS = {"status", "attempts", "receipt", "failure", "retry_at", "http_status"}


@dataclass(frozen=True)
class PostedEscalation:
    """Both delivery stages are confirmed, or the request stage had no targets."""

    review: PostedReview
    reviewer_request: ReviewerRequestReceipt

    @property
    def verdict(self) -> Literal["escalated"]:
        return "escalated"

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "review": asdict(self.review),
                "reviewer_request": _receipt_dict(self.reviewer_request)}


def _receipt_dict(receipt: ReviewerRequestReceipt) -> dict[str, Any]:
    return {"reviewers": list(receipt.reviewers), "provenance": receipt.provenance}


def _saved_request(value: Any, reviewers: tuple[str, ...]) -> ReviewerRequestReceipt:
    if (not isinstance(value, dict) or set(value) != {"reviewers", "provenance"}
            or not isinstance(value["reviewers"], list)):
        raise PostingError(_INVALID)
    receipt = ReviewerRequestReceipt(tuple(value["reviewers"]), value["provenance"])
    if receipt.reviewers != reviewers:
        raise PostingError(_INVALID)
    return receipt


def _intent(
    review_intent: dict[str, Any], review: PostedReview, reviewers: tuple[str, ...],
) -> dict[str, Any]:
    return review_intent | {
        "review_id": review.review_id,
        "review_receipt_sha256": posting._hash(canonical(asdict(review))),
        "reviewers": list(reviewers),
        "request_sha256": posting._hash(canonical({"reviewers": list(reviewers)})),
    }


def _validate_existing(
    run_dir: Path, review_intent: dict[str, Any], review: PostedReview | None,
    reviewers: tuple[str, ...], *, escalated: bool,
) -> dict[str, Any] | None:
    """Read-only cross-stage check used before either entrypoint touches the network."""
    path = run_dir / "escalation.json"
    request_path = run_dir / "reviewer-request.json"
    if not path.exists() and not request_path.exists():
        return None
    if not path.exists() or not request_path.exists() or not escalated or review is None:
        raise PostingError(_INVALID)
    state = read_json(path)
    intent = _intent(review_intent, review, reviewers)
    if not isinstance(state, dict) or type(state.get("review_id")) is not int:
        raise PostingError(_INVALID)
    try:
        if request_path.read_bytes() != canonical({"reviewers": list(reviewers)}):
            raise PostingError(_INVALID)
    except OSError:
        raise PostingError(_INVALID) from None
    if state.get("status") == "skipped":
        posting._validate_state(state | {"status": "prepared", "receipt": None}, intent)
        if reviewers or _saved_request(state.get("receipt"), reviewers).provenance != "no_targets":
            raise PostingError(_INVALID)
    else:
        posting._validate_state(state, intent)
        if not reviewers:
            raise PostingError(_INVALID)
        if state["status"] == "posted":
            _saved_request(state["receipt"], reviewers)
    return state


def _confirm(
    path: Path, state: dict[str, Any], receipt: ReviewerRequestReceipt,
) -> ReviewerRequestReceipt:
    validated = _saved_request(_receipt_dict(receipt), tuple(state["reviewers"]))
    updated = state | {"status": "posted", "receipt": _receipt_dict(validated),
                       "failure": None, "retry_at": None, "http_status": None}
    atomic_write(path, canonical(updated))
    return validated


def _reconcile(
    transport: ReviewClient, ref: PullRequestRef, path: Path, state: dict[str, Any],
) -> ReviewerRequestReceipt:
    state.update(status="unknown", receipt=None, failure=None, retry_at=None, http_status=None)
    atomic_write(path, canonical(state))
    reviewers = tuple(state["reviewers"])
    try:
        observed = normalize_human_reviewers(transport.get_requested_reviewers(ref))
        if not {value.lower() for value in reviewers} <= {value.lower() for value in observed}:
            raise PostingUncertain(_UNCERTAIN)
    except Exception:
        raise PostingUncertain(_UNCERTAIN) from None
    return _confirm(path, state, ReviewerRequestReceipt(reviewers, "observed_requested"))


def _request(
    transport: ReviewClient, ref: PullRequestRef, path: Path, state: dict[str, Any],
    sleeper: Callable[[float], None],
) -> ReviewerRequestReceipt:
    reviewers = tuple(state["reviewers"])
    if state["status"] in ("posted", "skipped"):
        return _saved_request(state["receipt"], reviewers)
    if state["status"] in ("sending", "unknown"):
        return _reconcile(transport, ref, path, state)
    while True:
        posting._wait_retry(state, sleeper)
        assert_pr_open(transport.get_pr(ref))
        posting._begin_attempt(path, state, atomic_write)
        try:
            receipt = transport.request_reviewers(ref, reviewers)
            if (not isinstance(receipt, ReviewerRequestReceipt)
                    or receipt.provenance != "post_response"):
                raise PostingUncertain(_UNCERTAIN)
            _saved_request(_receipt_dict(receipt), reviewers)
        except PostingRateLimited as error:
            if not posting._record_rejection(path, state, error, atomic_write):
                return _reconcile(transport, ref, path, state)
            continue
        except PostingRejected as error:
            posting._record_rejection(path, state, error, atomic_write)
            raise
        except Exception:
            return _reconcile(transport, ref, path, state)
        return _confirm(path, state, receipt)


def post_escalation(
    run_dir: Path, verdict: Verdict, *, client: ReviewClient | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> PostedEscalation:
    """Confirm one COMMENT review and human reviewer requests under one run lock."""
    if not isinstance(verdict, Verdict) or verdict.verdict != "escalated":
        raise PostingError("post_escalation requires a code-derived escalated Verdict.")
    with run_lock(run_dir):
        transport = client if client is not None else ReviewClient(sleeper=sleeper)
        review = posting._post_review_locked(run_dir, verdict, client=transport, sleeper=sleeper)
        try:
            # The review helper validated captured inputs and both stages before any network.
            review_state = read_json(run_dir / "posting.json")
            review_intent = {key: value for key, value in review_state.items()
                             if key not in _STAGE_FIELDS}
            _, _, config, _ = posting._capture(run_dir, verdict)
            reviewers = normalize_human_reviewers(config.github.human_reviewers)
            state = _validate_existing(run_dir, review_intent, review, reviewers, escalated=True)
            path = run_dir / "escalation.json"
            if state is None:
                state = _intent(review_intent, review, reviewers) | {
                    "status": "prepared" if reviewers else "skipped", "attempts": 0,
                    "receipt": (None if reviewers
                                else _receipt_dict(ReviewerRequestReceipt((), "no_targets"))),
                    "failure": None, "retry_at": None, "http_status": None,
                }
                atomic_write(path, canonical(state))
                atomic_write(run_dir / "reviewer-request.json",
                             canonical({"reviewers": list(reviewers)}))
            ref = resolve_pr(str(review_intent["pr_number"]),
                             repository=review_intent["repository"])
            receipt = _request(transport, ref, path, state,
                               sleeper if sleeper is not None else sleep)
            return PostedEscalation(review, receipt)
        except KeyboardInterrupt:
            raise PostingInterruptedError(review) from None
        except PostingError as error:
            error.confirmed_review = review
            raise
        except (OSError, GitHubError) as error:
            message = (str(error) if isinstance(error, GitHubError)
                       else "Cannot persist escalation artifacts; inspect the saved run.")
            raise PostingError(message, confirmed_review=review) from None
