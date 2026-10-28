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


def test_replay_does_not_owe_correction_to_invalid_original_state(capture, tmp_path):
    launch(capture, tmp_path, PLACEHOLDER, first_step={"status": "done", "stop": "budget"})
    assert issues(capture) == ()


@pytest.mark.parametrize("rehash", [False, True])
def test_reconstructed_assessor_pool_must_match_task_hash_and_phase_pool(capture, tmp_path, rehash):
    from hashlib import sha256

    second = assessments(*IDS)
    for row in second["assessments"]:
        row.update(
            status="refuted", counter_citations=row["supporting_citations"], supporting_citations=[]
        )
    _, _, attempt = launch(
        capture, tmp_path, assessments(*IDS, "unknown"), second, kind="assessment"
    )
    inv = load(attempt / "invocation.json")
    # This test supplies the authoritative assessment-phase artifact, as a full review does.
    task = inv["argv"][2]
    data, _ = json.JSONDecoder().raw_decode(task.split("\n", 1)[1])
    (capture / "assessment.json").write_text(json.dumps({"candidates": data["candidates"]}))
    changed = task.replace('"problem":"failure"', '"problem":"changed"')
    assert changed != task
    inv["argv"][2] = changed
    if rehash:
        inv["task_prompt_sha256"] = sha256(changed.encode()).hexdigest()
    (attempt / "invocation.json").write_text(json.dumps(inv))
    assert issues(capture)


@pytest.mark.parametrize("kind", ["assessment", "discussion"])
def test_original_and_correction_schema_must_be_canonical(capture, tmp_path, kind):
    from test_protocol_correction_session import proposals

    first, second = (
        (assessments(*IDS, "unknown"), assessments(*IDS))
        if kind == "assessment"
        else (proposals(*IDS, "unknown"), proposals(*IDS))
    )
    _, _, attempt = launch(capture, tmp_path, first, second, kind=kind)
    for name in ("findings.schema.json", "protocol-correction.schema.json"):
        doc = load(attempt / name)
        key, field = ("assessments", "status") if kind == "assessment" else ("proposals", "action")
        doc["properties"][key]["items"]["properties"][field]["enum"].append("invented")
        (attempt / name).write_text(json.dumps(doc))
    assert issues(capture)


@pytest.mark.parametrize(
    "files",
    [
        ("findings.schema.json", "protocol-correction.schema.json"),
        ("protocol-correction.schema.json",),
    ],
)
def test_schema_boolean_integer_confusion_is_rejected(capture, tmp_path, files):
    _, _, attempt = launch(
        capture, tmp_path, assessments(*IDS, "unknown"), assessments(*IDS), kind="assessment"
    )
    for name in files:
        value = load(attempt / name)
        value["additionalProperties"] = 0
        (attempt / name).write_text(json.dumps(value))
    assert issues(capture)


def test_invalid_resumed_done_budget_fails_and_replays(capture, tmp_path):
    outcome, _, attempt = launch(
        capture, tmp_path, PLACEHOLDER, correction={"status": "done", "stop": "budget"}
    )
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert load(attempt / "result.json")["protocol_correction"]["outcome"] == "failed"
    assert issues(capture) == ()


def test_corrected_overrun_cannot_be_relabelled_as_complete(capture, tmp_path):
    _, _, attempt = launch(capture, tmp_path, PLACEHOLDER, limit=100)
    result = load(attempt / "result.json")
    result.update(status="complete", reason="done", overshoot_tokens=0)
    (attempt / "result.json").write_text(json.dumps(result))
    assert issues(capture)


def test_replay_validates_correction_constraint_mode(capture, tmp_path):
    from scrutare.config import ModelRail

    _, _, attempt = launch(
        capture, tmp_path, PLACEHOLDER, rail=ModelRail("openai", None, "m", "json_schema")
    )
    path = attempt / "correction.stdout.jsonl"
    path.write_text(path.read_text().replace('"mode": "json_schema"', '"mode": "wrong-mode"'))
    assert issues(capture)
