"""Review a pull request or audit stored verdict artifacts offline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from scrutare import __version__
from scrutare.config import ConfigError, parse_config
from scrutare.engine.github import GitHubError, resolve_pr
from scrutare.engine.review import (
    ReviewInterruptedError,
    ReviewRunError,
    preflight_review,
    review_pr,
)
from scrutare.engine.session_models import NareRuntime
from scrutare.replay import ReplayError, replay_run


def main(argv: Sequence[str] | None = None) -> int:
    """Emit completed review or replay JSON and return the command's process exit code."""
    parser = argparse.ArgumentParser(prog="scrutare", description=__doc__)
    parser.add_argument("--version", action="version", version=f"scrutare {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser(
        "review", help="review a PR and post the captured-head verdict",
        description="Capture, review and post a verdict with durable local artifacts.",
    )
    review.add_argument("--pr", required=True, help="GitHub PR URL or number in the current repo")
    review.add_argument(
        "--config",
        type=Path,
        default=Path("scrutare.yaml"),
        help="validate YAML settings (default: scrutare.yaml); models.default.model is required",
    )
    review.add_argument(
        "--nare-executable", type=Path, default=Path("nare"), metavar="PATH",
        help="separately installed nare executable (default: nare on PATH)",
    )
    replay = commands.add_parser(
        "replay", help="audit a stored verdict offline; no model or network",
        description="Recompute a stored verdict and compare saved and recorded posted identity.",
    )
    replay.add_argument("dir", type=Path, help="directory containing captured review artifacts")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1

    if args.command == "replay":
        try:
            replay_result = replay_run(args.dir)
        except ReplayError as exc:
            print(f"scrutare: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(replay_result.to_dict()))
        return replay_result.exit_code

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
    except ConfigError as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        return 1

    try:
        runtime = NareRuntime(args.nare_executable)
        preflight_review(review_config, runtime)
        ref = resolve_pr(args.pr)
        result = asyncio.run(review_pr(
            ref, review_config, config_bytes=config_bytes,
            runs_root=Path(".scrutare/runs"), runtime=runtime,
        ))
    except ReviewRunError as exc:
        location = f" Run: {exc.run_dir}" if exc.run_dir is not None else ""
        print(f"scrutare: {exc}{location}", file=sys.stderr)
        return 130 if isinstance(exc, ReviewInterruptedError) else 1
    except GitHubError as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scrutare: review interrupted.", file=sys.stderr)
        return 130

    # This exact document was installed before the manifest and before any success output.
    sys.stdout.buffer.write(result.to_bytes())
    return 0
