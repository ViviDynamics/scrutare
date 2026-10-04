"""Strict readers for captured evidence, without ambient review execution."""

import json
import math
import os
import stat
from pathlib import Path
from typing import cast

from scrutare.config import CATEGORIES, Category, ConfigError, Strategy, VerdictSettings
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.models import Anchor, FindingError, Side, parse_finding
from scrutare.findings.verdict import Exhaustion
from scrutare.replay.models import AuditIssue, CapturedPolicy, ReplayError, SavedVerdict

_GROUP_FIELDS = ("file", "line", "side", "problem", "categories", "personas", "reasons", "sources")
_SOURCE_FIELDS = ("file", "line", "side", "category", "problem", "reason", "persona")
_ARTIFACT_NAMES = (
    "findings.json", "config.json", "verdict.json", "posting.json", "escalation.json",
    "reviewer-request.json", "metadata.json",
)


def _artifact_name(name: str) -> str:
    return name if name in _ARTIFACT_NAMES else "artifact"


def read_artifact(path: Path) -> bytes:
    """Read a regular file without following a leaf symlink or blocking on a FIFO."""
    artifact = _artifact_name(path.name)
    try:
        inspected = path.lstat()
        if not stat.S_ISREG(inspected.st_mode):
            raise ValueError
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (inspected.st_dev, inspected.st_ino)):
                raise ValueError
            return stream.read()
    except (OSError, ValueError):
        raise ReplayError(f"{artifact}: cannot read regular artifact file") from None


def _check_nesting(text: str) -> None:
    """Bound JSON container depth to 128 before version-dependent decoding."""
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 128:
                raise ValueError
        elif char in "]}":
            depth -= 1


def decode_artifact(data: bytes, *, artifact: str) -> object:
    """Decode strict UTF-8 JSON, rejecting ambiguous objects and invalid text."""
    artifact = _artifact_name(artifact)

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError

    try:
        text = data.decode("utf-8")
        _check_nesting(text)
        document: object = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        pending = [document]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError
        return document
    except (ValueError, RecursionError):
        raise ReplayError(f"{artifact}: invalid or unsupported UTF-8 JSON") from None


def _mapping(data: object, path: str, fields: tuple[str, ...] | None = None) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ReplayError(f"{path}: expected an object")
    if any(not isinstance(key, str) for key in data):
        raise ReplayError(f"{path}: expected string field names")
    if fields is not None:
        if any(key not in fields for key in data):
            raise ReplayError(f"{path}: unknown field")
        for field in fields:
            if field not in data:
                raise ReplayError(f"{path}.{field}: required field")
    return cast(dict[str, object], data)


def _array(data: object, path: str) -> list[object]:
    if not isinstance(data, list):
        raise ReplayError(f"{path}: expected an array")
    return cast(list[object], data)


def _strings(data: object, path: str, *, categories: bool = False) -> list[str]:
    items = _array(data, path)
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise ReplayError(f"{path}[{index}]: expected a nonempty string")
        if categories and item not in CATEGORIES:
            raise ReplayError(f"{path}[{index}]: expected a known category")
    return cast(list[str], items)


def parse_findings(data: object) -> tuple[tuple[MergedFinding, ...], tuple[AuditIssue, ...]]:
    """Reconstruct ordered groups from sources, checking redundant summaries."""
    groups = []
    issues = []
    for index, item in enumerate(_array(data, "findings")):
        path = f"findings[{index}]"
        group = _mapping(item, path, _GROUP_FIELDS)
        sources = []
        for source_index, source_data in enumerate(_array(group["sources"], f"{path}.sources")):
            source_path = f"{path}.sources[{source_index}]"
            source = _mapping(source_data, source_path, _SOURCE_FIELDS)
            try:
                sources.append(parse_finding(
                    {key: value for key, value in source.items() if key != "persona"},
                    persona=cast(str, source["persona"]),
                ))
            except FindingError as error:
                raise ReplayError(f"{source_path}.{error}") from None
        try:
            merged = MergedFinding(
                Anchor(cast(str, group["file"]), cast(int, group["line"]),
                       cast(Side, group["side"])),
                cast(str, group["problem"]), tuple(sources),
            )
        except FindingError as error:
            raise ReplayError(f"{path}.{error}") from None
        for field in ("categories", "personas", "reasons"):
            summary = _strings(group[field], f"{path}.{field}", categories=field == "categories")
            if summary != list(getattr(merged, field)):
                issues.append(AuditIssue("findings_summary_disagrees", f"{path}.{field}",
                                         "difference"))
        groups.append(merged)
    return tuple(groups), tuple(issues)


