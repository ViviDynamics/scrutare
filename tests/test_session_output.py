"""Untrusted nare streams must never create successful empty reviews or false accounting."""

import copy
import importlib
import importlib.util
import json
from dataclasses import FrozenInstanceError

import pytest
from test_review_inputs import capture as capture

from scrutare.engine.session_models import TokenUsage


def module():
    assert importlib.util.find_spec("scrutare.engine.session_output") is not None, (
        "session output decoder is missing"
    )
    return importlib.import_module("scrutare.engine.session_output")


def usage(**changes):
    value = {"input": 3, "output": 5, "cache_read": 7, "cache_write": 11, "cost": None}
    value.update(changes)
    return value


def event(kind="cost", detail=None):
    return {"type": kind, "text": "untrusted provider secret", "detail": (
        usage() if detail is None else detail
    ), "timestamp": "2026-10-04T18:08:42+00:00"}


def fixture(tmp_path, *, status="done", stop="end_turn", output=None, limit=26):
    document = {"findings": []} if output is None else output
    result = {
        "type": "result", "session_id": "session-1", "status": status,
        "stop_reason": stop, "usage": usage(), "contract": 1, "nare": "2026.10.0",
        "budget": {"tokens": limit, "used_tokens": 26, "used_usd": None},
        "output": document, "questions": [], "error": None, "turns": 1,
    }
    saved = {
        "id": "session-1", "usage": usage(), "output": copy.deepcopy(document),
        "status": status, "stop_reason": stop, "contract": 1, "nare": "2026.10.0",
        "policy": {"tools": ["read"], "root": str(tmp_path)}, "budget": {"tokens": limit},
    }
    return [event(), result], saved


def decode(tmp_path, events, saved, *, exit_code=0, limit=26):
    stdout = b"\n".join(json.dumps(item).encode() for item in events) + b"\n"
    snapshot = None if saved is None else json.dumps(saved).encode()
    return module().decode_session(
        stdout, snapshot, persona="security", exit_code=exit_code,
        expected_limit=limit, expected_root=tmp_path,
    )


def test_done_exact_threshold_preserves_empty_available_document(tmp_path):
    events, saved = fixture(tmp_path)
    result = decode(tmp_path, events, saved)
    assert result.session_id == "session-1"
    assert result.status == "done"
    assert result.stop_reason == "end_turn"
    assert result.exit_code == 0
    assert result.usage == TokenUsage(3, 5, 7, 11)
    assert result.budget_limit == 26
    assert result.findings == ()
    assert result.output_available is True
    assert result.partial is False
    with pytest.raises(FrozenInstanceError):
        result.partial = True


def test_above_threshold_done_is_partial_with_unclipped_cache_accounting(tmp_path):
    events, saved = fixture(tmp_path, limit=25)
    result = decode(tmp_path, events, saved, limit=25)
    assert result.partial is True
    assert result.usage.total == 26


def test_budget_error_null_output_is_honestly_unavailable(tmp_path):
    events, saved = fixture(tmp_path, status="error", stop="budget")
    events[-1]["output"] = saved["output"] = None
    result = decode(tmp_path, events, saved, exit_code=1)
    assert result.partial is True
    assert result.findings == ()
    assert result.output_available is False


def test_budget_error_with_valid_output_retains_caller_attributed_findings(tmp_path):
    finding = {"file": "review.py", "line": 2, "category": "correctness",
               "problem": "Addition subtracts", "reason": "Wrong value"}
    events, saved = fixture(tmp_path, status="error", stop="budget", output={"findings": [finding]})
    result = decode(tmp_path, events, saved, exit_code=1)
    assert result.partial is True
    assert result.output_available is True
    assert result.findings[0].persona == "security"
    assert result.findings[0].anchor.side == "RIGHT"


def test_provider_error_text_cannot_claim_budget_partial(tmp_path):
    events, saved = fixture(tmp_path, status="error", stop=None)
    events[-1]["error"] = "budget exhausted: provider secret"
    events[-1]["output"] = saved["output"] = None
    assert decode(tmp_path, events, saved, exit_code=1).partial is False


