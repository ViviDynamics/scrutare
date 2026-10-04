"""Exclusive producer ownership, exact bytes and replay/poster composition."""

import importlib
import json
import os
import stat

import pytest
from test_fanout import configure
from test_panel import finding, install, modules, run
from test_review_inputs import capture as capture

from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_artifacts import SessionArtifactError


@pytest.mark.parametrize("name", ["sessions", "fanout.json", "panel.json", "findings.json",
                                  "verdict.json", "posting.json", "review-payload.json",
                                  "escalation.json", ".posting.lock"])
@pytest.mark.parametrize("kind", ["regular", "link", "directory", "fifo"])
def test_stale_evidence_refused_before_preparation(capture, name, kind):
    strategy, _, _ = modules()
    config = configure(capture)
    path = capture / name
    if kind == "regular":
        path.write_bytes(b"prior evidence")
    elif kind == "link":
        path.symlink_to(capture / "absent")
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(SessionArtifactError):
        run(strategy, capture, config)
    assert not (capture / "review-inputs").exists()
    if kind == "regular":
        assert path.read_bytes() == b"prior evidence"


def test_exact_byte_writer_refuses_overwrite_and_only_engine_destinations(capture):
    module = importlib.import_module("scrutare.engine.session_artifacts")
    config = configure(capture)
    inputs = prepare_review_inputs(capture, config)
    for name in ("findings.json", "panel.json", "verdict.json"):
        path = capture / name
        module.write_owned_bytes(path, b"exact\r\n", prepared_root=inputs.root)
        assert path.read_bytes() == b"exact\r\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        with pytest.raises(SessionArtifactError):
            module.write_owned_bytes(path, b"new", prepared_root=inputs.root)
        assert path.read_bytes() == b"exact\r\n"
    with pytest.raises(SessionArtifactError):
        module.write_owned_bytes(capture / "config.json", b"new", prepared_root=inputs.root)


@pytest.mark.parametrize("failed_name", ["findings.json", "panel.json", "verdict.json"])
def test_publication_is_verdict_last_and_retains_partial_evidence(
        capture, monkeypatch, failed_name):
    config = configure(capture, personas=["security"])
    strategy, _, _, _ = install(monkeypatch, {"security": (finding(),)})
    artifacts = importlib.import_module("scrutare.engine.panel_artifacts")
    original = artifacts.write_owned_bytes
    writes = []
    def write(path, data, **kwargs):
        writes.append(path.name)
        if path.name == failed_name:
            raise SessionArtifactError("disk unavailable")
        original(path, data, **kwargs)
    monkeypatch.setattr(artifacts, "write_owned_bytes", write)
    with pytest.raises(SessionArtifactError):
        run(strategy, capture, config)
    expected = ["findings.json", "panel.json", "verdict.json"]
    assert writes == expected[:expected.index(failed_name) + 1]
    assert not (capture / "verdict.json").exists()
    assert (capture / "findings.json").exists() == (failed_name != "findings.json")


def test_install_race_never_replaces_competing_evidence(capture, monkeypatch):
    config = configure(capture, personas=["security"])
    strategy, _, _, _ = install(monkeypatch, {"security": (finding(),)})
    original = os.link
    def race(src, dst, **kwargs):
        if dst == "findings.json":
            (capture / dst).write_bytes(b"concurrent evidence")
        return original(src, dst, **kwargs)
    monkeypatch.setattr(os, "link", race)
    with pytest.raises(SessionArtifactError):
        run(strategy, capture, config)
    assert (capture / "findings.json").read_bytes() == b"concurrent evidence"
    assert not (capture / "verdict.json").exists()
    assert not list(capture.glob(".scrutare-*.tmp"))


def test_real_producer_replays_and_composes_with_existing_fake_poster(tmp_path, monkeypatch):
    from test_post_review import REF, CaptureClient, FakePoster

    from scrutare.config import parse_config
    from scrutare.engine.ingestion import ingest_pr
    from scrutare.poster import post_review
    from scrutare.replay import replay_run

    raw = b"personas: [security]\nmodels: {default: {model: default-model}}\n"
    config = parse_config(raw)
    capture = ingest_pr(CaptureClient(), REF, tmp_path, config_bytes=raw,
                        config_data=config.to_dict())
    strategy, _, _, _ = install(monkeypatch, {"security": (finding(file="example.py"),)})
    result = run(strategy, capture, config)
    saved = (capture / "verdict.json").read_bytes()
    audit = replay_run(capture)
    assert audit.saved_identical is True and audit.posted_identical is None
    assert audit.posting.status == "absent"
    post_review(capture, result.verdict, client=FakePoster())
    assert (capture / "verdict.json").read_bytes() == saved
    audit = replay_run(capture)
    assert audit.saved_identical is True and audit.posted_identical is True
    assert audit.posting.status == "posted"
    assert json.loads((capture / "posting.json").read_bytes())["event"] == "REQUEST_CHANGES"


def test_serialization_failure_precedes_any_publication(capture):
    from scrutare.engine.panel_artifacts import publish_panel
    from scrutare.findings import derive_verdict

    config = configure(capture)
    inputs = prepare_review_inputs(capture, config)
    with pytest.raises(SessionArtifactError):
        publish_panel(capture, inputs.root, {"bad": float("nan")},
                      derive_verdict((), config.verdict))
    assert not any((capture / name).exists() for name in (
        "findings.json", "panel.json", "verdict.json"))


def test_run_directory_symlink_is_refused_before_preparation(capture):
    strategy, _, _ = modules()
    config = configure(capture)
    link = capture.parent / "run-link"
    link.symlink_to(capture, target_is_directory=True)
    with pytest.raises(SessionArtifactError):
        run(strategy, link, config)
    assert not (capture / "review-inputs").exists()
