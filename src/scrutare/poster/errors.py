"""Safe domain errors shared by payload rendering and review posting."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrutare.poster.client import PostedReview

from scrutare.engine.github import GitHubError


class PostingError(GitHubError):
    """Review input or posting failed with a safe, actionable explanation."""

    def __init__(self, message: str, *, confirmed_review: PostedReview | None = None) -> None:
        super().__init__(message)
        self.confirmed_review = confirmed_review

    @property
    def confirmed_posting(self) -> bool:
        return self.confirmed_review is not None


class PostingInterruptedError(KeyboardInterrupt):
    """Synchronous interruption after a validated review receipt was obtained."""

    def __init__(self, confirmed_review: PostedReview) -> None:
        super().__init__("Review interrupted after confirmed delivery.")
        self.confirmed_review = confirmed_review


class PostingRejected(PostingError):
    """An explicit HTTP rejection, or a launch proven not to have sent."""

    def __init__(
        self, message: str, *, status: int | None = None, unsent: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.unsent = unsent


class PostingRateLimited(PostingRejected):
    """Explicit throttling rejection with a safe delay in seconds."""

    def __init__(self, *, status: int, retry_after: float) -> None:
        if (
            type(retry_after) not in (int, float)
            or not math.isfinite(retry_after)
            or retry_after < 0
        ):
            raise PostingError("Rate-limit retry timing must be finite and nonnegative.")
        super().__init__(
            "GitHub rate limited the review; wait before checking and retrying.", status=status,
        )
        self.retry_after = retry_after


class PostingUncertain(PostingError):
    """The review may have been sent; reconcile before any further mutation."""
