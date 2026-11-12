"""Strict contract-1/2/3 decoding of nare evidence and caller-attributed candidates."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn

from scrutare.config import CATEGORIES
from scrutare.engine.assessment_inputs import AssessmentInput
from scrutare.engine.debate_inputs import DebateInput
from scrutare.engine.persona_inputs import PersonaReanchorInput
from scrutare.engine.reanchor import parse_reanchor_output
from scrutare.engine.session_models import SessionCost, TokenUsage
from scrutare.findings.citation_correction import (
    CitationDefect,
    apply_citation_correction,
    citation_defects,
)
from scrutare.findings.evidence import CitationValidationError
from scrutare.findings.models import Finding, FindingError, parse_finding
from scrutare.findings.protocol_correction import ProtocolContext, ProtocolDefect
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
    cut_off: bool = False
    # Resent model requests over the session's life, when nare reports the count.
    retries: int | None = None
    cost: SessionCost = SessionCost()


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
    cut_off: bool = False
    # Resent model requests over the session's life, when nare reports the count.
    retries: int | None = None
    cost: SessionCost = SessionCost()


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
    cut_off: bool = False
    cost: float | None = None
    retries: int | None = None
    # Whether nare says the backend reported every dollar of `cost` (none calculated).
    reported: bool = False
    # Capacity refusals nare queued over the session's life, when it reports the count.
    queued: int | None = None

    @property
    def session_cost(self) -> SessionCost:
        return _cost(self.cost, self.reported)


class CitationCorrectionOffered(Exception):
    """A discovery answer refused only for correctable citation defects (opt-in, #91).

    Not a ValueError, so no decoding path mistakes it for a refusal. It carries the
    session's first evidence, so the correction can continue that session's accounting.
    """

    def __init__(self, defects: tuple[CitationDefect, ...], reason: str,
                 citation_reason: str | None, evidence: _DecodedEvidence) -> None:
        super().__init__("citation correction offered")
        self.defects = defects
        self.reason = reason
        self.citation_reason = citation_reason
        self.evidence = evidence


class ProtocolCorrectionOffered(Exception):
    """A complete answer refused only for enumerated deterministic protocol defects."""

    def __init__(self, defects: tuple[ProtocolDefect, ...], reason: str,
                 citation_reason: str | None, evidence: _DecodedEvidence,
                 context: ProtocolContext) -> None:
        super().__init__("protocol correction offered")
        self.defects, self.reason, self.citation_reason = defects, reason, citation_reason
        self.evidence, self.context = evidence, context


# nare's error event for a turn cut off at max_tokens (nare 2026.10.12 loop._UNFINISHED; the
# exact text is nare's contract, not provider text). A cut-off ends the session.
CUT_OFF = "the model's turn was cut off at max_tokens"


def cut_off_at_max_tokens(events: list[Any], status: object, stop: object) -> bool:
    """Whether nare's last turn was cut off at max_tokens; ``events`` precede the result.

    A cut-off session stops with ``max_tokens``. When the cut-off turn also exhausts the
    token budget, nare's after-turn budget check appends its own error event and replaces
    the stop with ``budget`` (nare 2026.10.12), so the cut-off is read from the stream:
    its error event immediately followed by nare's budget error record, in status error.
    nare writes nothing between or after the two.
    """
    if stop == "max_tokens":
        return True
    if stop != "budget" or status != "error" or len(events) < 2:
        return False
    cut, budget = events[-2:]
    detail = budget.get("detail") if isinstance(budget, dict) else None
    return (isinstance(cut, dict) and cut.get("type") == "error" and cut.get("text") == CUT_OFF
            and isinstance(detail, dict) and budget.get("type") == "error"
            and isinstance(detail.get("budget"), dict) and isinstance(detail.get("usage"), dict))


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
    if "total" in document and _integer(document["total"]) != sum(counters):
        _fail()
    cost = document["cost"]
    return TokenUsage(*counters), None if cost is None else _number(cost)


def _reported(value: object) -> bool:
    """Whether a usage record says its cost came from the backend and none was calculated.

    nare marks `estimated` true when any part was calculated from prices. A record without
    the field (an older nare) does not say, so its cost is not taken as reported.
    """
    return isinstance(value, dict) and value.get("estimated") is False


def _cost(usd: float | None, reported: bool) -> SessionCost:
    """A reported amount, or unknown: a missing or calculated cost is never an amount."""
    return SessionCost(usd, "provider_reported") if usd is not None and reported else SessionCost()


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


def constrained_turns(stdout: bytes, mode: str | None, *, strict: bool = True) -> int:
    """Count nare's schema-constrained answer turns, which an unconstrained session never has.

    Strict decoding refuses a malformed record or one on a session that did not ask for the
    constraint. Tolerant counting, for evidence a failed session leaves, skips unreadable lines.
    """
    count = 0
    for line in stdout.split(b"\n"):
        if not line.strip():
            continue
        try:
            document = _json(line)
            if _envelope(document) != "progress" or "structured_output" not in document["detail"]:
                continue
            record = document["detail"]["structured_output"]
            # Additive fields are ignored, as nare's contract asks of callers.
            if (mode is None or not isinstance(record, dict) or not {
                    "mode", "tools_withheld"} <= set(record) or record["mode"] != mode
                    or type(record["tools_withheld"]) is not bool):
                _fail()
            count += 1
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            if strict:
                raise SessionProtocolError(
                    "Invalid constrained-answer evidence; inspect private capture.") from None
    return count


def require_sent_effort(session_document: bytes | None, effort: str | None) -> None:
    """Refuse a saved session that records another reasoning effort than the rail asked for.

    nare records what each invocation sent, null when nothing. A session file from before
    nare recorded it has no field and proves nothing either way.
    """
    if session_document is None:
        return
    try:
        saved = _json(session_document)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise SessionProtocolError("Invalid saved session; inspect private capture.") from None
    if "effort" in saved and saved["effort"] != effort:
        raise SessionProtocolError("effort: saved session records another reasoning effort")


def _retry(document: dict[str, Any]) -> bool:
    """Whether an event is nare's record of one resent model request."""
    return document["type"] == "progress" and isinstance(document["detail"].get("retry"), dict)


def _queued(document: dict[str, Any]) -> float | None:
    """The wait nare announced for one queued capacity refusal, or None for any other event.

    nare writes the record before it sleeps. A refused request was never dispatched, so the
    record is neither a resend nor a turn and carries no usage.
    """
    if document["type"] != "progress" or "capacity" not in document["detail"]:
        return None
    return _number(_object(document["detail"]["capacity"])["wait"])


def decode_live_event(
    line: bytes,
) -> tuple[TokenUsage | None, bool, SessionCost | None, float | None]:
    """One event's turn counters and cost, if it is a cost event, whether it is a resend,
    and the announced wait if it is a queued capacity refusal.

    A resent request has no cost event of its own: nare reports usage for completed replies
    only, so a retry never adds counters, and neither does a request a provider refused for
    capacity before dispatch. Other additive event types are ignored.
    """
    try:
        document = _json(line)
        kind = _envelope(document)
        if kind == "result":
            return None, False, None, None
        if kind != "cost":
            return None, _retry(document), None, _queued(document)
        usage, cost = _usage(document["detail"])
        return usage, False, _cost(cost, _reported(document["detail"])), None
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError("Invalid nare event; inspect private capture.") from None


def decode_cost_event(line: bytes) -> TokenUsage | None:
    """Decode a single event's disjoint turn counters, ignoring additive event types."""
    return decode_live_event(line)[0]


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
    expected_limit: int, expected_root: Path, baseline: _DecodedEvidence | None = None,
) -> _DecodedEvidence:
    """Reconcile fresh execution and saved evidence independently of the typed payload.

    With ``baseline``, the stream continues that session (a resumed, tool-free correction
    turn): nare's usage is cumulative, so the result equals the baseline plus this stream.
    """
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
        if contract not in (1, 2, 3):
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
        if baseline is not None:
            summed = TokenUsage(*(getattr(summed, field) + getattr(baseline.usage, field)
                                  for field in ("input", "output", "cache_read",
                                                "cache_write")))
            summed_cost = (None if summed_cost is None or baseline.cost is None
                           else summed_cost + baseline.cost)
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
        # Each resend leaves one retry event, and nare counts them over the session's life.
        resent = sum(_retry(record) for record in records[:-1])
        retries = result.get("retries")
        if retries is None:
            if resent:
                _fail()
        elif _integer(retries) != resent + ((baseline.retries or 0) if baseline else 0):
            _fail()
        # Each queued capacity refusal leaves one record, counted like resends but apart
        # from them, from turns and from usage.
        waits = sum(_queued(record) is not None for record in records[:-1])
        queued = result.get("capacity_refusals")
        if queued is None:
            if waits:
                _fail()
        elif _integer(queued) != waits + ((baseline.queued or 0) if baseline else 0):
            _fail()
        output = result["output"]
        if output is None and status == "done":
            _fail()
        saved = _json(session_document)
        if "retries" in saved and _integer(saved["retries"]) != (retries or 0):
            _fail()
        if ("capacity_refusals" in saved
                and _integer(saved["capacity_refusals"]) != (queued or 0)):
            _fail()
        if (_text(saved["id"]) != identity or _text(saved["status"]) != status
                or saved["stop_reason"] != stop
                or _integer(saved["contract"], positive=True) != contract
                or _text(saved["nare"]) != version or saved["output"] != output):
            _fail()
        saved_usage, saved_cost = _usage(saved["usage"])
        if saved_usage != total_usage or not _same_cost(saved_cost, cost):
            _fail()
        _budget(saved["budget"], expected_limit)
        policy = _object(saved["policy"])
        # A correction turn continues the same session with no tools at all.
        if (policy["tools"] != ([] if baseline is not None else ["read"])
                or policy["root"] != str(expected_root)):
            _fail()
        if "turns" in saved and _integer(saved["turns"]) != count:
            _fail()
        # --max-turns 1 allows one correction. Newer nare can then make one
        # accounted, tool-free summary request, retaining error/max_turns. Its
        # output is never a successful correction; every completed turn must
        # have its own cost event and remain within this session's budget.
        max_correction_turns = 2 if status == "error" and stop == "max_turns" else 1
        if baseline is not None and (
                identity != baseline.session_id or version != baseline.nare_version
                or contract != baseline.contract or baseline.turns is None
                or not baseline.turns <= count <= baseline.turns + max_correction_turns
                or count - baseline.turns != len(turns)):
            _fail()
        if (baseline is not None and len(turns) == 2
                and baseline.usage.total + turns[0][0].total >= expected_limit):
            # Nare checks cumulative usage before admitting its summary; only
            # the final admitted request may cross the ceiling.
            _fail()
        return _DecodedEvidence(identity, status, stop, exit_code, total_usage,
                                output is not None, expected_limit,
                                (status == "error" and stop == "budget")
                                or total_usage.total > expected_limit,
                                version, contract, count, output, saved["output"],
                                cut_off_at_max_tokens(records[:-1], status, stop), cost,
                                retries, _reported(result["usage"]), queued)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None


