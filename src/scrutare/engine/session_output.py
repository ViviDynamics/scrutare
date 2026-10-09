"""Strict contract-1 decoding of nare evidence and caller-attributed candidates."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn

from scrutare.config import CATEGORIES
from scrutare.engine.debate_inputs import DebateInput
from scrutare.engine.persona_inputs import PersonaReanchorInput
from scrutare.engine.reanchor import parse_reanchor_output
from scrutare.engine.session_models import TokenUsage
from scrutare.findings.evidence import CitationValidationError
from scrutare.findings.models import Finding, parse_finding
from scrutare.findings.verification import ReanchorCorrection


class SessionProtocolError(ValueError):
    """A protocol diagnostic that never incorporates untrusted model or provider text."""


@dataclass(frozen=True)
class DecodedSession:
    session_id: str | None
    status: str | None
    stop_reason: str | None
    exit_code: int
    usage: TokenUsage
    findings: tuple[Finding, ...]
    output_available: bool
    budget_limit: int
    partial: bool
    nare_version: str | None
    contract: int | None
    turns: int | None


@dataclass(frozen=True)
class DecodedReanchorSession:
    session_id: str | None
    status: str | None
    stop_reason: str | None
    exit_code: int
    usage: TokenUsage
    corrections: tuple[ReanchorCorrection, ...]
    output_available: bool
    budget_limit: int
    partial: bool
    nare_version: str | None
    contract: int | None
    turns: int | None


@dataclass(frozen=True)
class _DecodedEvidence:
    session_id: str | None
    status: str | None
    stop_reason: str | None
    exit_code: int
    usage: TokenUsage
    output_available: bool
    budget_limit: int
    partial: bool
    nare_version: str | None
    contract: int | None
    turns: int | None
    output: object
    saved_output: object


def findings_schema(*, evidence_version: int = 1) -> dict[str, object]:
    """Return a fresh schema using only nare-supported JSON Schema keywords."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "problem": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["file", "line", "category", "problem", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["findings"],
        "additionalProperties": False,
    }

    if evidence_version == 2:
        from scrutare.findings.evidence import evidence_schema
        item = schema["properties"]["findings"]["items"]
        item["properties"]["evidence"] = evidence_schema()
        item["required"].append("evidence")
    elif evidence_version != 1:
        raise ValueError("Unsupported evidence version")
    return schema


def _fail() -> NoReturn:
    raise SessionProtocolError(
        "Session evidence is invalid or inconsistent; inspect private capture."
    )


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in items:
        if key in document:
            _fail()
        document[key] = value
    return document


def _number(value: object) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        _fail()
    return float(value)


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        _fail()
    return result


def _constant(value: str) -> None:
    _fail()


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail()
    assert isinstance(value, dict)
    return value


def _json(data: bytes) -> dict[str, Any]:
    return _object(json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_constant=_constant, parse_float=_float))


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail()
    assert isinstance(value, str)
    value.encode("utf-8")
    return value


