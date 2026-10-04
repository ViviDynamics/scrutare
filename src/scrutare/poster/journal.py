"""Atomic posting artifacts and a persistent per-run advisory lock."""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from scrutare.poster.errors import PostingError


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def read_json(path: Path) -> Any:
    # Duplicate keys and nonfinite values cannot silently change durable intent.
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError

    try:
        return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=invalid)
    except (OSError, ValueError, UnicodeError):
        raise PostingError(
            "Posting artifacts are missing or invalid; inspect the saved run."
        ) from None


def atomic_write(path: Path, data: bytes) -> None:
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="wb", prefix=".posting-", suffix=".tmp",
                                dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise PostingError(
            "Cannot persist posting artifacts; check run permissions and free disk space."
        ) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def run_lock(run_dir: Path) -> Iterator[None]:
    try:
        import fcntl
    except ImportError:
        raise PostingError("Posting requires POSIX advisory lock support.") from None
    try:
        stream = (run_dir / ".posting.lock").open("a+b")
    except OSError:
        raise PostingError("Cannot lock the run; check its directory and permissions.") from None
    with stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PostingError("Run is locked or locking is unsupported; retry later.") from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