def test_blocked_exit_zero_is_valid_but_not_complete_by_inference(tmp_path):
    events, saved = fixture(tmp_path, status="blocked", stop="tool_use")
    events[-1]["output"] = saved["output"] = None
    assert decode(tmp_path, events, saved).status == "blocked"


def test_never_started_exit_two_without_stdout_has_zero_accounting(tmp_path):
    result = module().decode_session(
        b"", None, persona="security", exit_code=2, expected_limit=26, expected_root=tmp_path,
    )
    assert result.session_id is None
    assert result.status is None
    assert result.usage.total == 0
    assert result.output_available is False
    assert result.partial is False


@pytest.mark.parametrize("stdout", [b"\xff", b"[]\n", b"null\n", b'{"a":1,"a":2}\n',
                                    b'{"a":NaN}\n', b'{"a":Infinity}\n', b'{"a":1e999}\n',
                                    b'{"a":', b'"text"\n'])
def test_invalid_jsonl_is_rejected_with_safe_diagnostic(tmp_path, stdout):
    with pytest.raises(module().SessionProtocolError) as raised:
        module().decode_session(stdout, b"{}", persona="security", exit_code=0,
                                expected_limit=26, expected_root=tmp_path)
    assert "untrusted" not in str(raised.value)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "trailing", "no-session"])
def test_terminal_and_persistence_are_required_once_and_last(tmp_path, mutation):
    events, saved = fixture(tmp_path)
    if mutation == "missing":
        events.pop()
    elif mutation == "duplicate":
        events.append(copy.deepcopy(events[-1]))
    elif mutation == "trailing":
        events.append(event("progress", {}))
    else:
        saved = None
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("status,exit_code", [("done", 1), ("blocked", 1), ("error", 0),
                                             ("done", 2), ("invented", 0), ("done", True)])
def test_status_exit_mismatch_is_rejected(tmp_path, status, exit_code):
    events, saved = fixture(tmp_path, status=status)
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved, exit_code=exit_code)


@pytest.mark.parametrize("field", ["input", "output", "cache_read", "cache_write"])
@pytest.mark.parametrize("bad", [-1, True, False, 1.5, "2", None])
def test_usage_requires_actual_nonnegative_integers(tmp_path, field, bad):
    events, saved = fixture(tmp_path)
    events[0]["detail"][field] = bad
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("bad", [True, "1", -0.1, float("nan"), float("inf")])
def test_cost_requires_finite_nonnegative_number_or_null(tmp_path, bad):
    events, saved = fixture(tmp_path)
    events[0]["detail"]["cost"] = bad
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("field", ["input", "output", "cache_read", "cache_write", "cost"])
def test_missing_usage_fields_fail(tmp_path, field):
    events, saved = fixture(tmp_path)
    del events[-1]["usage"][field]
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("field,value", [("contract", True), ("contract", 2),
                                        ("session_id", ""), ("status", None),
                                        ("stop_reason", 1), ("nare", "")])
def test_terminal_identity_and_contract_are_validated(tmp_path, field, value):
    events, saved = fixture(tmp_path)
    events[-1][field] = value
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("field", ["id", "usage", "output", "status", "stop_reason", "contract",
                                  "nare", "policy", "budget"])
def test_session_required_known_fields_cannot_be_omitted(tmp_path, field):
    events, saved = fixture(tmp_path)
    del saved[field]
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("field,value", [
    ("id", "different"), ("usage", usage(input=4)), ("output", None), ("status", "blocked"),
    ("stop_reason", "budget"), ("contract", True), ("nare", "different"),
    ("policy", {"tools": ["read", "exec"], "root": "bad"}),
    ("budget", {"tokens": 25}),
])
def test_session_known_fields_must_agree(tmp_path, field, value):
    events, saved = fixture(tmp_path)
    saved[field] = value
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("mutation", ["extra-tool", "wrong-root", "relative-root"])
def test_read_policy_is_exactly_read_and_expected_root(tmp_path, mutation):
    events, saved = fixture(tmp_path)
    if mutation == "extra-tool":
        saved["policy"]["tools"] = ["read", "write"]
    elif mutation == "wrong-root":
        saved["policy"]["root"] = str(tmp_path.parent)
    else:
        saved["policy"]["root"] = "."
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("field,value", [("tokens", True), ("tokens", 25),
                                        ("used_tokens", 25), ("used_tokens", True),
                                        ("used_usd", 0), ("usd", 10)])
