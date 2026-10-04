"""Pure validation and stable normalization of configured human reviewer logins."""

import re
from dataclasses import dataclass
from typing import Literal

from scrutare.poster.errors import PostingError


def normalize_human_reviewers(values: tuple[str, ...]) -> tuple[str, ...]:
    """Require safe human login syntax and keep the first case-insensitive occurrence."""
    error = "github.human_reviewers: expected a tuple of valid human GitHub logins"
    if not isinstance(values, tuple):
        raise PostingError(error)
    normalized = []
    seen = set()
    for value in values:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 39
            or re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", value) is None
        ):
            raise PostingError(error)
        folded = value.lower()
        if folded not in seen:
            seen.add(folded)
            normalized.append(value)
    return tuple(normalized)


@dataclass(frozen=True)
class ReviewerRequestReceipt:
    """Confirmed targets with provenance that does not claim causal ownership."""

    reviewers: tuple[str, ...]
    provenance: Literal["post_response", "observed_requested", "no_targets"]

    def __post_init__(self) -> None:
        normalized = normalize_human_reviewers(self.reviewers)
        if (
            normalized != self.reviewers
            or self.provenance not in ("post_response", "observed_requested", "no_targets")
            or (not self.reviewers) != (self.provenance == "no_targets")
        ):
            raise PostingError("Use a normalized reviewer receipt with valid delivery provenance.")
