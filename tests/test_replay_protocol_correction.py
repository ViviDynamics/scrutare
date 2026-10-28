"""Replay proves why a single protocol correction was owed and what it returned."""

import json

import pytest
from test_protocol_correction_session import (
    IDS,
    PLACEHOLDER,
    VALID,
    assessments,
    launch,
    load,
)
from test_review_inputs import capture as capture

from scrutare.replay.audit import _protocol_correction_issues


def issues(capture):
    return _protocol_correction_issues(capture, load(capture / "config.json"))


@pytest.mark.parametrize(
    "kind,first,second",
    [
        ("discovery", PLACEHOLDER, VALID),
        ("discovery", PLACEHOLDER, PLACEHOLDER),
        ("assessment", assessments(*IDS, "unknown"), assessments(*IDS)),
        ("assessment", assessments(*IDS[:-1]), assessments(*IDS)),
    ],
)
def test_all_outcomes_replay(capture, tmp_path, kind, first, second):
    launch(capture, tmp_path, first, second, kind=kind)
    assert issues(capture) == ()


@pytest.mark.parametrize(
    "file,change",
    [
        ("protocol-correction.json", lambda d: d.update(prompt="changed")),
        ("protocol-correction.json", lambda d: d["defects"][0].update(requirement="changed")),
        (
            "protocol-correction.json",
            lambda d: d["argv"].__setitem__(d["argv"].index("none"), "read"),
        ),
        ("protocol-correction.schema.json", lambda d: d.update(required=[])),
        ("result.json", lambda d: d["protocol_correction"].update(outcome="refused")),
        ("result.json", lambda d: d["usage"].update(input=1)),
        ("result.json", lambda d: d.update(findings=[])),
        ("correction-session.json", lambda d: d["output"]["findings"][0].update(line=True)),
    ],
)
def test_tampering_is_rejected(capture, tmp_path, file, change):
    _, _, attempt = launch(capture, tmp_path, PLACEHOLDER)
    document = load(attempt / file)
    change(document)
    (attempt / file).write_text(json.dumps(document))
    assert issues(capture)


def test_missing_and_unconfigured_evidence_is_rejected(capture, tmp_path):
    _, _, attempt = launch(capture, tmp_path, PLACEHOLDER)
    (attempt / "protocol-correction.json").unlink()
    assert issues(capture)
    config = load(capture / "config.json")
    config["findings"].pop("protocol_correction")
    assert _protocol_correction_issues(capture, config)


@pytest.mark.parametrize(
    "step",
    [
        {"status": "error", "stop": "max_turns"},
        {"never_start": True},
        {"corrupt": True},
        {"session_id": "foreign-session"},
    ],
)
def test_unusable_turns_replay(capture, tmp_path, step):
    launch(capture, tmp_path, PLACEHOLDER, correction=step)
    assert issues(capture) == ()


def test_discussion_correction_replays(capture, tmp_path):
    from test_protocol_correction_session import proposals

    launch(capture, tmp_path, proposals(*IDS, "extra"), proposals(*IDS), kind="discussion")
    assert issues(capture) == ()