def test_terminal_budget_is_configured_and_reconciled(tmp_path, field, value):
    events, saved = fixture(tmp_path)
    events[-1]["budget"][field] = value
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


def test_per_turn_sums_reconcile_terminal_vector_and_known_cost(tmp_path):
    events, saved = fixture(tmp_path)
    events[0]["detail"] = usage(input=1, output=2, cache_read=3, cache_write=4, cost=0.1)
    events.insert(1, event(detail=usage(input=2, output=3, cache_read=4, cache_write=7, cost=0.2)))
    events[-1]["usage"]["cost"] = saved["usage"]["cost"] = 0.3
    events[-1]["budget"]["used_usd"] = 0.3
    assert decode(tmp_path, events, saved).usage.total == 26
    events[0]["detail"]["input"] = 2
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


def test_omitted_cost_events_cannot_manufacture_complete_accounting(tmp_path):
    events, saved = fixture(tmp_path)
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events[1:], saved)


def test_unknown_additive_fields_and_types_are_accepted_without_cost_inference(tmp_path):
    events, saved = fixture(tmp_path)
    events.insert(0, event("new-event", {"usage": "not known accounting"}))
    events[-1]["future"] = "additive"
    saved["future"] = "additive"
    assert decode(tmp_path, events, saved).usage.total == 26


@pytest.mark.parametrize("field,value", [("type", 1), ("text", {}), ("detail", []),
                                        ("timestamp", None)])
def test_even_unknown_events_require_valid_envelope(tmp_path, field, value):
    events, saved = fixture(tmp_path)
    events.insert(0, event("future", {}))
    events[0][field] = value
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


def test_latest_terminal_document_is_canonical_without_aggregating_earlier_output(tmp_path):
    events, saved = fixture(tmp_path)
    earlier = {"findings": [{"file": "a.py", "line": 1, "category": "docs",
                             "problem": "Old", "reason": "Earlier document"}]}
    events.insert(0, event("output", {"output": earlier}))
    assert decode(tmp_path, events, saved).findings == ()
    assert events[0]["detail"]["output"] == earlier


@pytest.mark.parametrize("bad", [None, {}, {"findings": None},
                                {"findings": [], "verdict": "approve"},
                                {"findings": [None]}, {"findings": [{"file": "../escape"}]}])
@pytest.mark.parametrize("budget", [False, True])
def test_malformed_output_cannot_be_masked_as_budget_partial(tmp_path, bad, budget):
    events, saved = fixture(tmp_path, status="error" if budget else "done",
                            stop="budget" if budget else "end_turn")
    events[-1]["output"] = saved["output"] = bad
    if bad is None and budget:
        assert decode(tmp_path, events, saved, exit_code=1).output_available is False
    else:
        with pytest.raises(module().SessionProtocolError):
            decode(tmp_path, events, saved, exit_code=1 if budget else 0)


@pytest.mark.parametrize("field,value", [("line", True), ("line", 0), ("side", "BOTH"),
                                        ("category", "invented"), ("problem", " "),
                                        ("persona", "model"), ("severity", "high"),
                                        ("verdict", "approve"), ("file", "/raw")])
def test_parse_finding_owns_semantics_and_rejects_model_attribution(tmp_path, field, value):
    item = {"file": "a.py", "line": 1, "category": "docs", "problem": "P", "reason": "R"}
    item[field] = value
    events, saved = fixture(tmp_path, output={"findings": [item]})
    with pytest.raises(module().SessionProtocolError) as raised:
        decode(tmp_path, events, saved)
    assert "model" not in str(raised.value)
    assert "/raw" not in str(raised.value)


def test_incremental_cost_parser_uses_direct_detail_and_validates_envelope():
    value = module().decode_cost_event(json.dumps(event()).encode())
    assert value == TokenUsage(3, 5, 7, 11)
    assert module().decode_cost_event(json.dumps(event("future", {})).encode()) is None
    with pytest.raises(module().SessionProtocolError):
        module().decode_cost_event(json.dumps(event(detail={"usage": usage()})).encode())


