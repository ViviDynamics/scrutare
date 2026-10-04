"""Correction identity binds exact originals while the model supplies anchors only."""

import importlib
import importlib.util
from dataclasses import FrozenInstanceError, replace

import pytest

from scrutare.findings import Anchor, Finding, FindingError, ReanchorCorrection


def module():
    assert importlib.util.find_spec("scrutare.engine.reanchor") is not None, (
        "typed reanchor protocol is missing"
    )
    return importlib.import_module("scrutare.engine.reanchor")


def original(line=900, *, persona="security", problem="Same problem"):
    return Finding(Anchor("src/app.py", line), "correctness", problem, "Same reason", persona)


def correction(request_id="r0001", **changes):
    value = {"request_id": request_id, "file": "src/app.py", "line": 3, "side": "RIGHT"}
    value.update(changes)
    return value


def test_ids_distinguish_bad_anchors_and_equal_originals_share_one_request():
    first, second = original(), original(901)
    requests = module().make_reanchor_requests((first, second, replace(first)))
    assert isinstance(requests, tuple)
    assert [(r.request_id, r.original) for r in requests] == [
        ("r0001", first), ("r0002", second),
    ]
    assert requests[0].original is first
    with pytest.raises(FrozenInstanceError):
        requests[0].request_id = "r0002"


def test_each_original_field_participates_in_identity():
    first = original()
    originals = (first, replace(first, category="docs"), replace(first, reason="Other"),
                 replace(first, problem="Other"), replace(first, persona="devops"),
                 replace(first, anchor=Anchor("other.py", 900, "LEFT")))
    assert len(module().make_reanchor_requests(originals)) == 6


@pytest.mark.parametrize("bad", [[], (None,), ("finding",), None])
def test_request_builder_requires_immutable_typed_originals(bad):
    with pytest.raises(FindingError):
        module().make_reanchor_requests(bad)


@pytest.mark.parametrize("request_id", [None, "", "bad", "r0000", "r1", "r0001\n"])
def test_request_constructor_requires_stable_id(request_id):
    with pytest.raises(FindingError):
        module().ReanchorRequest(request_id, original())


def test_request_constructor_requires_typed_original():
    with pytest.raises(FindingError):
        module().ReanchorRequest("r0001", None)


def test_corrections_bind_ids_allow_omissions_and_leave_anchor_existence_to_verification():
    first, second = original(), original(901)
    requests = module().make_reanchor_requests((first, second))
    parsed = module().parse_reanchor_output(
        {"corrections": [correction("r0002", file="excluded.py", line=999, side="LEFT")]},
        requests,
    )
    assert parsed == (ReanchorCorrection(second, Anchor("excluded.py", 999, "LEFT")),)
    assert parsed[0].original is second
    assert module().parse_reanchor_output({"corrections": []}, requests) == ()
    assert module().make_reanchor_requests(()) == ()


@pytest.mark.parametrize("bad", [
    None, [], {}, {"corrections": None}, {"corrections": (), "verdict": "approve"},
    {"corrections": [], "verdict": "approve"}, {"corrections": [None]},
    {"corrections": [correction("r9999")]},
    {"corrections": [correction(), correction()]},
])
def test_malformed_documents_and_unknown_or_duplicate_ids_fail(bad):
    requests = module().make_reanchor_requests((original(),))
    with pytest.raises(FindingError):
        module().parse_reanchor_output(bad, requests)


@pytest.mark.parametrize("field,value", [
    ("line", True), ("line", False), ("line", 0), ("line", -1), ("line", 1.5),
    ("side", "BOTH"), ("file", "../raw"), ("file", "/raw"),
    ("request_id", None), ("problem", "Changed"), ("reason", "Changed"),
    ("category", "docs"), ("persona", "model"), ("verdict", "approve"), ("text", "Changed"),
])
def test_corrections_accept_only_valid_anchor_fields(field, value):
    requests = module().make_reanchor_requests((original(),))
    with pytest.raises(FindingError):
        module().parse_reanchor_output({"corrections": [correction(**{field: value})]}, requests)


@pytest.mark.parametrize("field", ["request_id", "file", "line", "side"])
def test_all_correction_fields_are_required(field):
    requests = module().make_reanchor_requests((original(),))
    item = correction()
    del item[field]
    with pytest.raises(FindingError):
        module().parse_reanchor_output({"corrections": [item]}, requests)


def test_schema_is_closed_supported_required_and_fresh():
    schema = module().reanchor_schema()
    assert schema == {
        "type": "object", "properties": {"corrections": {"type": "array", "items": {
            "type": "object", "properties": {
                "request_id": {"type": "string"}, "file": {"type": "string"},
                "line": {"type": "integer"},
                "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
            }, "required": ["request_id", "file", "line", "side"],
            "additionalProperties": False,
        }}}, "required": ["corrections"], "additionalProperties": False,
    }
    schema["properties"].clear()
    assert "corrections" in module().reanchor_schema()["properties"]
