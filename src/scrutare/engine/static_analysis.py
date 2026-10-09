"""Normalize bounded captured Ruff data; this adapter never executes a tool."""

from __future__ import annotations

import json
import math
import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any

from scrutare.config import ReviewConfig
from scrutare.engine.repository_context import context_contents, encoded

MAX_CAPTURE_BYTES = 1048576
MAX_DIAGNOSTICS = 1000
MAX_PREPARED_BYTES = 1048576
MANIFEST = "static-analysis.json"


def read_capture(path: Path) -> bytes:
    """Bound regular no-follow evidence reads without disclosing caller paths."""
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Static analysis requires unlinked regular capture data.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Static analysis requires regular capture data.")
        data = stream.read(MAX_CAPTURE_BYTES + 1)
    if len(data) > MAX_CAPTURE_BYTES:
        raise ValueError("Static analysis capture exceeds its byte bound.")
    return data


def _json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate analysis JSON key.")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError("Nonfinite analysis JSON value.")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def _text(value: object, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ValueError("Invalid bounded analysis text.")
    return value


def _path(value: object, *, absolute: bool = False) -> str:
    path = _text(value)
    parts = path[1:].split("/") if absolute else path.split("/")
    if (
        path.startswith("/") != absolute
        or "\\" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError("Unsafe analysis path.")
    return path


def _position(value: object) -> tuple[int, int]:
    if not isinstance(value, dict) or set(value) != {"row", "column"}:
        raise ValueError("Invalid diagnostic position.")
    row, column = value["row"], value["column"]
    if (
        type(row) is not int
        or type(column) is not int
        or not 0 < row <= 1000000
        or not (0 < column <= 1000000)
    ):
        raise ValueError("Invalid diagnostic position.")
    return row, column


def _diagnostic(value: object, root: str) -> tuple[dict[str, Any], str]:
    required = {"code", "message", "filename", "location", "end_location"}
    optional = {"fix", "url", "cell", "noqa_row"}
    if not isinstance(value, dict) or not required <= set(value) <= required | optional:
        raise ValueError("Invalid Ruff diagnostic fields.")
    _text(value["code"], 64)
    _text(value["message"])
    filename = _text(value["filename"])
    if filename.startswith("/"):
        _path(filename, absolute=True)
        if not filename.startswith(root + "/"):
            raise ValueError("Diagnostic escapes declared source root.")
        filename = filename[len(root) + 1 :]
    path = _path(filename)
    start, end = _position(value["location"]), _position(value["end_location"])
    if end < start:
        raise ValueError("Invalid diagnostic range.")
    for field in ("cell", "noqa_row"):
        item = value.get(field)
        if item is not None and (type(item) is not int or not 0 < item <= 1000000):
            raise ValueError("Invalid Ruff optional position.")
    if value.get("url") is not None:
        _text(value["url"], 2048)
    fix = value.get("fix")
    if fix is not None:
        if not isinstance(fix, dict) or set(fix) != {"applicability", "message", "edits"}:
            raise ValueError("Invalid Ruff fix metadata.")
        if fix["applicability"] not in ("safe", "unsafe", "displayonly"):
            raise ValueError("Invalid Ruff fix applicability.")
        _text(fix["message"])
        if not isinstance(fix["edits"], list) or len(fix["edits"]) > 100:
            raise ValueError("Invalid Ruff fix edits.")
        for edit in fix["edits"]:
            if not isinstance(edit, dict) or set(edit) != {"content", "location", "end_location"}:
                raise ValueError("Invalid Ruff fix edit.")
            if not isinstance(edit["content"], str) or len(edit["content"]) > 4096:
                raise ValueError("Invalid Ruff replacement bound.")
            if _position(edit["end_location"]) < _position(edit["location"]):
                raise ValueError("Invalid Ruff fix range.")
    return value, path


def _normalize(run: Path, config: ReviewConfig, data: bytes | None) -> dict[str, Any]:
    contents = context_contents(run, config)
    context = _json(contents["repository-context.json"])
    revision = context["revisions"]["head"]["sha"]
    result: dict[str, Any] = {
        "schema_version": 1,
        "revision": revision,
        "tool": {"name": "ruff", "version": "0.16.10"},
        "status": "unavailable",
        "source_sha256": None,
        "tool_runtime_seconds": None,
        "claim_truth": "not_assessed",
        "diagnostics": [],
        "omitted": {"not_captured": 0},
        "provenance": "caller_supplied_unsigned_capture",
    }
    if data is None:
        return result
    document = _json(data)
    fields = {
        "schema_version",
        "revision",
        "tool",
        "status",
        "duration_seconds",
        "source_root",
        "diagnostics",
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["revision"] != revision
            or document["tool"] != {"name": "ruff", "version": "0.16.10"}
            or document["status"] not in ("complete", "failed", "truncated", "unavailable")
        )
    ):
        raise ValueError("Unsupported analysis capture identity or status.")
    duration = document["duration_seconds"]
    if (
        type(duration) not in (int, float)
        or not math.isfinite(duration)
        or not 0 <= duration <= 86400
    ):
        raise ValueError("Invalid tool runtime.")
    root = _path(document["source_root"], absolute=True)
    values = document["diagnostics"]
    if not isinstance(values, list) or len(values) > MAX_DIAGNOSTICS:
        raise ValueError("Analysis diagnostic count exceeds its bound.")
    by_path = {
        e["path"]: e
        for e in context["entries"]
        if e["side"] == "head" and e["status"] == "captured"
    }
    for item in values:
        diagnostic, path = _diagnostic(item, root)
        entry = by_path.get(path)
        if entry is None:
            result["omitted"]["not_captured"] += 1
            continue
        text = contents[entry["artifact"]].decode("utf-8")
        lines = text.splitlines()
        # Ruff permits a final empty line and end-of-line columns.
        if text.endswith("\n"):
            lines.append("")
        for row, column in (
            _position(diagnostic["location"]),
            _position(diagnostic["end_location"]),
        ):
            if row > len(lines) or column > len(lines[row - 1]) + 1:
                raise ValueError("Diagnostic range does not bind captured source bytes.")
        result["diagnostics"].append(
            {
                "path": path,
                "side": "head",
                "revision": revision,
                "sha256": entry["sha256"],
                "rule": diagnostic["code"],
                "message": diagnostic["message"],
                "location": diagnostic["location"],
                "end_location": diagnostic["end_location"],
            }
        )
    result.update(
        status=document["status"],
        source_sha256=sha256(data).hexdigest(),
        tool_runtime_seconds=duration,
    )
    while len(encoded(result)) > MAX_PREPARED_BYTES and result["diagnostics"]:
        result["diagnostics"].pop()
        result["omitted"]["byte_limit"] = result["omitted"].get("byte_limit", 0) + 1
        if result["status"] == "complete":
            result["status"] = "truncated"
    if len(encoded(result)) > MAX_PREPARED_BYTES:
        raise ValueError("Analysis manifest exceeds its byte bound.")
    return result


