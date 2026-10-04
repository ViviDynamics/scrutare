"""Pure changed-file validation and case-sensitive POSIX path selection."""

from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase

from scrutare.config import ConfigError, PathSettings
from scrutare.findings.models import FindingError, _path

_STATUSES = ("added", "removed", "modified", "renamed", "copied", "changed", "unchanged")


@dataclass(frozen=True)
class ChangedFile:
    """Minimal immutable API file identity, without patch or discussion content."""

    filename: str
    status: str
    previous_filename: str | None = None

    def __post_init__(self) -> None:
        _path(self.filename, "files.filename")
        if not isinstance(self.status, str) or self.status not in _STATUSES:
            raise FindingError("files.status: expected a known GitHub file status")
        if self.previous_filename is not None:
            _path(self.previous_filename, "files.previous_filename")


def _unique(files: tuple[ChangedFile, ...]) -> None:
    seen: set[str] = set()
    for file in files:
        if not isinstance(file, ChangedFile):
            raise FindingError("files: expected ChangedFile records")
        if file.filename in seen:
            raise FindingError("files.filename: duplicate file")
        seen.add(file.filename)


def parse_changed_files(data: object) -> tuple[ChangedFile, ...]:
    """Project a GitHub files array into validated, independent file records."""
    if not isinstance(data, list):
        raise FindingError("files: expected a list")
    files: list[ChangedFile] = []
    for record in data:
        if not isinstance(record, Mapping):
            raise FindingError("files: expected file mappings")
        filename = _path(record.get("filename"), "files.filename")
        status = record.get("status")
        if not isinstance(status, str):
            raise FindingError("files.status: expected a known GitHub file status")
        previous = record.get("previous_filename")
        if previous is not None:
            previous = _path(previous, "files.previous_filename")
        files.append(ChangedFile(filename, status, previous))
    result = tuple(files)
    _unique(result)
    return result


def _validate_settings(paths: PathSettings) -> None:
    if not isinstance(paths, PathSettings):
        raise ConfigError("github.paths: expected PathSettings")
    for field, patterns in (("include", paths.include), ("exclude", paths.exclude)):
        if not isinstance(patterns, tuple):
            raise ConfigError(f"github.paths.{field}: expected a tuple")
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ConfigError(f"github.paths.{field}: expected nonempty string patterns")
            try:
                _path(pattern, f"github.paths.{field}")
            except FindingError:
                raise ConfigError(
                    f"github.paths.{field}: expected repository-relative patterns"
                ) from None


def _matches(filename: str, pattern: str) -> bool:
    if "/" not in pattern:
        return fnmatchcase(filename.rsplit("/", 1)[-1], pattern)
    parts = filename.split("/")
    # Positions in the path reachable after consuming each pattern segment.
    # A complete ** segment may consume any number of directories, including zero.
    positions = {0}
    for segment in pattern.split("/"):
        if segment == "**":
            positions = set(range(min(positions), len(parts) + 1)) if positions else set()
        else:
            positions = {i + 1 for i in positions
                         if i < len(parts) and fnmatchcase(parts[i], segment)}
        if not positions:
            return False
    return len(parts) in positions


def select_changed_files(
    files: tuple[ChangedFile, ...], paths: PathSettings,
) -> tuple[ChangedFile, ...]:
    """Keep canonical include matches unless either identity matches an exclusion."""
    _validate_settings(paths)
    if not isinstance(files, tuple):
        raise FindingError("files: expected a tuple")
    _unique(files)
    return tuple(
        file for file in files
        if (not paths.include or any(_matches(file.filename, pattern) for pattern in paths.include))
        and not any(
            _matches(name, pattern)
            for name in (file.filename, file.previous_filename)
            if name is not None
            for pattern in paths.exclude
        )
    )
