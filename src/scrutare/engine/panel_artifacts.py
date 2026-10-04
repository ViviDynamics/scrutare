"""Exclusive panel evidence installation; verdict is the last individual artifact."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scrutare.engine.session_artifacts import SessionArtifactError, _directory, write_owned_bytes
from scrutare.findings.verdict import Verdict

_PRIOR_EVIDENCE = (
    "sessions", "fanout.json", "panel.json", "findings.json", "verdict.json",
    "posting.json", "review-payload.json", "escalation.json", "reviewer-request.json",
    ".posting.lock",
)


def preflight_panel(run_dir: Path) -> None:
    """Reject any prior execution/delivery entry, including dangling links and nonfiles."""
    try:
        path = run_dir.absolute()
        if ".." in path.parts:
            raise SessionArtifactError("Cannot reserve panel: unsafe run directory.")
        with _directory(path) as parent:
            for name in _PRIOR_EVIDENCE:
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise SessionArtifactError("Cannot reserve panel: existing execution artifact.")
    except OSError:
        raise SessionArtifactError("Cannot reserve panel: unsafe run directory.") from None


def encode_panel(document: object) -> bytes:
    """Serialize completely before installing any final evidence."""
    try:
        return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise SessionArtifactError("Cannot serialize panel evidence.") from None


def publish_panel(run_dir: Path, prepared_root: Path, document: object,
                  verdict: Verdict | None) -> None:
    """Install serialized findings, panel, then exact verdict bytes, each exclusively.

    This is not a multi-file transaction. A failed install leaves preceding evidence
    intact and propagates the error; retrying requires a fresh captured run.
    """
    panel_bytes = encode_panel(document)
    findings_bytes = (encode_panel([finding.to_dict() for finding in verdict.findings])
                      if verdict is not None else None)
    verdict_bytes = verdict.to_bytes() if verdict is not None else None
    if findings_bytes is not None:
        write_owned_bytes(run_dir / "findings.json", findings_bytes, prepared_root=prepared_root)
    write_owned_bytes(run_dir / "panel.json", panel_bytes, prepared_root=prepared_root)
    if verdict_bytes is not None:
        write_owned_bytes(run_dir / "verdict.json", verdict_bytes, prepared_root=prepared_root)
