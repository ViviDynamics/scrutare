"""Ingest pull request inputs; review and posting stages are not implemented yet."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from scrutare import __version__
from scrutare.config import ConfigError, parse_config
from scrutare.engine.github import GitHubClient, GitHubError, resolve_pr
from scrutare.engine.ingestion import ingest_pr
from scrutare.engine.review_inputs import ReviewInputError


def main(argv: Sequence[str] | None = None) -> int:
    """Return a process exit code and emit JSON only for successful ingestion."""
    parser = argparse.ArgumentParser(prog="scrutare", description=__doc__)
    parser.add_argument("--version", action="version", version=f"scrutare {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("review", help="capture PR inputs only; no review or posting")
    review.add_argument("--pr", required=True, help="GitHub PR URL or number in the current repo")
    review.add_argument(
        "--config",
        type=Path,
        default=Path("scrutare.yaml"),
        help="validate YAML settings (default: scrutare.yaml); models.default.model is required",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    config: Path = args.config
    try:
        if not config.is_file():
            raise OSError("not a regular file")
        config_bytes = config.read_bytes()
    except OSError:
        print(
            "scrutare: cannot read config file; provide an accessible regular file.",
            file=sys.stderr,
        )
        return 1
    try:
        review_config = parse_config(config_bytes)
        config_data = review_config.to_dict()
    except ConfigError as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        return 1

    try:
        ref = resolve_pr(args.pr)
        run_dir = ingest_pr(
            GitHubClient(),
            ref,
            Path(".scrutare/runs"),
            config_bytes=config_bytes,
            config_data=config_data,
            review_config=review_config,
        )
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    except (GitHubError, ReviewInputError) as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print(
            "scrutare: filesystem operation failed; check config and run directory access.",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "status": "ingested",
                "run_dir": str(run_dir),
                "head_sha": metadata["head_sha"],
                "scrutare_version": __version__,
            }
        )
    )
    return 0