def test_schema_is_supported_closed_and_fresh():
    schema = module().findings_schema()
    allowed = {"type", "properties", "required", "items", "enum", "additionalProperties"}

    def check(node):
        assert set(node) <= allowed
        for child in node.get("properties", {}).values():
            check(child)
        if "items" in node:
            check(node["items"])

    check(schema)
    assert schema["required"] == ["findings"]
    assert schema["additionalProperties"] is False
    item = schema["properties"]["findings"]["items"]
    assert item["required"] == ["file", "line", "category", "problem", "reason"]
    assert set(item["properties"]) == {"file", "line", "side", "category", "problem", "reason"}
    assert item["properties"]["side"]["enum"] == ["LEFT", "RIGHT"]
    assert item["additionalProperties"] is False
    item["properties"].clear()
    assert "file" in module().findings_schema()["properties"]["findings"]["items"]["properties"]


@pytest.mark.parametrize("turns", [True, -1, 1.5, None])
def test_terminal_turns_are_actual_nonnegative_integer(tmp_path, turns):
    events, saved = fixture(tmp_path)
    events[-1]["turns"] = turns
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


def test_capability_identity_and_turn_count_are_preserved(tmp_path):
    events, saved = fixture(tmp_path)
    result = decode(tmp_path, events, saved)
    assert result.nare_version == "2026.10.0"
    assert result.contract == 1
    assert result.turns == 1


@pytest.mark.parametrize("field", ["session_id", "status", "stop_reason", "usage",
                                  "contract", "nare",
                                  "budget", "output", "questions", "error", "turns"])
def test_terminal_known_fields_cannot_be_missing(tmp_path, field):
    events, saved = fixture(tmp_path)
    del events[-1][field]
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


@pytest.mark.parametrize("field,value", [("questions", {}), ("questions", [1]), ("error", {})])
def test_terminal_auxiliary_fields_are_well_typed(tmp_path, field, value):
    events, saved = fixture(tmp_path)
    events[-1][field] = value
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved)


def test_jsonl_uses_literal_newline_boundaries_and_accepts_unicode_event_text(tmp_path):
    events, saved = fixture(tmp_path)
    events[0]["text"] = "line separator \u2028 remains JSON string content"
    stdout = "\n".join(json.dumps(item, ensure_ascii=False) for item in events).encode()
    result = module().decode_session(stdout, json.dumps(saved).encode(), persona="security",
                                     exit_code=0, expected_limit=26, expected_root=tmp_path)
    assert result.usage.total == 26


def test_budget_event_cumulative_usage_and_limit_must_match_terminal(tmp_path):
    events, saved = fixture(tmp_path, status="error", stop="budget")
    events.insert(1, event("error", {"budget": {"tokens": 26, "used_tokens": 26, "used_usd": None},
                                    "usage": usage(input=4)}))
    with pytest.raises(module().SessionProtocolError):
        decode(tmp_path, events, saved, exit_code=1)


@pytest.fixture
def correction_descriptor(capture):
    from test_persona_inputs import reanchor_descriptor

    return reanchor_descriptor(capture)


def decode_correction(descriptor, events, saved, *, exit_code=0, limit=26):
    assert hasattr(module(), "decode_reanchor_session"), "typed correction decoder is missing"
    return module().decode_reanchor_session(
        b"\n".join(json.dumps(item).encode() for item in events),
        None if saved is None else json.dumps(saved).encode(), descriptor=descriptor,
        exit_code=exit_code, expected_limit=limit,
    )


@pytest.mark.parametrize("status,stop,exit_code,limit,partial", [
    ("done", "end_turn", 0, 26, False), ("done", "end_turn", 0, 25, True),
    ("error", "budget", 1, 26, True),
])
def test_correction_decoder_keeps_metadata_and_anchor_only_bindings(
    correction_descriptor, status, stop, exit_code, limit, partial,
):
    from scrutare.findings import Anchor, ReanchorCorrection

    descriptor = correction_descriptor
    output = {"corrections": [{"request_id": "r0002", "file": "src/app.py", "line": 3,
                               "side": "LEFT"}]}
    events, saved = fixture(descriptor.inputs.root, status=status, stop=stop,
                            output=output, limit=limit)
    result = decode_correction(descriptor, events, saved, exit_code=exit_code, limit=limit)
    assert result.corrections == (ReanchorCorrection(descriptor.requests[1].original,
                                                    Anchor("src/app.py", 3, "LEFT")),)
    assert (result.session_id, result.status, result.stop_reason, result.exit_code,
            result.usage, result.output_available, result.budget_limit, result.partial,
            result.nare_version, result.contract, result.turns) == (
        "session-1", status, stop, exit_code, TokenUsage(3, 5, 7, 11), True, limit, partial,
        "2026.10.0", 1, 1,
    )
    with pytest.raises(FrozenInstanceError):
        result.corrections = ()


