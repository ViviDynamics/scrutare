"""Review latency records: what each session and review writes, and how replay checks it."""

import json

import pytest
from test_capacity_config import FIXED
from test_capacity_session import launch, native, queued
from test_rail_options_session import RETRY, run
from test_review_inputs import capture as capture

from scrutare.config import ModelRail
from scrutare.engine.session_output import retry_events
from scrutare.engine.timing import (
    SessionTiming,
    TimingError,
    footer_line,
    read_session_timings,
    review_timing,
    validate_review_timing,
    write_review_timing,
)
from scrutare.replay.audit import _timing_issues


def load(path):
    return json.loads(path.read_bytes())


def edit(path, change):
    document = load(path)
    change(document)
    path.write_text(json.dumps(document))


def codes(run_dir):
    return [(issue.code, issue.path.rsplit("/", 1)[-1], issue.severity)
            for issue in _timing_issues(run_dir)]


def session(**changes):
    fields = {"provider": "openai", "model": "m", "invocations": 1, "wall_seconds": 12.5,
              "tokens": 100, "output_tokens": 40} | changes
    return SessionTiming(**fields)


# Session records


def test_a_launched_session_records_its_timing_beside_its_result(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, native(tmp_path))
    assert outcome.status == "complete"
    timing = load(attempt / "timing.json")
    assert timing.pop("wall_seconds") > 0
    assert timing == {
        "schema_version": 1, "provider": "openai", "model": "m", "invocations": 1,
        "tokens": outcome.usage.total, "output_tokens": outcome.usage.output, "retries": 0,
        "retry_wait_seconds": 0.0, "capacity_wait_seconds": 0.0,
        "capacity_slot_wait_seconds": 0.0, "first_token_seconds": None,
        "decode_tokens_per_second": None}
    # A measurement, never part of the outcome nor of result.json.
    assert "timing" not in load(attempt / "result.json")
    assert codes(capture) == []


def test_retry_waits_are_the_ones_the_stream_announced(capture, tmp_path):
    rail = ModelRail("openai", None, "m", request_retries=2)
    outcome, attempt, _ = run(capture, tmp_path, rail, extra=RETRY + RETRY, retries=2)
    assert outcome.status == "complete"
    timing = load(attempt / "timing.json")
    assert (timing["retries"], timing["retry_wait_seconds"]) == (2, 4.0)
    assert codes(capture) == []


def test_capacity_waits_are_the_recorded_capacity_counters(capture, tmp_path):
    outcome, _, attempt = launch(capture, queued(tmp_path), FIXED)
    assert outcome.capacity.wait_seconds == 2.5
    assert load(attempt / "timing.json")["capacity_wait_seconds"] == 2.5
    assert codes(capture) == []


def test_a_failed_session_still_records_its_timing(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path, status="error", exit_code=1))
    assert outcome.status == "failed"
    assert load(attempt / "timing.json")["invocations"] == 1
    assert codes(capture) == []


def test_retry_events_skip_what_they_cannot_read():
    good = json.dumps({"type": "progress", "text": "", "timestamp": "t",
                       "detail": {"retry": {"wait": 2.0}}}).encode()
    bad = json.dumps({"type": "progress", "text": "", "timestamp": "t",
                      "detail": {"retry": {"wait": -1}}}).encode()
    assert retry_events(b"\n".join([good, b"not json", bad, good, b""])) == (2, 4.0)
    assert retry_events(b"") == (0, 0.0)


@pytest.mark.parametrize("changes", [
    {"provider": ""}, {"invocations": 0}, {"tokens": -1}, {"output_tokens": 101},
    {"wall_seconds": -0.5}, {"wall_seconds": float("inf")}, {"retries": True},
])
def test_a_session_record_must_be_well_formed(changes):
    with pytest.raises(TimingError):
        session(**changes)


@pytest.mark.parametrize("change", [
    lambda d: d.update(first_token_seconds=0.4),
    lambda d: d.update(extra=1),
    lambda d: d.pop("retries"),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(wall_seconds=1.23456),
])
def test_a_session_document_must_be_exactly_the_record(change):
    document = session().to_dict()
    change(document)
    with pytest.raises(TimingError):
        SessionTiming.from_dict(document)


# Replay


