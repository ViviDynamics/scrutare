"""Capacity flags replay and audit; runs without them audit exactly as before."""

import json
from dataclasses import replace

import pytest
from test_capacity_queue import ADAPTIVE, FIXED, launch, native
from test_review_inputs import capture as capture

from scrutare.config import CapacitySettings, ModelSettings
from scrutare.replay.audit import (
    _capacity_issues,
    _citation_correction_issues,
    _protocol_correction_issues,
)


def config_data(capture):
    return json.loads((capture / "config.json").read_text())


@pytest.mark.parametrize("capacity", [FIXED, ADAPTIVE])
def test_recorded_capacity_run_audits_clean(capture, tmp_path, capacity):
    outcome, _, attempt = launch(capture, native(tmp_path), capacity)
    assert outcome.status == "complete"
    assert _capacity_issues(capture, config_data(capture)) == ()


def test_runs_without_capacity_are_not_audited(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    assert outcome.status == "complete"
    data = config_data(capture)
    assert "capacity" not in data["models"]
    assert _capacity_issues(capture, data) == ()
    # Nothing recorded and nothing configured: not even a broken bundle is inspected.
    (attempt / "result.json").write_text("not json")
    assert _capacity_issues(capture, data) == ()


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
    outcome, _, attempt = launch(capture, native(tmp_path), ADAPTIVE)
    path = attempt / "invocation.json"
    document = json.loads(path.read_text())
    document["argv"] = mutation(document["argv"])
    path.write_text(json.dumps(document))
    assert [issue.code for issue in _capacity_issues(capture, config_data(capture))] == [
        "capacity_unrecorded"]


def test_flags_without_configuration_are_invalid(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path), FIXED)
    data = config_data(capture)
    del data["models"]["capacity"]
    assert [issue.code for issue in _capacity_issues(capture, data)] == ["capacity_unrecorded"]


def test_configuration_without_flags_is_invalid(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    data = config_data(capture)
    data["models"]["capacity"] = FIXED
    codes = [issue.code for issue in _capacity_issues(capture, data)]
    assert codes == ["capacity_unrecorded"]


@pytest.mark.parametrize("change", ["missing", "extra_field", "negative", "unexpected"])
def test_result_must_record_exactly_the_queued_counters(capture, tmp_path, change):
    outcome, _, attempt = launch(capture, native(tmp_path), FIXED)
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
        launch_dir = attempt / "invocation.json"
        invocation = json.loads(launch_dir.read_text())
        invocation["argv"] = [item for item in invocation["argv"]
                              if not item.startswith("--capacity") and item not in ("1800", "50")]
        launch_dir.write_text(json.dumps(invocation))
        del data["models"]["capacity"]
    assert [issue.code for issue in _capacity_issues(capture, data)] == [
        "capacity_result_disagrees"]


def test_invalid_configuration_is_reported(capture, tmp_path):
    launch(capture, native(tmp_path), FIXED)
    data = config_data(capture)
    data["models"]["capacity"] = {"mode": "fixed"}
    assert [issue.code for issue in _capacity_issues(capture, data)] == [
        "capacity_config_invalid"]


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