@pytest.mark.parametrize("available", [False, True])
def test_correction_decoder_distinguishes_missing_from_empty_partial(
    correction_descriptor, available,
):
    descriptor = correction_descriptor
    events, saved = fixture(descriptor.inputs.root, status="error", stop="budget",
                            output={"corrections": []})
    if not available:
        events[-1]["output"] = saved["output"] = None
    result = decode_correction(descriptor, events, saved, exit_code=1)
    assert result.corrections == () and result.partial
    assert result.output_available is available


@pytest.mark.parametrize("mutation", [
    "saved-bool-line", "unknown-id", "duplicate-id", "extra-field", "missing-side",
    "saved-mismatch", "missing-terminal", "duplicate-terminal", "missing-session",
    "usage", "cost-event", "root", "tools", "budget", "contract", "exit",
    "resume-cumulative", "duplicate-json-key",
])
def test_correction_decoder_refuses_payload_and_accounting_corruption(
    correction_descriptor, mutation,
):
    descriptor = correction_descriptor
    item = {"request_id": "r0001", "file": "src/app.py", "line": 1, "side": "RIGHT"}
    events, saved = fixture(descriptor.inputs.root, output={"corrections": [item]})
    exit_code = 0
    if mutation == "saved-bool-line":
        saved["output"]["corrections"][0]["line"] = True
    elif mutation == "unknown-id":
        item["request_id"] = "r9999"
    elif mutation == "duplicate-id":
        events[-1]["output"]["corrections"].append(copy.deepcopy(item))
    elif mutation == "extra-field":
        item["persona"] = "MODEL_SECRET"
    elif mutation == "missing-side":
        del item["side"]
    elif mutation == "saved-mismatch":
        saved["output"]["corrections"] = []
    elif mutation == "missing-terminal":
        events.pop()
    elif mutation == "duplicate-terminal":
        events.append(copy.deepcopy(events[-1]))
    elif mutation == "missing-session":
        saved = None
    elif mutation == "usage":
        events[-1]["usage"]["input"] = True
    elif mutation == "cost-event":
        events.pop(0)
    elif mutation == "root":
        saved["policy"]["root"] = str(descriptor.inputs.root.parent)
    elif mutation == "tools":
        saved["policy"]["tools"] = ["read", "write"]
    elif mutation == "budget":
        saved["budget"]["tokens"] = 25
    elif mutation == "contract":
        saved["contract"] = True
    elif mutation == "exit":
        exit_code = 1
    elif mutation == "resume-cumulative":
        events[-1]["usage"]["input"] += 10
        saved["usage"]["input"] += 10
        events[-1]["budget"]["used_tokens"] += 10
    elif mutation == "duplicate-json-key":
        with pytest.raises(module().SessionProtocolError):
            assert hasattr(module(), "decode_reanchor_session"), "correction decoder is missing"
            module().decode_reanchor_session(
                b'{"type":"result","type":"result"}', json.dumps(saved).encode(),
                descriptor=descriptor, exit_code=0, expected_limit=26,
            )
        return
    with pytest.raises(module().SessionProtocolError) as raised:
        decode_correction(descriptor, events, saved, exit_code=exit_code)
    assert "MODEL_SECRET" not in str(raised.value)


def test_correction_decoder_never_started_has_zero_accounting(correction_descriptor):
    assert hasattr(module(), "decode_reanchor_session"), "typed correction decoder is missing"
    result = module().decode_reanchor_session(
        b"", None, descriptor=correction_descriptor, exit_code=2, expected_limit=26,
    )
    assert result.session_id is None and result.status is None
    assert result.usage == TokenUsage() and result.corrections == ()
    assert not result.output_available and not result.partial
