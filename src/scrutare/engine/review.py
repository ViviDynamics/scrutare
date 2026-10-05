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
    PostedReview,
    PostingError,
    ReviewClient,
    ReviewerRequestReceipt,
    post_escalation,
    post_review,
)
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
    review: PostedReview
    reviewer_request: ReviewerRequestReceipt | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schema_version": 1, "scrutare_version": __version__, "status": "posted",
            "run_dir": str(self.run_dir), "head_sha": self.head_sha,
            "verdict": self.verdict, "rule": self.rule, "panel_status": self.panel_status,
            "usage": self.usage.to_dict(), "accounting_complete": self.accounting_complete,
            "review": asdict(self.review),
        }
        if self.reviewer_request is not None:
            data["reviewer_request"] = {
                "reviewers": list(self.reviewer_request.reviewers),
                "provenance": self.reviewer_request.provenance,
            }
        return data

    def to_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                           indent=2, allow_nan=False) + "\n").encode("utf-8")


def preflight_review(config: ReviewConfig, runtime: NareRuntime) -> None:
    """Reject unsupported strategies or absent executables before any GitHub access."""
    if config.strategy != "panel":
        raise ReviewRunError(f"Strategy {config.strategy!r} is not yet implemented.")
    if not isinstance(runtime, NareRuntime) or shutil.which(str(runtime.executable)) is None:
        raise ReviewRunError("executable: cannot locate an executable nare runtime")
    # Version and protocol inspection remain authoritative in the engine.


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
) -> ReviewRunResult:
    """Run once, never reusing captured evidence or inventing a missing verdict."""
    preflight_review(config, runtime)
    run_dir: Path | None = None
    confirmed = False
    manifest_attempted = False
    try:
        run_dir = ingest_pr(capture_client if capture_client is not None else GitHubClient(),
                            ref, runs_root, config_bytes=config_bytes,
                            config_data=config.to_dict(), review_config=config)
        panel = await run_review(run_dir, config, runtime=runtime)
        if (panel.status not in ("complete", "partial") or not panel.accounting_complete
                or not isinstance(panel.verdict, Verdict)):
            raise ReviewRunError("Panel failed to produce a usable verdict; inspect the saved run.")
        verdict = panel.verdict
        request = None
        if verdict.exhaustion is not None:
            delivery = post_escalation(run_dir, verdict, client=review_client)
            receipt, request = delivery.review, delivery.reviewer_request
        else:
            receipt = post_review(run_dir, verdict, client=review_client)
        confirmed = True
        result = ReviewRunResult(run_dir, receipt.commit_id, verdict.verdict, verdict.rule,
                                 panel.status, panel.usage, panel.accounting_complete,
                                 receipt, request)
        write_owned_bytes(run_dir / "result.json", result.to_bytes(),
                          prepared_root=run_dir / "review-inputs")
        manifest_attempted = True
        write_artifact_manifest(run_dir)
        return result
    except (asyncio.CancelledError, KeyboardInterrupt) as error:
        failure: ReviewRunError = ReviewInterruptedError(
            "review interrupted.", run_dir=run_dir, confirmed_posting=confirmed)
        raise failure from error
    except Exception as error:
        confirmed = confirmed or (isinstance(error, PostingError) and error.confirmed_posting)
        raise ReviewRunError(_safe_error(error), run_dir=run_dir,
                             confirmed_posting=confirmed) from None
    finally:
        if run_dir is not None and not manifest_attempted:
            # Awaited engine failure/cancellation guarantees child cleanup has quiesced.
            # Posting is synchronous and has released its lock before reaching here.
            try:
                write_artifact_manifest(run_dir)
            except (OSError, ProvenanceError):
                pass  # A secondary snapshot failure must not hide the primary diagnostic.
