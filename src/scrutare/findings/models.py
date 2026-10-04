"""Validated immutable findings, with attribution owned by the caller."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from scrutare.config import CATEGORIES, Category

Side = Literal["LEFT", "RIGHT"]


class FindingError(ValueError):
    """Invalid finding or diff, with diagnostics that omit untrusted values."""


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FindingError(f"{field}: expected a nonempty string")
    return value


def _path(value: object, field: str = "file") -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\x00" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise FindingError(f"{field}: expected a repository-relative path without traversal")
    return value


def _line(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise FindingError("line: expected a positive integer")
    return value


def _side(value: object) -> Side:
    if not isinstance(value, str) or value not in ("LEFT", "RIGHT"):
        raise FindingError("side: expected LEFT or RIGHT")
    return cast(Side, value)


def _category(value: object) -> Category:
    if not isinstance(value, str) or value not in CATEGORIES:
        raise FindingError(f"category: expected one of {', '.join(CATEGORIES)}")
    return value


@dataclass(frozen=True)
class Anchor:
    """A repository path and positive line on one side of a patch."""

    file: str
    line: int
    side: Side = "RIGHT"

    def __post_init__(self) -> None:
        _path(self.file)
        _line(self.line)
        _side(self.side)


@dataclass(frozen=True)
class Finding:
    """A review observation without model-owned verdict or severity fields."""

    anchor: Anchor
    category: Category
    problem: str
    reason: str
    persona: str

    def __post_init__(self) -> None:
        if not isinstance(self.anchor, Anchor):
            raise FindingError("anchor: expected an Anchor")
        _category(self.category)
        _text(self.problem, "problem")
        _text(self.reason, "reason")
        _text(self.persona, "persona")


def parse_finding(data: Mapping[str, object], *, persona: str) -> Finding:
    """Parse only approved wire fields, attributing them to the supplied persona."""
    if not isinstance(data, Mapping):
        raise FindingError("finding: expected a mapping")
    fields = ("file", "line", "side", "category", "problem", "reason")
    if any(not isinstance(key, str) or key not in fields for key in data):
        raise FindingError(f"finding: unknown field; allowed: {', '.join(fields)}")
    return Finding(
        Anchor(_path(data.get("file")), _line(data.get("line")), _side(data.get("side", "RIGHT"))),
        _category(data.get("category")),
        _text(data.get("problem"), "problem"),
        _text(data.get("reason"), "reason"),
        _text(persona, "persona"),
    )
