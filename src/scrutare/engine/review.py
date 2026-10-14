"""Capture, execute and deliver one fresh review with durable terminal evidence."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scrutare import __version__
from scrutare.config import ReviewConfig
from scrutare.engine.github import GitHubClient, GitHubError, PullRequestRef
from scrutare.engine.ingestion import ingest_pr
from scrutare.engine.nare_session import SessionRuntimeError
from scrutare.engine.review_inputs import ReviewInputError
from scrutare.engine.session_artifacts import SessionArtifactError, write_owned_bytes
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.engine.strategy import StrategyNotImplementedError, run_review
from scrutare.findings import Verdict
from scrutare.poster import (
    PostedReply,
    PostedReview,
    PostingError,
    ReviewClient,
    ReviewerRequestReceipt,
    post_escalation,
    post_replies,
    post_review,
)
from scrutare.poster.errors import PostingInterruptedError
from scrutare.provenance import ProvenanceError, write_artifact_manifest


class ReviewRunError(Exception):
    """Safe failure with retained run identity and explicit delivery confirmation."""

    def __init__(self, message: str, *, run_dir: Path | None = None,
                 confirmed_posting: bool = False) -> None:
        if confirmed_posting:
            message = f"Review delivery was confirmed; {message}"
        super().__init__(message)
        self.run_dir = run_dir
        self.confirmed_posting = confirmed_posting


class ReviewInterruptedError(ReviewRunError):
    """Execution was interrupted after the engine finished child cleanup."""


@dataclass(frozen=True)
class ReviewRunResult:
    """Completed delivery whose exact JSON and provenance have both been installed."""

    run_dir: Path
    head_sha: str
    verdict: str
    rule: str
    panel_status: str
    usage: TokenUsage
    accounting_complete: bool
    review: PostedReview | None
    reviewer_request: ReviewerRequestReceipt | None = None
    # None unless replies are enabled. A replies-only run that found nothing to
    # reassess posts no review and reports `unchanged`.
    replies: tuple[PostedReply, ...] | None = None
    # Present only for an opt-in phase overrun fallback, so callers need not read verdict.json.
    degraded: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": 1, "scrutare_version": __version__,
            "status": "posted" if self.review is not None else "unchanged",
            "run_dir": str(self.run_dir), "head_sha": self.head_sha,
            "verdict": self.verdict, "rule": self.rule, "panel_status": self.panel_status,
            "usage": self.usage.to_dict(), "accounting_complete": self.accounting_complete,
        }
        if self.review is not None:
            data["review"] = asdict(self.review)
        if self.replies is not None:
            data["replies"] = [
                {"thread": reply.thread, "comment_id": reply.comment_id,
                 "disposition": reply.disposition, "status": reply.status,
                 "html_url": reply.html_url} for reply in self.replies]
        if self.degraded is not None:
            data["degraded"] = dict(self.degraded)
        if self.reviewer_request is not None:
            data["reviewer_request"] = {
                "reviewers": list(self.reviewer_request.reviewers),
                "provenance": self.reviewer_request.provenance,
            }
        return data

    def to_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                           indent=2, allow_nan=False) + "\n").encode("utf-8")


def preflight_replies_only(config: ReviewConfig, replies_only: bool) -> None:
    """Refuse a replies-only run the configuration has not opted into."""
    if replies_only and not config.replies.enabled:
        raise ReviewRunError("--replies-only requires replies.enabled in the configuration.")


def preflight_review(config: ReviewConfig, runtime: NareRuntime) -> None:
    """Reject unsupported strategies or absent executables before any GitHub access."""
    if config.strategy not in ("panel", "iterative", "debate"):
        raise ReviewRunError(f"Strategy {config.strategy!r} is not yet implemented.")
    if not isinstance(runtime, NareRuntime) or shutil.which(str(runtime.executable)) is None:
        raise ReviewRunError("executable: cannot locate an executable nare runtime")
    # Version and protocol inspection remain authoritative in the engine.


def _unchanged_head(run_dir: Path) -> str | None:
    """The captured head when the engine found nothing that needs a new review."""
    try:
        document = json.loads((run_dir / "replies.json").read_bytes())
        if document["review_required"] is False and isinstance(document["head_sha"], str):
            return str(document["head_sha"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _safe_error(error: Exception) -> str:
    if isinstance(error, (ReviewRunError, GitHubError, ReviewInputError, SessionRuntimeError,
                          SessionArtifactError, ProvenanceError, StrategyNotImplementedError)):
        return str(error)
    if isinstance(error, OSError):
        return "filesystem operation failed; check run directory access and free disk space."
    return "review execution failed; inspect the saved run."


async def review_pr(
    ref: PullRequestRef, config: ReviewConfig, *, config_bytes: bytes,
    runs_root: Path, runtime: NareRuntime, capture_client: GitHubClient | None = None,
    review_client: ReviewClient | None = None,
    analysis_capture: Path | None = None, replies_only: bool = False,
) -> ReviewRunResult:
    """Run once, never reusing captured evidence or inventing a missing verdict.

    With `replies_only`, a run that finds no new head and no trusted contest posts no
    review. Pending threaded responses are still delivered.
    """
    preflight_replies_only(config, replies_only)
    preflight_review(config, runtime)
    run_dir: Path | None = None
    confirmed = False
    manifest_attempted = False
    try:
        run_dir = ingest_pr(capture_client if capture_client is not None else GitHubClient(),
                            ref, runs_root, config_bytes=config_bytes,
                            config_data=config.to_dict(), review_config=config,
                            analysis_capture=analysis_capture).absolute()
        panel = await run_review(run_dir, config, runtime=runtime)
        if (panel.status not in ("complete", "partial") or not panel.accounting_complete
                or not isinstance(panel.verdict, Verdict)):
            raise ReviewRunError("Panel failed to produce a usable verdict; inspect the saved run.")
        verdict = panel.verdict
        request = None
        receipt: PostedReview | None = None
        head = _unchanged_head(run_dir) if replies_only else None
        if head is not None:
            pass  # Same head, no trusted contest: the posted review still stands.
        elif verdict.verdict == "escalated":
            delivery = post_escalation(run_dir, verdict, client=review_client)
            receipt, request = delivery.review, delivery.reviewer_request
        else:
            receipt = post_review(run_dir, verdict, client=review_client)
        confirmed = receipt is not None
        replies = (post_replies(run_dir, config, client=review_client)
                   if config.replies.enabled else None)
        # Deliver pending native SIGINT cancellation while this run and receipt are owned.
        await asyncio.sleep(0)
        result = ReviewRunResult(run_dir, receipt.commit_id if receipt is not None else str(head),
                                 verdict.verdict, verdict.rule,
                                 panel.status, panel.usage, panel.accounting_complete,
                                 receipt, request, replies,
                                 verdict.degradation.to_dict()
                                 if verdict.degradation is not None else None)
        write_owned_bytes(run_dir / "result.json", result.to_bytes(),
                          prepared_root=run_dir / "review-inputs")
        await asyncio.sleep(0)
        manifest_attempted = True
        write_artifact_manifest(run_dir)
        await asyncio.sleep(0)
        return result
    except (asyncio.CancelledError, KeyboardInterrupt) as error:
        confirmed = confirmed or isinstance(error, PostingInterruptedError)
        failure: ReviewRunError = ReviewInterruptedError(
            "review interrupted.", run_dir=run_dir, confirmed_posting=confirmed)
        raise failure from error
    except Exception as error:
        confirmed = confirmed or (isinstance(error, PostingError) and error.confirmed_posting)
        # A synchronous failure may coincide with SIGINT, including a rejected second stage.
        try:
            await asyncio.sleep(0)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise ReviewInterruptedError(
                "review interrupted.", run_dir=run_dir, confirmed_posting=confirmed) from None
        raise ReviewRunError(_safe_error(error), run_dir=run_dir,
                             confirmed_posting=confirmed) from None
    finally:
        if run_dir is not None and not manifest_attempted:
            # Awaited engine failure/cancellation guarantees child cleanup has quiesced.
            # Posting is synchronous and has released its lock before reaching here.
            try:
                try:
                    write_artifact_manifest(run_dir)
                except (OSError, ProvenanceError):
                    pass  # A secondary snapshot failure must not hide the primary diagnostic.
                await asyncio.sleep(0)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise ReviewInterruptedError(
                    "review interrupted.", run_dir=run_dir, confirmed_posting=confirmed) from None
