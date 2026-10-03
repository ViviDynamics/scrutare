"""Capture coherent PR inputs for later review stages without posting anything."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scrutare import __version__
from scrutare.engine.github import GitHubClient, GitHubError, PullRequestRef, assert_pr_open


def check_pr_open(client: GitHubClient, ref: PullRequestRef) -> None:
    """Recheck lifecycle before later stages, allowing a push after capture."""
    assert_pr_open(client.get_pr(ref))


def ingest_pr(
    client: GitHubClient,
    ref: PullRequestRef,
    runs_root: Path,
    config_path: Path | None = None,
) -> Path:
    """Persist inputs only when two surrounding metadata reads agree on the head."""
    for _ in range(3):
        before = client.get_pr(ref)
        assert_pr_open(before)
        diff = client.get_diff(ref)
        inputs: dict[str, Any] = {
            "files.json": client.get_files(ref),
            "reviews.json": client.get_reviews(ref),
            "comments.json": client.get_comments(ref),
            "review_comments.json": client.get_review_comments(ref),
        }
        captured = client.get_pr(ref)
        assert_pr_open(captured)
        if before["head"]["sha"] == captured["head"]["sha"]:
            break
    else:
        raise GitHubError("Pull request head changed during all three capture attempts; retry.")

    metadata = {
        "schema_version": 1,
        "scrutare_version": __version__,
        "head_sha": captured["head"]["sha"],
        "repository": f"{ref.owner}/{ref.repo}",
        "pr_number": ref.number,
        "pull_request": captured,
        "status": "ingested",
    }
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=runs_root))
    try:
        (run_dir / "diff.patch").write_text(diff, encoding="utf-8")
        for filename, data in inputs.items():
            (run_dir / filename).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if config_path is not None:
            (run_dir / "config.yaml").write_bytes(config_path.read_bytes())
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(run_dir)
        raise
    return run_dir