def _settings(data: object, path: str) -> VerdictSettings:
    document = _mapping(data, path)
    blocking = _strings(document.get("blocking_categories"), f"{path}.blocking_categories",
                        categories=True)
    advisory = _strings(document.get("advisory_categories"), f"{path}.advisory_categories",
                        categories=True)
    try:
        return VerdictSettings(cast(tuple[Category, ...], tuple(blocking)),
                               cast(tuple[Category, ...], tuple(advisory)))
    except ConfigError:
        raise ReplayError(
            f"{path}: expected a complete category partition without duplicates"
        ) from None


def _strategy(data: object, path: str) -> Strategy:
    if not isinstance(data, str) or data not in ("panel", "iterative", "debate"):
        raise ReplayError(f"{path}: expected a known strategy")
    return cast(Strategy, data)


def _positive(data: object, path: str) -> int:
    if type(data) is not int or data <= 0:
        raise ReplayError(f"{path}: expected a positive integer")
    return data


def parse_policy(data: object) -> CapturedPolicy:
    """Project deciding fields while retaining unrelated captured configuration."""
    document = _mapping(data, "config")
    settings = _settings(document.get("verdict"), "config.verdict")
    strategy = (
        _strategy(document["strategy"], "config.strategy") if "strategy" in document else None
    )
    round_limit = None
    if "rounds" in document:
        rounds = _mapping(document["rounds"], "config.rounds")
        if "max" in rounds:
            round_limit = _positive(rounds["max"], "config.rounds.max")
    return CapturedPolicy(settings, strategy, round_limit, document)


def _exhaustion(data: object) -> Exhaustion:
    path = "verdict.exhaustion"
    document = _mapping(data, path, ("strategy", "rounds_completed", "round_limit", "converged"))
    try:
        return Exhaustion(
            _strategy(document["strategy"], f"{path}.strategy"),
            cast(int, document["rounds_completed"]), cast(int, document["round_limit"]),
            cast(bool, document["converged"]),
        )
    except FindingError as error:
        raise ReplayError(f"verdict.{error}") from None


def parse_saved_verdict(data: bytes) -> SavedVerdict:
    """Validate the established baseline shape, preserving recorded output fields."""
    raw_document = decode_artifact(data, artifact="verdict.json")
    document = _mapping(raw_document, "verdict")
    fields: tuple[str, ...] = ("schema_version", "verdict", "rule", "config", "findings")
    if "exhaustion" in document:
        fields += ("exhaustion",)
    _mapping(document, "verdict", fields)
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ReplayError("verdict.schema_version: expected supported integer version 1")
    if not isinstance(document["verdict"], str) or document["verdict"] not in (
        "approve", "changes_requested", "escalated"
    ):
        raise ReplayError("verdict.verdict: expected a known verdict")
    if not isinstance(document["rule"], str) or document["rule"] not in (
        "any_blocking_finding", "no_blocking_findings", "rounds_exhausted_without_convergence"
    ):
        raise ReplayError("verdict.rule: expected a known rule")
    config = _mapping(document["config"], "verdict.config",
                      ("blocking_categories", "advisory_categories"))
    _settings(config, "verdict.config")
    groups = []
    for index, item in enumerate(_array(document["findings"], "verdict.findings")):
        path = f"verdict.findings[{index}]"
        group = _mapping(item, path, _GROUP_FIELDS + ("blocking", "blocking_categories"))
        if type(group["blocking"]) is not bool:
            raise ReplayError(f"{path}.blocking: expected a boolean")
        _strings(group["blocking_categories"], f"{path}.blocking_categories", categories=True)
        groups.append({key: group[key] for key in _GROUP_FIELDS})
    try:
        parse_findings(groups)
    except ReplayError as error:
        raise ReplayError(f"verdict.{error}") from None
    exhaustion = _exhaustion(document["exhaustion"]) if "exhaustion" in document else None
    if exhaustion is None and (
        document["verdict"] == "escalated"
        or document["rule"] == "rounds_exhausted_without_convergence"
    ):
        raise ReplayError("verdict.exhaustion: explicit recorded exhaustion is required")
    return SavedVerdict(data, document, exhaustion)


def validate_exhaustion(saved: SavedVerdict, policy: CapturedPolicy) -> Exhaustion | None:
    """Check an explicit recorded bound assertion against the captured policy."""
    exhaustion = saved.exhaustion
    if exhaustion is None:
        return None
    if policy.strategy != exhaustion.strategy:
        raise ReplayError("config.strategy: must match recorded exhaustion strategy")
    if policy.round_limit != exhaustion.round_limit:
        raise ReplayError("config.rounds.max: must match recorded exhaustion bound")
    return exhaustion