def decode_session(
    stdout: bytes, session_document: bytes | None, *, persona: str, exit_code: int,
    expected_limit: int, expected_root: Path,
    descriptor: DebateInput | AssessmentInput | None = None,
    evidence_version: int = 1, candidate_namespace: str = "",
    discard_cut_off: bool = False, offer_citation_correction: bool = False,
    protocol_context: ProtocolContext | None = None,
) -> DecodedSession:
    """Reconcile fresh findings evidence, attributing candidates only to the caller.

    With ``discard_cut_off`` (a rail with an output cap), a session whose last turn was cut
    off at max_tokens yields no answer: whatever document nare kept is not parsed or
    validated.

    With ``offer_citation_correction`` (opt-in discovery), a finished answer within its
    budget whose only refusals are correctable citation defects raises
    CitationCorrectionOffered instead of failing; every other outcome is unchanged.
    """
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
        discarded = discard_cut_off and evidence.cut_off
        try:
            findings = () if evidence.output is None or discarded else parser(evidence.output)
        except (ValueError, TypeError, KeyError) as error:
            if (protocol_context is not None and evidence.status == "done" and not discarded
                    and evidence.usage.total < expected_limit):
                from scrutare.findings.protocol_correction import protocol_defects
                protocol_found = protocol_defects(evidence.output, expected_root, protocol_context)
                if protocol_found is not None:
                    # Both saved and streamed documents must have the same typed semantics,
                    # including at refused fields (JSON bool/int equality is insufficient).
                    if json.dumps(evidence.output, sort_keys=True) != json.dumps(
                            evidence.saved_output, sort_keys=True):
                        _fail()
                    raise ProtocolCorrectionOffered(
                        protocol_found,
                        "citation_validation" if isinstance(error, CitationValidationError)
                        else "protocol",
                        error.reason if isinstance(error, CitationValidationError) else None,
                        evidence, protocol_context) from None
            # nare refuses to start a turn once usage reaches the limit, so a session with no
            # budget left is never offered one.
            if (offer_citation_correction and descriptor is None and evidence_version == 2
                    and evidence.status == "done" and not discarded
                    and evidence.usage.total < expected_limit):
                defects = citation_defects(evidence.output, expected_root, parse=parser)
                if defects is not None:
                    refused = isinstance(error, CitationValidationError)
                    raise CitationCorrectionOffered(
                        defects, "citation_validation" if refused else "protocol",
                        error.reason if isinstance(error, CitationValidationError) else None,
                        evidence) from None
            raise
        # Validate saved semantics independently: bool/int equality can conceal corruption.
        if evidence.saved_output is not None and not discarded:
            parser(evidence.saved_output)
        return DecodedSession(
            evidence.session_id, evidence.status, evidence.stop_reason, evidence.exit_code,
            evidence.usage, findings, evidence.output_available and not discarded,
            evidence.budget_limit,
            evidence.partial, evidence.nare_version, evidence.contract, evidence.turns,
            evidence.cut_off, evidence.retries, evidence.session_cost,
        )
    except CitationValidationError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None