def test_runs_without_timing_are_not_audited(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    (attempt / "timing.json").unlink()
    (attempt / "result.json").write_text("not json")
    assert _timing_issues(capture) == ()


@pytest.mark.parametrize("change", [
    lambda d: d.update(provider="anthropic"),
    lambda d: d.update(model="other"),
    lambda d: d.update(tokens=d["tokens"] + 1),
    lambda d: d.update(output_tokens=d["output_tokens"] + 1),
    lambda d: d.update(invocations=2),
    lambda d: d.update(retries=1, retry_wait_seconds=2.0),
    lambda d: d.update(capacity_wait_seconds=1.0),
    lambda d: d.update(decode_tokens_per_second=10.0),
    lambda d: d.update(wall_seconds="fast"),
])
def test_a_session_record_must_agree_with_its_retained_evidence(capture, tmp_path, change):
    outcome, _, attempt = launch(capture, native(tmp_path))
    edit(attempt / "timing.json", change)
    assert codes(capture) == [("timing_disagrees", "timing.json", "invalid")]


def test_wall_clock_is_a_measurement_and_is_not_compared(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    edit(attempt / "timing.json", lambda d: d.update(wall_seconds=9999.0))
    assert codes(capture) == []


def test_a_launched_session_without_its_record_is_incomplete(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    write_review_timing(capture, 30.0)
    (attempt / "timing.json").unlink()
    assert codes(capture) == [("timing_missing", "timing.json", "incomplete")]


def test_the_review_record_must_be_the_sum_of_its_sessions(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    written = write_review_timing(capture, 30.0)
    assert written["sessions"] == 1 and written["wall_seconds"] == 30.0
    assert written["rails"] == [{"provider": "openai", "model": "m", "sessions": 1,
                                 "session_wall_seconds": written["session_wall_seconds"],
                                 "tokens": outcome.usage.total,
                                 "output_tokens": outcome.usage.output}]
    assert load(capture / "timing.json") == written
    assert codes(capture) == []
    edit(capture / "timing.json", lambda d: d.update(tokens=d["tokens"] + 1))
    assert codes(capture) == [("timing_disagrees", "timing.json", "invalid")]


def test_a_review_cannot_be_shorter_than_one_of_its_sessions(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    edit(attempt / "timing.json", lambda d: d.update(wall_seconds=50.0))
    write_review_timing(capture, 20.0)
    assert codes(capture) == [("timing_disagrees", "timing.json", "invalid")]


def test_review_totals_and_rails():
    sessions = [session(), session(model="n", wall_seconds=2.25, retries=1,
                                   retry_wait_seconds=2.0, capacity_slot_wait_seconds=0.5)]
    document = review_timing(20.0, sessions)
    assert document == {
        "schema_version": 1, "wall_seconds": 20.0, "sessions": 2,
        "session_wall_seconds": 14.75, "longest_session_seconds": 12.5, "tokens": 200,
        "output_tokens": 80, "retries": 1, "retry_wait_seconds": 2.0,
        "capacity_wait_seconds": 0.0, "capacity_slot_wait_seconds": 0.5,
        "first_token_seconds": None, "decode_tokens_per_second": None,
        "rails": [{"provider": "openai", "model": "m", "sessions": 1,
                   "session_wall_seconds": 12.5, "tokens": 100, "output_tokens": 40},
                  {"provider": "openai", "model": "n", "sessions": 1,
                   "session_wall_seconds": 2.25, "tokens": 100, "output_tokens": 40}]}
    assert validate_review_timing(json.loads(json.dumps(document)), sessions) == document
    with pytest.raises(TimingError):
        validate_review_timing(review_timing(10.0, sessions), sessions)
    with pytest.raises(TimingError):
        validate_review_timing(document | {"rails": []}, sessions)


def test_a_malformed_session_record_fails_the_review_record(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path))
    (attempt / "timing.json").write_text("{}")
    with pytest.raises(TimingError):
        read_session_timings(capture)


@pytest.mark.parametrize("seconds,text", [
    (4.4, "4 s"), (59.6, "1 min 0 s"), (725, "12 min 5 s"), (7322, "2 h 2 min")])
def test_footer_line(seconds, text):
    document = review_timing(seconds, [session(wall_seconds=min(seconds, 1.0))])
    assert footer_line(document) == f"Review time: {text}, 1 session, 100 tokens (40 output)"
    two = review_timing(seconds, [session(wall_seconds=1.0)] * 2)
    assert footer_line(two).endswith(", 2 sessions, 200 tokens (80 output)")
