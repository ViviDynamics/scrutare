"""Named, versioned configuration profiles (#133).

A profile fills opt-in options a file leaves unset with fixed values. A version's values
never change: a new value is a new version, so a file that names ``self-hosted@1`` keeps
its behavior, and replay of its configuration resolves the same values. The evidence
behind each value is in ``docs/self-hosted.md``.
"""

from dataclasses import dataclass
from typing import Literal

# When a value applies, judged on the file's own settings before anything is filled:
# always; with evidence v2; with evidence v2 and the post-session citation check (the
# in-session check excludes the correction turns); with independent assessment enabled.
Condition = Literal["always", "evidence_v2", "post_session_v2", "assessment"]


@dataclass(frozen=True)
class ProfileValue:
    path: str
    value: object
    when: Condition = "always"


# Profiles are refused on any rail that is not openai: their rail values include options
# nare offers on its openai rail only.
PROFILES: dict[str, dict[int, tuple[ProfileValue, ...]]] = {
    "self-hosted": {
        1: (
            ProfileValue("models.default.structured_output", "json_schema"),
            ProfileValue("models.default.max_output_tokens", 20000),
            ProfileValue("models.default.proxy_num_retries", 0),
            ProfileValue("models.preflight.route", True),
            ProfileValue("context.line_counts", True, "evidence_v2"),
            ProfileValue("findings.protocol_correction", True, "post_session_v2"),
            ProfileValue("findings.assessment.tokens", 50000, "assessment"),
        ),
    },
}

# The default rail's fields a profile fills; overrides inherit them like any other.
RAIL_PREFIX = "models.default."


def profile_values(reference: str) -> tuple[ProfileValue, ...]:
    """The values of one known ``NAME@VERSION``; the reference must already be validated."""
    name, _, version = reference.partition("@")
    return PROFILES[name][int(version)]