def capture_static_analysis(
    run: Path, config: ReviewConfig, capture_path: Path | None = None, *,
    capture_bytes: bytes | None = None
) -> None:
    """Retain only validated original envelopes in private producer-owned storage."""
    if not config.analysis.enabled:
        if capture_path is not None or capture_bytes is not None:
            raise ValueError("Explicit analysis capture requires analysis.enabled.")
        return
    if capture_path is not None and capture_bytes is not None:
        raise ValueError("Supply exactly one analysis capture source.")
    data = read_capture(capture_path) if capture_path is not None else capture_bytes
    if data is not None and (not isinstance(data, bytes) or len(data) > MAX_CAPTURE_BYTES):
        raise ValueError("Invalid bounded analysis capture bytes.")
    _normalize(run, config, data)
    directory = run / "static-analysis"
    directory.mkdir(mode=0o700)
    if data is not None:
        with (directory / "source.json").open("xb") as stream:
            stream.write(data)
        (directory / "source.json").chmod(0o600)


def static_analysis_contents(run: Path, config: ReviewConfig) -> dict[str, bytes]:
    """Recompute normalized data from bounded raw capture and verified context."""
    if not config.analysis.enabled:
        return {}
    directory = run / "static-analysis"
    if directory.is_symlink():
        raise ValueError("Unsafe analysis capture directory.")
    data = None
    if directory.exists():
        names = {p.name for p in directory.iterdir()}
        if names not in (set(), {"source.json"}):
            raise ValueError("Unexpected analysis capture inventory.")
        if names:
            data = read_capture(directory / "source.json")
    return {MANIFEST: encoded(_normalize(run, config, data))}


def analysis_dependencies(root: Path) -> list[dict[str, Any]]:
    """Tool status/content changes invalidate every carried finding conservatively."""
    path = root / MANIFEST
    if not path.exists():
        return []
    data = read_capture(path)
    value = _json(data)
    return [
        {
            "side": "head",
            "path": MANIFEST,
            "status": value["status"],
            "blob_sha": None,
            "sha256": sha256(data).hexdigest(),
            "mode": None,
        }
    ]
