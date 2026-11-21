"""Review a pull request, audit stored verdict artifacts, or inspect a configuration."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from scrutare import __version__
from scrutare.config import ConfigError, explain_config, parse_config
from scrutare.engine.github import GitHubError, resolve_pr
from scrutare.engine.preflight import PreflightError, not_started, run_preflight
from scrutare.engine.review import (
    ReviewInterruptedError,
    ReviewRunError,
    ReviewSupersededError,
    preflight_replies_only,
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
    review.add_argument("--analysis-capture", type=Path, metavar="PATH",
                        help="explicit captured Ruff evidence; requires analysis.enabled")
    review.add_argument("--replies-only", action="store_true",
                        help="post a review only for a new head or a trusted reply that "
                             "contests a finding; requires replies.enabled")
    review.add_argument("--whole-review-retry", action="store_true",
                        help="retry one failed whole review before posting, on captured inputs")
    review.add_argument("--expected-head", help="supersede if capture differs from this head")
    review.add_argument("--skip-preflight", action="store_true",
                        help="the caller already ran the configured models.preflight "
                             "checks (the App worker does)")
    replay = commands.add_parser(
        "replay", help="audit a stored verdict offline; no model or network",
        description="Recompute a stored verdict and compare saved and recorded posted identity.",
    )
    replay.add_argument("dir", type=Path, help="directory containing captured review artifacts")
    settings = commands.add_parser(
        "config", help="inspect a configuration offline; no model, GitHub or network",
        description="Read-only configuration commands. Nothing is written or sent.",
    )
    actions = settings.add_subparsers(dest="action", required=True)
    show = actions.add_parser(
        "show", help="print the resolved configuration and each profile option's source",
        description="Print the canonical configuration as a review would record it, and "
                    "for each option a profile covers, its value and whether it came from "
                    "the profile or the file.",
    )
    show.add_argument("--config", type=Path, default=Path("scrutare.yaml"),
                      help="YAML settings to resolve (default: scrutare.yaml)")
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

    config_bytes = _read_config(args.config)
    if config_bytes is None:
        return 1
    if args.command == "config":
        try:
            explained = explain_config(config_bytes)
        except ConfigError as exc:
            print(f"scrutare: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(explained, indent=2, allow_nan=False))
        return 0
    try:
        review_config = parse_config(config_bytes)
    except ConfigError as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        return 1

    try:
        runtime = NareRuntime(args.nare_executable)
        preflight_replies_only(review_config, args.replies_only)
        preflight_review(review_config, runtime)
        if not args.skip_preflight:
            # Opt-in. Before any GitHub access or capture, so a route that cannot answer
            # claims no run directory, charges no budget and posts nothing.
            asyncio.run(run_preflight(review_config, runtime))
        ref = resolve_pr(args.pr)
        result = asyncio.run(review_pr(
            ref, review_config, config_bytes=config_bytes,
            runs_root=Path(".scrutare/runs"), runtime=runtime,
            analysis_capture=args.analysis_capture, replies_only=args.replies_only,
            **({"whole_review_retry": True, "expected_head": args.expected_head}
               if args.whole_review_retry else {}),
        ))
    except PreflightError as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        sys.stdout.write(json.dumps(not_started(exc.report, __version__), sort_keys=True,
                                    indent=2, allow_nan=False) + "\n")
        return 4
    except ReviewSupersededError as exc:
        print(f"scrutare: {exc}", file=sys.stderr)
        return 3
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


def _read_config(config: Path) -> bytes | None:
    """The configuration file's exact bytes, or None after a concise diagnostic."""
    try:
        if not config.is_file():
            raise OSError("not a regular file")
        return config.read_bytes()
    except OSError:
        print(
            "scrutare: cannot read config file; provide an accessible regular file.",
            file=sys.stderr,
        )
        return None
