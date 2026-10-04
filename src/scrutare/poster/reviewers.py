"""Pure validation and stable normalization of configured human reviewer logins."""

import re

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
