"""Installed nare proof: per-request timing reaches each session's and the review's record.

nare 2026.11.109 times every model request and sums each invocation's requests on its
result line as `request_timing`. Through the real console and the offline HTTP harness, a
streamed session's record keeps that sum, derives time to first token and decode rate from
it, and replays from the retained streams alone.
"""

import json

from test_installed_citation_correction import first_answer, fixed, review
from test_installed_request_retries import FINAL, READ, events, http_review
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed
from test_review_inputs import capture as capture

from scrutare.engine.timing import RequestTiming, write_review_timing
from scrutare.replay import replay_run
from scrutare.replay.audit import _timing_issues

FIELDS = {"requests", "timed", "headers_seconds", "total_seconds", "first_tokens",
          "first_token_seconds", "generation_seconds", "generation_output_tokens"}


def load(path):
    return json.loads(path.read_bytes())


def derived(block):
    timing = RequestTiming.from_dict(block)
    return timing.mean_first_token_seconds, timing.decode_tokens_per_second


def test_a_streamed_session_records_nare_request_timing(capture, tmp_path, installed):
    result, outcome, observed = http_review(capture, tmp_path, installed,
                                            [{"frames": READ}, {"frames": FINAL}])
    assert result.status == "complete"
    attempt = outcome.artifact_directory
    stream = events(attempt)
    terminal = stream[-1]
    assert terminal["type"] == "result" and set(terminal["request_timing"]) == FIELDS
    costs = [e["detail"] for e in stream if e["type"] == "cost"]
    assert len(costs) == 2 and all(set(c["timing"]) == {
        "headers_seconds", "first_token_seconds", "total_seconds", "generation_seconds"}
        for c in costs)
    # The default review is read-only here: nothing new is sent for timing.
    assert "--stream" in load(attempt / "invocation.json")["argv"]
    timing = load(attempt / "timing.json")
    assert timing["schema_version"] == 2
    assert timing["requests"] == terminal["request_timing"]
    block = timing["requests"]
    # Streamed through a real transport, every answered request is timed and has a first token.
    assert block["requests"] == block["timed"] == block["first_tokens"] == 2
    assert block["first_token_seconds"] is not None
    assert (timing["first_token_seconds"], timing["decode_tokens_per_second"]) == derived(block)
    assert timing["first_token_seconds"] is not None
    review = write_review_timing(capture, max(30.0, timing["wall_seconds"]))
    assert review["requests"] == block
    assert (review["first_token_seconds"], review["decode_tokens_per_second"]) == derived(block)
    assert _timing_issues(capture) == ()
    replayed = replay_run(capture)
    assert replayed.issues == () and replayed.exit_code == 0


def test_a_corrected_session_sums_both_invocations_request_timing(
        tmp_path, installed, wheel_cli):
    result, run, attempt, replay, _ = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: fixed(p, end_line=1))
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == "corrected"
    first, second = events(attempt)[-1], events(attempt, "correction.stdout.jsonl")[-1]
    # Per invocation on nare's side, unlike its retry count, so the session adds the two.
    total = (RequestTiming.from_dict(first["request_timing"])
             + RequestTiming.from_dict(second["request_timing"]))
    timing = load(attempt / "timing.json")
    assert timing["invocations"] == 2 and timing["requests"] == total.to_dict()
    assert timing["requests"]["requests"] == (first["request_timing"]["requests"]
                                              + second["request_timing"]["requests"])
    assert (timing["first_token_seconds"], timing["decode_tokens_per_second"]) == (
        total.mean_first_token_seconds, total.decode_tokens_per_second)
    run_record = load(run / "timing.json")
    assert run_record["schema_version"] == 2 and run_record["requests"] == total.to_dict()
    assert replay.returncode == 0, replay.stderr.decode()
    assert json.loads(replay.stdout)["issues"] == []