def decode_citation_correction(
    stdout: bytes, session_document: bytes | None, *,
    offered: CitationCorrectionOffered | ProtocolCorrectionOffered,
    persona: str, exit_code: int, expected_limit: int, expected_root: Path,
    candidate_namespace: str = "", discard_cut_off: bool = False,
) -> DecodedSession:
    """Reconcile a resumed correction turn and validate the corrected answer as before.

    A correction that ended without an answer (nare not done, or cut off at the cap)
    yields no findings. Otherwise the corrected answer is the original document with the
    model's values in exactly the named citations; its refusal raises CitationValidationError
    or FindingError, exactly as an uncorrected answer's would.
    """
    try:
        if (not isinstance(persona, str)
                or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", persona)):
            _fail()
        first = offered.evidence
        evidence = _decode_evidence(stdout, session_document, exit_code=exit_code,
                                    expected_limit=expected_limit, expected_root=expected_root,
                                    baseline=first)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None
    # A correction run that never started reported nothing; the session's usage stands.
    usage = first.usage if evidence.status is None else evidence.usage
    cost = first.session_cost if evidence.status is None else evidence.session_cost
    answered = evidence.status == "done" and not (discard_cut_off and evidence.cut_off)
    findings: tuple[Finding, ...] = ()
    if (isinstance(offered, ProtocolCorrectionOffered)
            and evidence.status in ("done", "blocked") and evidence.stop_reason == "budget"
            and not evidence.partial):
        raise SessionProtocolError("protocol correction: invalid native terminal state")
    if answered:
        try:
            if isinstance(offered, ProtocolCorrectionOffered):
                assert isinstance(offered, ProtocolCorrectionOffered)
                findings = offered.context.parse(evidence.output, expected_root, persona,
                                                  candidate_namespace)
                offered.context.parse(evidence.saved_output, expected_root, persona,
                                       candidate_namespace)
                return DecodedSession(
                    evidence.session_id, evidence.status, evidence.stop_reason, evidence.exit_code,
                    usage, findings, answered, evidence.budget_limit, evidence.partial,
                    evidence.nare_version, evidence.contract, evidence.turns, evidence.cut_off,
                    evidence.retries, cost)
            corrected = apply_citation_correction(first.output, offered.defects, evidence.output)
            findings = _findings(corrected, persona, evidence_version=2,
                                 expected_root=expected_root,
                                 candidate_namespace=candidate_namespace)
            # Validate saved semantics independently: bool/int equality can conceal corruption.
            _findings(apply_citation_correction(first.saved_output, offered.defects,
                                                evidence.saved_output),
                      persona, evidence_version=2, expected_root=expected_root,
                      candidate_namespace=candidate_namespace)
        except (CitationValidationError, FindingError):
            raise
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise FindingError("citation correction: corrected answer is invalid") from None
    return DecodedSession(
        evidence.session_id, evidence.status, evidence.stop_reason, evidence.exit_code,
        usage, findings, answered, evidence.budget_limit, evidence.partial,
        evidence.nare_version, evidence.contract, evidence.turns, evidence.cut_off,
        evidence.retries, cost,
    )


def decode_reanchor_session(
    stdout: bytes, session_document: bytes | None, *, descriptor: PersonaReanchorInput,
    exit_code: int, expected_limit: int, discard_cut_off: bool = False,
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
        discarded = discard_cut_off and evidence.cut_off
        corrections = () if evidence.output is None or discarded else parse_reanchor_output(
            evidence.output, descriptor.requests,
        )
        if evidence.saved_output is not None and not discarded:
            parse_reanchor_output(evidence.saved_output, descriptor.requests)
        return DecodedReanchorSession(
            evidence.session_id, evidence.status, evidence.stop_reason, evidence.exit_code,
            evidence.usage, corrections, evidence.output_available and not discarded,
            evidence.budget_limit,
            evidence.partial, evidence.nare_version, evidence.contract, evidence.turns,
            evidence.cut_off, evidence.retries, evidence.session_cost,
        )
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise SessionProtocolError(
            "Session evidence is invalid or inconsistent; inspect private capture."
        ) from None
