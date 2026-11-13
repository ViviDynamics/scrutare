"""Capacity flags and counters replay and audit; runs without them audit as before."""

import json
from dataclasses import replace

import pytest
from test_capacity_config import ADAPTIVE, FIXED
from test_capacity_session import cut, launch, limited, native, queued, refusal, slot
from test_review_inputs import capture as capture

from scrutare.config import CapacitySettings, ModelSettings
from scrutare.replay.audit import (
    _capacity_issues,
    _citation_correction_issues,
    _protocol_correction_issues,
)


def config_data(capture):
    return json.loads((capture / "config.json").read_text())


def codes(capture, data=None):
    return [(issue.code, issue.path.rsplit("/", 1)[-1], issue.severity)
            for issue in _capacity_issues(capture, data or config_data(capture))]


def edit(path, change):
    document = json.loads(path.read_text())
    replaced = change(document)
    path.write_text(json.dumps(document if replaced is None else replaced))


@pytest.mark.parametrize("capacity,script", [(FIXED, queued), (ADAPTIVE, limited)])
def test_recorded_capacity_run_audits_clean(capture, tmp_path, capacity, script):
    outcome, _, attempt = launch(capture, script(tmp_path), capacity)
    assert outcome.status == "complete"
    assert _capacity_issues(capture, config_data(capture)) == ()


def test_a_session_that_queued_nothing_audits_clean(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path), FIXED)
    assert outcome.status == "complete" and outcome.capacity.refusals == 0
    assert codes(capture) == []


