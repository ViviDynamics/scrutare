"""Exclusive producer inventory of a quiescent run, preserving canonical artifact bytes."""

from __future__ import annotations

import os
import stat
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path

from scrutare import __version__
from scrutare.engine.session_artifacts import _directory, write_owned_json


class ProvenanceError(ValueError):
    """A safe snapshot diagnostic without paths or captured provider content."""


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _unchanged(before: os.stat_result, after: os.stat_result) -> None:
    if _signature(before) != _signature(after):
        raise ProvenanceError("Artifact changed while creating provenance.")


def _file(parent: int, name: str, before: os.stat_result) -> tuple[str, int]:
    # NONBLOCK also prevents a regular-file-to-FIFO race from blocking on open.
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ProvenanceError("Cannot inventory a nonregular artifact.")
        _unchanged(before, opened)
        digest = sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        _unchanged(opened, os.fstat(descriptor))
        _unchanged(opened, os.stat(name, dir_fd=parent, follow_symlinks=False))
        if size != opened.st_size:
            raise ProvenanceError("Artifact changed while creating provenance.")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _inventory(parent: int, prefix: str = "") -> list[dict[str, str | int]]:
    before = os.fstat(parent)
    entries: list[dict[str, str | int]] = []
    for name in sorted(os.listdir(parent)):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        relative = f"{prefix}{name}"
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                _unchanged(info, os.fstat(child))
                entries.extend(_inventory(child, f"{relative}/"))
                _unchanged(info, os.stat(name, dir_fd=parent, follow_symlinks=False))
            finally:
                os.close(child)
        elif not stat.S_ISREG(info.st_mode):
            raise ProvenanceError("Cannot inventory a linked or nonregular artifact.")
        elif (name != ".posting.lock" and not fnmatchcase(name, ".scrutare-*.tmp")
              and not fnmatchcase(name, ".posting-*.tmp")):
            digest, size = _file(parent, name, info)
            entries.append({"path": relative, "sha256": digest, "size_bytes": size})
    _unchanged(before, os.fstat(parent))
    return entries


def write_artifact_manifest(run_dir: Path) -> Path:
    """Exclusively install artifacts.json after execution and posting have quiesced.

    This local byte snapshot asserts neither successful execution nor remote delivery.
    Symlinks and nonregular entries are refused, including excluded staging names.
    Ordinary changes observed during inventory fail; this is not an authenticity proof.
    """
    try:
        run = run_dir.absolute()
        if ".." in run.parts:
            raise ProvenanceError("Unsafe run directory.")
        destination = run / "artifacts.json"
        with _directory(run) as parent:
            try:
                os.stat("artifacts.json", dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ProvenanceError("Provenance destination already exists.")
            before = os.fstat(parent)
            entries = _inventory(parent)
            _unchanged(before, run.stat(follow_symlinks=False))
        entries.sort(key=lambda entry: str(entry["path"]))
        write_owned_json(destination, {
            "schema_version": 1,
            "scrutare_version": __version__,
            "artifacts": entries,
        }, prepared_root=run / "review-inputs")
        return destination
    except (OSError, ValueError, TypeError, RecursionError):
        raise ProvenanceError(
            "Cannot create artifact manifest; inspect run ownership, entries and stability."
        ) from None
