"""Exclusive private engine artifacts bounded by the captured run and prepared read root."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn
from uuid import uuid4


class SessionArtifactError(ValueError):
    """A safe filesystem diagnostic without raw paths or captured provider content."""


def _fail() -> NoReturn:
    raise SessionArtifactError("Cannot create private session artifact; inspect run ownership.")


def _persona(value: object) -> bool:
    return (isinstance(value, str)
            and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value) is not None)


def _boundary(path: Path, prepared_root: Path) -> tuple[Path, Path]:
    if (not isinstance(path, Path) or not isinstance(prepared_root, Path)
            or not path.is_absolute() or not prepared_root.is_absolute()
            or ".." in path.parts or ".." in prepared_root.parts):
        _fail()
    if any(part.is_symlink() for part in (prepared_root, *prepared_root.parents)):
        _fail()
    run = prepared_root.parent
    if (path == prepared_root or prepared_root in path.parents
            or (path != run and run not in path.parents)):
        _fail()
    return path, run


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    """Open each ancestor separately, refusing links without a check/open race."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _private(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        _fail()


@contextmanager
def _child(parent: int, name: str, *, exclusive: bool = False) -> Iterator[int]:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        if exclusive:
            _fail()
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        _private(descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def create_attempt_directory(
    run_dir: Path, persona: str, attempt: int = 1, *, prepared_root: Path,
) -> Path:
    """Create sessions/persona/attempt-NNNN once, outside the explicit model read root."""
    try:
        run_dir, run = _boundary(run_dir, prepared_root)
        if (run_dir != run or not _persona(persona) or type(attempt) is not int
                or not 1 <= attempt <= 9999):
            _fail()
        name = f"attempt-{attempt:04d}"
        destination, _ = _boundary(run / "sessions" / persona / name, prepared_root)
        with _directory(run) as parent, _child(parent, "sessions") as sessions:
            with _child(sessions, persona) as owner, _child(owner, name, exclusive=True):
                pass
        return destination
    except (OSError, ValueError, TypeError):
        raise SessionArtifactError(
            "Cannot create private session attempt; inspect run ownership and destination."
        ) from None


def _destination(path: Path, prepared_root: Path) -> tuple[Path, bool]:
    path, run = _boundary(path, prepared_root)
    parts = path.relative_to(run).parts
    if len(parts) == 1 and parts[0] in (
        "fanout.json", "panel.json", "debate.json", "findings.json", "verdict.json",
        "artifacts.json", "result.json", "iterative.json"
    ):
        return path, False
    if (len(parts) != 4 or parts[0] != "sessions" or not _persona(parts[1])
            or re.fullmatch(r"attempt-[0-9]{4}", parts[2]) is None
            or parts[2] == "attempt-0000"
            or re.fullmatch(r"[a-z][a-z0-9_.-]*", parts[3]) is None):
        _fail()
    return path, True


def write_owned_json(path: Path, document: object, *, prepared_root: Path) -> None:
    """Atomically create a new owner-only JSON record; never replace any existing entry."""
    try:
        data = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise SessionArtifactError("Cannot serialize private JSON artifact.") from None
    write_owned_bytes(path, data, prepared_root=prepared_root)


def write_owned_bytes(path: Path, data: bytes, *, prepared_root: Path) -> None:
    """Install exact bytes atomically at an engine destination without replacing evidence."""
    temporary: str | None = None
    try:
        path, private_parent = _destination(path, prepared_root)
        if not isinstance(data, bytes):
            _fail()
        with _directory(path.parent) as parent:
            if private_parent:
                _private(parent)
                # All session ancestors must remain private, including after creation.
                with _directory(path.parent.parent) as persona:
                    _private(persona)
                with _directory(path.parent.parent.parent) as sessions:
                    _private(sessions)
            try:
                candidate = f".scrutare-{uuid4().hex}.tmp"
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                temporary = candidate
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # link installs the fully written inode atomically and refuses all existing entries.
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            finally:
                if temporary is not None:
                    os.unlink(temporary, dir_fd=parent)
                    temporary = None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        raise SessionArtifactError(
            "Cannot create private JSON artifact; inspect destination, permissions and disk space."
        ) from None