def test_runs_without_capacity_are_not_audited(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    assert outcome.status == "complete"
    data = config_data(capture)
    assert "capacity" not in data["models"]
    assert _capacity_issues(capture, data) == ()
    # Nothing recorded and nothing configured: not even a broken bundle is inspected.
    (attempt / "result.json").write_text("not json")
    assert _capacity_issues(capture, data) == ()
    assert _capacity_issues(capture, {"models": 3}) == ()


@pytest.mark.parametrize("mutation", [
    lambda argv: [item for item in argv if item != "--capacity-queue"],
    lambda argv: [("2" if item == "50" else item) for item in argv],
    lambda argv: [*argv, "--capacity-refusals", "50"],
    lambda argv: [("--capacity-refusals=50" if item == "--capacity-refusals" else item)
                  for item in argv if item != "50"],
    lambda argv: [("/elsewhere" if item.endswith("/capacity") else item) for item in argv],
    lambda argv: [item for item in argv if item != "--capacity-adaptive"],
])
def test_changed_or_lost_capacity_flags_are_invalid(capture, tmp_path, mutation):
    outcome, _, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    edit(attempt / "invocation.json",
         lambda document: document.update(argv=mutation(document["argv"])))
    assert codes(capture) == [("capacity_unrecorded", "invocation.json", "invalid")]


def test_flags_without_configuration_are_invalid(capture, tmp_path):
    outcome, _, attempt = launch(capture, queued(tmp_path), FIXED)
    data = config_data(capture)
    del data["models"]["capacity"]
    assert [code for code, _, _ in codes(capture, data)] == ["capacity_unrecorded"]


def test_configuration_without_flags_is_invalid(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    data = config_data(capture)
    data["models"]["capacity"] = FIXED
    assert [code for code, _, _ in codes(capture, data)] == ["capacity_unrecorded"]


@pytest.mark.parametrize("change", ["missing", "extra_field", "negative", "unexpected"])
def test_result_must_record_exactly_the_queued_counters(capture, tmp_path, change):
    outcome, _, attempt = launch(capture, queued(tmp_path), FIXED)
    path = attempt / "result.json"
    result = json.loads(path.read_text())
    if change == "missing":
        del result["capacity"]
    elif change == "extra_field":
        result["capacity"]["tokens"] = 1
    elif change == "negative":
        result["capacity"]["refusals"] = -1
    path.write_text(json.dumps(result))
    data = config_data(capture)
    if change == "unexpected":
        # A session that never queued must not claim counters either.
        def strip(document):
            document["argv"] = [item for item in document["argv"]
                                if not item.startswith("--capacity")
                                and item not in ("1800", "50")]
        edit(attempt / "invocation.json", strip)
        del data["models"]["capacity"]
    assert [code for code, _, _ in codes(capture, data)] == ["capacity_result_disagrees"]


@pytest.mark.parametrize("change", [
    lambda record: record.update(refusals=0),
    lambda record: record.update(refusals=2),
    lambda record: record.update(wait_seconds=2.0),
    lambda record: record.update(wait_seconds=0.0),
    lambda record: record.update(slot_wait_seconds=0.5),
    lambda record: record.update(slot_wait_seconds=0.0),
    lambda record: record.update(source="stream"),
    lambda record: record.update(limit=True),
])
def test_recorded_counters_are_recomputed_from_the_retained_streams(capture, tmp_path, change):
    outcome, _, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    assert codes(capture) == []
    edit(attempt / "result.json", lambda result: change(result["capacity"]))
    assert codes(capture) == [("capacity_result_disagrees", "result.json", "invalid")]


def test_the_recorded_counters_must_match_the_retained_stream(capture, tmp_path):
    outcome, _, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    stream = attempt / "stdout.jsonl"
    original = stream.read_bytes()
    # A queue record removed, then the same records announcing other seconds.
    stream.write_bytes(original.replace(b'"capacity":', b'"other":'))
    assert codes(capture) == [("capacity_result_disagrees", "result.json", "invalid")]
    stream.write_bytes(original.replace(b'"wait": 2.5', b'"wait": 0.5'))
    assert codes(capture) == [("capacity_result_disagrees", "result.json", "invalid")]
    stream.write_bytes(original.replace(b'"waited": 0.25}', b'"waited": 1.25}'))
    assert codes(capture) == [("capacity_result_disagrees", "result.json", "invalid")]
    stream.write_bytes(original)
    assert codes(capture) == []
    stream.unlink()
    assert codes(capture) == [("capacity_evidence_missing", "stdout.jsonl", "incomplete")]


def test_counters_taken_from_events_are_recomputed_exactly(capture, tmp_path):
    path = limited(tmp_path, events=refusal(0.25) + cut(4, 8) + slot(4, 0.25)
                   + "import time\ntime.sleep(30)\n")
    outcome, _, attempt = launch(capture, path, ADAPTIVE, timeout=1.5)
    assert outcome.reason == "timeout" and outcome.capacity.source == "events"
    assert codes(capture) == []
    edit(attempt / "result.json", lambda result: result["capacity"].update(wait_seconds=0.251))
    assert codes(capture) == [("capacity_result_disagrees", "result.json", "invalid")]


def test_invalid_configuration_is_reported(capture, tmp_path):
    launch(capture, queued(tmp_path), FIXED)
    data = config_data(capture)
    data["models"]["capacity"] = {"mode": "fixed"}
    assert [code for code, _, _ in codes(capture, data)] == ["capacity_config_invalid"]


@pytest.mark.parametrize("protocol", [False, True])
@pytest.mark.parametrize("flags", [
    ["--capacity-refusals", "2"],
    ["--capacity-refusals=50"],
    [],
])
def test_correction_carries_and_cannot_change_capacity_flags(
        capture, tmp_path, monkeypatch, protocol, flags):
    import test_citation_correction_defects as defects
    import test_citation_correction_session as citation
    import test_protocol_correction_session as correction

    # Extend the scripted external CLI's accepted flags; execute real session/correction code.
    monkeypatch.setattr(citation, "BODY", citation.BODY.replace(
        '"max-tokens"):', '"max-tokens", "capacity-wait-seconds", "capacity-refusals"):')
        .replace('("yes", "jsonl", "stream")', '("yes", "jsonl", "stream", "capacity-queue")'))
    capable = replace(citation.RUNTIME, capacity=("fixed",))
    monkeypatch.setattr(citation, "RUNTIME", capable)
    monkeypatch.setattr(correction, "RUNTIME", capable)
    models = replace(citation.OPENAI_MODELS,
                     capacity=CapacitySettings("fixed", wait_seconds=1800, refusals=50))
    assert isinstance(models, ModelSettings)
    monkeypatch.setattr(citation, "OPENAI_MODELS", models)
    monkeypatch.setattr(defects, "OPENAI_MODELS", models)
    if protocol:
        outcome, _, attempt = correction.launch(capture, tmp_path, correction.PLACEHOLDER)
        stem, audit = "protocol-correction", _protocol_correction_issues
    else:
        outcome, _, attempt = citation.launch(capture, tmp_path, citation.PAST_END,
            {"output": citation.fix(("c0001", citation.GOOD, 1, 3))})
        stem, audit = "citation-correction", _citation_correction_issues
    assert outcome.status == "complete"
    first = json.loads((attempt / "cwd/observed.json").read_text())["argv"]
    resumed = json.loads((attempt / "cwd/observed-resume.json").read_text())["argv"]
    queue = ["--capacity-queue", "--capacity-wait-seconds", "1800", "--capacity-refusals", "50"]
    for argv in (first, resumed):
        index = argv.index("--capacity-queue")
        assert argv[index:index + 5] == queue
    # This scripted runtime reports no counters, so the queued session counts its events.
    assert outcome.capacity is not None and outcome.capacity.source == "events"
    data = config_data(capture)
    assert data["models"]["capacity"]["mode"] == "fixed"
    assert audit(capture, data) == () and _capacity_issues(capture, data) == ()
    path = attempt / (stem + ".json")
    document = json.loads(path.read_text())
    index = document["argv"].index("--capacity-refusals")
    document["argv"][index:index + 2] = flags
    path.write_text(json.dumps(document))
    assert any(issue.code == stem.replace("-", "_") + "_disagrees"
               for issue in audit(capture, data))