def _integer(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        _fail()
    assert isinstance(value, int)
    return value


def _usage(value: object) -> tuple[TokenUsage, float | None]:
    document = _object(value)
    counters = [_integer(document[field])
                for field in ("input", "output", "cache_read", "cache_write")]
    cost = document["cost"]
    return TokenUsage(*counters), None if cost is None else _number(cost)


def _envelope(document: dict[str, Any]) -> str:
    kind = _text(document["type"])
    # A terminal result has its own contract rather than an event envelope.
    if kind != "result":
        if not isinstance(document["text"], str):
            _fail()
        document["text"].encode("utf-8")
        _object(document["detail"])
        _text(document["timestamp"])
    return kind


def decode_cost_event(line: bytes) -> TokenUsage | None:
    """Decode a single event's disjoint turn counters, ignoring additive event types."""
    try:
        document = _json(line)
        if _envelope(document) != "cost":
            return None
        return _usage(document["detail"])[0]
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError("Invalid nare event; inspect private capture.") from None


def _budget(value: object, expected_limit: int, usage: TokenUsage | None = None,
            cost: float | None = None) -> None:
    document = _object(value)
    if _integer(document["tokens"], positive=True) != expected_limit or "usd" in document:
        _fail()
    if usage is not None:
        if _integer(document["used_tokens"]) != usage.total:
            _fail()
        used_cost = document["used_usd"]
        if used_cost is not None:
            used_cost = _number(used_cost)
        if not _same_cost(used_cost, cost):
            _fail()


def _same_cost(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)


def _findings(
    value: object, persona: str, *, evidence_version: int = 1,
    expected_root: Path | None = None, candidate_namespace: str = "",
) -> tuple[Finding, ...]:
    document = _object(value)
    if set(document) != {"findings"} or not isinstance(document["findings"], list):
        _fail()
    from scrutare.findings.evidence import validate_evidence
    findings = []
    for index, item in enumerate(document["findings"]):
        identifier = sha256(f"{candidate_namespace}:{persona}:{index}".encode()).hexdigest()
        finding = parse_finding(_object(item), persona=persona, evidence_version=evidence_version,
                                candidate_id=identifier if evidence_version == 2 else None)
        if evidence_version == 2:
            if expected_root is None:
                _fail()
            assert expected_root is not None
            finding = validate_evidence(finding, expected_root)
        findings.append(finding)
    return tuple(findings)


def _decode_evidence(
    stdout: bytes, session_document: bytes | None, *, exit_code: int,
    expected_limit: int, expected_root: Path,
) -> _DecodedEvidence:
    """Reconcile fresh execution and saved evidence independently of the typed payload."""
    try:
        _integer(expected_limit, positive=True)
        if (type(exit_code) is not int or exit_code not in (0, 1, 2)
                or not isinstance(expected_root, Path) or not expected_root.is_absolute()):
            _fail()
        if exit_code == 2 and stdout == b"" and session_document is None:
            return _DecodedEvidence(None, None, None, 2, TokenUsage(), False,
                                    expected_limit, False, None, None, None, None, None)
        if exit_code == 2 or session_document is None:
            _fail()
        assert session_document is not None
        records = [_json(line.encode("utf-8")) for line in stdout.decode("utf-8").split("\n")
                   if line.strip()]
        if not records or records[-1].get("type") != "result":
            _fail()
        turns: list[tuple[TokenUsage, float | None]] = []
        for record in records[:-1]:
            kind = _envelope(record)
            if kind == "result":
                _fail()
            if kind == "cost":
                turns.append(_usage(record["detail"]))
        result = records[-1]
        identity = _text(result["session_id"])
        status = _text(result["status"])
        if {"done": 0, "blocked": 0, "error": 1}.get(status) != exit_code:
            _fail()
        stop = result["stop_reason"]
        if stop is not None:
            stop = _text(stop)
        contract = _integer(result["contract"], positive=True)
        if contract != 1:
            _fail()
        version = _text(result["nare"])
        count = _integer(result["turns"])
        questions = result["questions"]
        if not isinstance(questions, list) or any(not isinstance(q, str) for q in questions):
            _fail()
        error = result["error"]
        if error is not None and not isinstance(error, str):
            _fail()
        total_usage, cost = _usage(result["usage"])
        summed = TokenUsage(*(sum(getattr(turn[0], field) for turn in turns)
                              for field in ("input", "output", "cache_read", "cache_write")))
        summed_cost = None if any(turn[1] is None for turn in turns) else sum(
            turn[1] for turn in turns if turn[1] is not None
        )
        # With no reported turns nare starts with known zero dollar usage.
        if total_usage != summed or not _same_cost(cost, summed_cost):
            _fail()
        _budget(result["budget"], expected_limit, total_usage, cost)
        for record in records[:-1]:
            if record["type"] == "error":
                detail = record["detail"]
                if "budget" in detail or "usage" in detail:
                    event_usage, event_cost = _usage(detail["usage"])
                    if event_usage != total_usage or not _same_cost(event_cost, cost):
                        _fail()
                    _budget(detail["budget"], expected_limit, event_usage, event_cost)
        output = result["output"]
        if output is None and status == "done":
            _fail()
        saved = _json(session_document)
        if (_text(saved["id"]) != identity or _text(saved["status"]) != status
                or saved["stop_reason"] != stop or _integer(saved["contract"], positive=True) != 1
                or _text(saved["nare"]) != version or saved["output"] != output):
            _fail()
        saved_usage, saved_cost = _usage(saved["usage"])
        if saved_usage != total_usage or not _same_cost(saved_cost, cost):
            _fail()
        _budget(saved["budget"], expected_limit)
        policy = _object(saved["policy"])
        if policy["tools"] != ["read"] or policy["root"] != str(expected_root):
            _fail()
        if "turns" in saved and _integer(saved["turns"]) != count:
            _fail()
        return _DecodedEvidence(identity, status, stop, exit_code, total_usage,
                                output is not None, expected_limit,
                                (status == "error" and stop == "budget")
                                or total_usage.total > expected_limit,
                                version, contract, count, output, saved["output"])
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None


def decode_session(
    stdout: bytes, session_document: bytes | None, *, persona: str, exit_code: int,
    expected_limit: int, expected_root: Path, descriptor: DebateInput | None = None,
    evidence_version: int = 1, candidate_namespace: str = "",

) -> DecodedSession:
    """Reconcile fresh findings evidence, attributing candidates only to the caller."""
    try:
        if (not isinstance(persona, str)
                or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", persona)):
            _fail()
        evidence = _decode_evidence(stdout, session_document, exit_code=exit_code,
                                    expected_limit=expected_limit, expected_root=expected_root)
        parser = (descriptor.parse_output if descriptor is not None
                  else lambda value: _findings(value, persona, evidence_version=evidence_version,
                                             expected_root=expected_root,
                                             candidate_namespace=candidate_namespace))
        findings = () if evidence.output is None else parser(evidence.output)
        # Validate saved semantics independently: bool/int equality can conceal corruption.
        if evidence.saved_output is not None:
            parser(evidence.saved_output)
        return DecodedSession(
            evidence.session_id, evidence.status, evidence.stop_reason, evidence.exit_code,
            evidence.usage, findings, evidence.output_available, evidence.budget_limit,
            evidence.partial, evidence.nare_version, evidence.contract, evidence.turns,
        )
    except CitationValidationError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None


def decode_reanchor_session(
    stdout: bytes, session_document: bytes | None, *, descriptor: PersonaReanchorInput,
    exit_code: int, expected_limit: int,
) -> DecodedReanchorSession:
    """Reconcile fresh anchor-only evidence against the descriptor's exact originals."""
    try:
        if not isinstance(descriptor, PersonaReanchorInput):
            _fail()
        descriptor.__post_init__()
        evidence = _decode_evidence(
            stdout, session_document, exit_code=exit_code, expected_limit=expected_limit,
            expected_root=descriptor.inputs.root,
        )
        corrections = () if evidence.output is None else parse_reanchor_output(
            evidence.output, descriptor.requests,
        )
        if evidence.saved_output is not None:
            parse_reanchor_output(evidence.saved_output, descriptor.requests)
        return DecodedReanchorSession(
            evidence.session_id, evidence.status, evidence.stop_reason, evidence.exit_code,
            evidence.usage, corrections, evidence.output_available, evidence.budget_limit,
            evidence.partial, evidence.nare_version, evidence.contract, evidence.turns,
        )
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None
