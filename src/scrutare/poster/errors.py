"""Safe domain errors shared by payload rendering and review posting."""

from scrutare.engine.github import GitHubError


class PostingError(GitHubError):
    """Review input or posting failed with a safe, actionable explanation."""
