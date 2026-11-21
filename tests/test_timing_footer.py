"""The posted summary's review time line: rendered from the run's own timing record."""

import json

import pytest
from test_post_review import REF, SHA, CaptureClient, FakePoster, post
from test_review_payload import DIFF as PAYLOAD_DIFF

from scrutare.config import ConfigError, parse_config
from scrutare.engine.ingestion import ingest_pr
from scrutare.engine.timing import SessionTiming, review_timing
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict
from scrutare.poster import PostingError, build_review_payload

RAW = b"models: {default: {model: test-model}}\n"
OFF = RAW + b"github: {timing_footer: false}\n"
LINE = "Review time: 12 min 5 s, 1 session, 100 tokens (40 output)"


def captured(tmp_path, raw=RAW, *, timed=True):
    config = parse_config(raw)
    run = ingest_pr(CaptureClient(), REF, tmp_path, config_bytes=raw,
                    config_data=config.to_dict())
    verdict = derive_verdict(dedupe_findings([
        Finding(Anchor("example.py", 1), "security", "Problem", "Risk", "security")]),
        config.verdict)
    if timed:
        attempt = run / "sessions" / "security" / "attempt-0001"
        attempt.mkdir(parents=True)
        timing = SessionTiming("openai", "m", 1, 700.0, 100, 40)
        (attempt / "timing.json").write_text(json.dumps(timing.to_dict()))
        (run / "timing.json").write_text(json.dumps(review_timing(725.0, [timing])))
    return run, verdict


def test_the_summary_carries_one_timing_line_above_the_marker(tmp_path):
    run, verdict = captured(tmp_path)
    client = FakePoster()
    post(run, verdict, client=client)
    body = client.creates[0].body
    assert f"Head SHA: {SHA}\n\n{LINE}\n\n<!-- scrutare-run:" in body
    assert body.count("Review time:") == 1
    # A resumed post renders the same bytes from the same record.
    assert post(run, verdict, client=client).body == body and len(client.creates) == 1


def test_turning_the_line_off_posts_the_summary_as_before(tmp_path):
    with_line, verdict = captured(tmp_path / "on")
    without, _ = captured(tmp_path / "off", OFF)
    plain, _ = captured(tmp_path / "plain", timed=False)
    bodies = []
    for run in (with_line, without, plain):
        client = FakePoster()
        post(run, verdict, client=client)
        bodies.append(client.creates[0].body)
    assert "Review time:" in bodies[0]
    assert "Review time:" not in bodies[1] and "Review time:" not in bodies[2]
    marker = lambda body: body.rsplit("<!-- scrutare-run:", 1)[0]  # noqa: E731
    assert marker(bodies[1]) == marker(bodies[2]) == marker(bodies[0]).replace(
        f"{LINE}\n\n", "")


def test_a_timing_record_that_disagrees_with_its_sessions_is_refused(tmp_path):
    run, verdict = captured(tmp_path)
    document = json.loads((run / "timing.json").read_text())
    document["tokens"] = 1
    (run / "timing.json").write_text(json.dumps(document))
    client = FakePoster()
    with pytest.raises(PostingError, match="Timing evidence"):
        post(run, verdict, client=client)
    assert client.creates == [] and not (run / "posting.json").exists()


def test_the_payload_without_a_footer_is_unchanged_and_a_footer_is_one_plain_line():
    verdict = derive_verdict([], parse_config(RAW).verdict)
    kwargs = {"head_sha": SHA, "strategy": "panel", "post_mode": "review", "run_id": "0" * 32}
    plain = build_review_payload(verdict, PAYLOAD_DIFF, **kwargs)
    footed = build_review_payload(verdict, PAYLOAD_DIFF, footer=LINE, **kwargs)
    marker = "<!-- scrutare-run:" + "0" * 32 + " -->"
    assert footed.body == plain.body[:-len(marker)] + f"{LINE}\n\n{marker}"
    for bad in ("", "two\nlines", "Review time: <b>", "@someone"):
        with pytest.raises(PostingError):
            build_review_payload(verdict, PAYLOAD_DIFF, footer=bad, **kwargs)


def test_the_footer_setting_is_recorded_only_when_turned_off():
    assert "timing_footer" not in parse_config(RAW).to_dict()["github"]
    assert parse_config(RAW).github.timing_footer is True
    off = parse_config(OFF)
    assert off.github.timing_footer is False
    assert off.to_dict()["github"]["timing_footer"] is False
    assert parse_config(json.dumps(off.to_dict()).encode()) == off
    with pytest.raises(ConfigError, match="github.timing_footer"):
        parse_config(RAW + b"github: {timing_footer: 'no'}\n")


def test_an_escalation_keeps_its_targets_readable_to_replay_with_a_footer():
    from test_escalation_payload import build, finding

    from scrutare.replay.posting import _original_targets

    payload = build([finding()], human_reviewers=("Alice",), footer=LINE)
    assert payload.body.endswith(f"\n\n{LINE}\n\n<!-- scrutare-run:"
                                 "0123456789abcdef0123456789abcdef -->")
    assert _original_targets(payload) == ("Alice",)
