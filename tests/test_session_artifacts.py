"""Private attempt evidence cannot overwrite earlier work or escape through filesystem links."""

import importlib
import importlib.util
import json
import os
import stat

import pytest


def module():
    assert importlib.util.find_spec("scrutare.engine.session_artifacts") is not None, (
        "private session artifact helpers are missing"
    )
    return importlib.import_module("scrutare.engine.session_artifacts")


def persist(run, path, document):
    return module().write_owned_json(path, document, prepared_root=run / "review-inputs")


def create(tmp_path, *, persona="security", attempt=1):
    return module().create_attempt_directory(
        tmp_path, persona, attempt, prepared_root=tmp_path / "review-inputs",
    )


def test_attempts_are_exclusive_private_and_keep_other_persona_evidence(tmp_path):
    first = create(tmp_path)
    assert first == tmp_path / "sessions" / "security" / "attempt-0001"
    for directory in (first, first.parent, first.parent.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    raw = first / "stdout.jsonl"
    raw.write_bytes(b"captured evidence")
    with pytest.raises(module().SessionArtifactError):
        create(tmp_path)
    second = create(tmp_path, attempt=2)
    other = create(tmp_path, persona="senior-dev")
    assert second.name == "attempt-0002"
    assert other.parent.name == "senior-dev"
    assert raw.read_bytes() == b"captured evidence"


@pytest.mark.parametrize("persona", ["../security", "", "Security", "security/x", "a_b"])
def test_unsafe_persona_is_refused_without_creating_sessions(tmp_path, persona):
    with pytest.raises(module().SessionArtifactError):
        create(tmp_path, persona=persona)
    assert not (tmp_path / "sessions").exists()


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5, "1", 10000])
def test_attempt_requires_bounded_actual_positive_integer(tmp_path, attempt):
    with pytest.raises(module().SessionArtifactError):
        create(tmp_path, attempt=attempt)
    assert not (tmp_path / "sessions").exists()


@pytest.mark.parametrize("component", ["run", "sessions", "persona", "attempt"])
def test_symlinked_destination_or_ancestor_is_refused(tmp_path, component):
    target = tmp_path / "outside"
    target.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    if component == "run":
        run.rmdir()
        run.symlink_to(target, target_is_directory=True)
    elif component == "sessions":
        (run / "sessions").symlink_to(target, target_is_directory=True)
    elif component == "persona":
        (run / "sessions").mkdir(mode=0o700)
        (run / "sessions" / "security").symlink_to(target, target_is_directory=True)
    else:
        (run / "sessions" / "security").mkdir(parents=True)
        (run / "sessions").chmod(0o700)
        (run / "sessions" / "security").chmod(0o700)
        (run / "sessions" / "security" / "attempt-0001").symlink_to(
            target, target_is_directory=True,
        )
    with pytest.raises(module().SessionArtifactError):
        create(run)
    assert list(target.iterdir()) == []


def test_symlinked_ancestor_above_run_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "run").mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(module().SessionArtifactError):
        create(linked / "run")
    assert not (real / "run" / "sessions").exists()


@pytest.mark.parametrize("component", ["sessions", "persona", "attempt"])
def test_non_directory_components_are_refused(tmp_path, component):
    sessions = tmp_path / "sessions"
    if component == "sessions":
        sessions.write_text("owned by someone else")
    else:
        sessions.mkdir(mode=0o700)
        persona = sessions / "security"
        if component == "persona":
            persona.write_text("owned by someone else")
        else:
            persona.mkdir(mode=0o700)
            (persona / "attempt-0001").write_text("owned by someone else")
    with pytest.raises(module().SessionArtifactError):
        create(tmp_path)


@pytest.mark.parametrize("nested", [False, True])
def test_attempt_never_created_inside_explicit_prepared_root(tmp_path, nested):
    prepared = tmp_path / "custom-prepared-name"
    run = prepared / "nested" if nested else prepared
    run.mkdir(parents=True)
    with pytest.raises(module().SessionArtifactError):
        module().create_attempt_directory(run, "security", prepared_root=prepared)
    assert not (run / "sessions").exists()


def test_public_existing_session_parent_cannot_expose_new_raw_evidence(tmp_path):
    (tmp_path / "sessions").mkdir(mode=0o755)
    with pytest.raises(module().SessionArtifactError):
        create(tmp_path)
    assert stat.S_IMODE((tmp_path / "sessions").stat().st_mode) == 0o755


def test_owned_json_atomic_new_file_is_private_utf8_and_does_not_replace(tmp_path):
    attempt = create(tmp_path)
    destination = attempt / "outcome.json"
    persist(tmp_path, destination, {"finding": "café", "usage": 26})
    assert json.loads(destination.read_bytes()) == {"finding": "café", "usage": 26}
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    original = destination.read_bytes()
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, destination, {"replacement": True})
    assert destination.read_bytes() == original
    assert {item.name for item in attempt.iterdir()} == {"outcome.json"}


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_nonregular_or_symlinked_output_is_refused_without_modification(tmp_path, kind):
    attempt = create(tmp_path)
    destination = attempt / "outcome.json"
    target = tmp_path / "outside.json"
    target.write_bytes(b"original")
    if kind == "symlink":
        destination.symlink_to(target)
    elif kind == "directory":
        destination.mkdir()
    else:
        os.mkfifo(destination)
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, destination, {"replacement": True})
    assert target.read_bytes() == b"original"
    assert {item.name for item in attempt.iterdir()} == {"outcome.json"}


def test_json_writer_refuses_generic_destination(tmp_path):
    destination = tmp_path / "outcome.json"
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, destination, {})
    assert not destination.exists()


def test_writer_does_not_follow_attempt_symlink(tmp_path):
    attempt = create(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    attempt.rmdir()
    attempt.symlink_to(outside, target_is_directory=True)
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, attempt / "outcome.json", {})
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("document", [float("nan"), float("inf"), {"text": "\ud800"}, object()])
def test_invalid_json_keeps_captured_logs_and_leaves_no_temporary_files(tmp_path, document):
    attempt = create(tmp_path)
    stdout = attempt / "stdout.jsonl"
    stdout.write_bytes(b"raw captured log")
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, attempt / "outcome.json", document)
    assert stdout.read_bytes() == b"raw captured log"
    assert {item.name for item in attempt.iterdir()} == {"stdout.jsonl"}


def test_install_failure_cleans_only_own_temporary_and_preserves_capture(tmp_path, monkeypatch):
    attempt = create(tmp_path)
    stdout = attempt / "stdout.jsonl"
    stderr = attempt / "stderr.log"
    stdout.write_bytes(b"stream")
    stderr.write_bytes(b"provider failure")

    def failed_link(*args, **kwargs):
        raise OSError("provider secret must stay private")

    monkeypatch.setattr(os, "link", failed_link)
    with pytest.raises(module().SessionArtifactError) as raised:
        persist(tmp_path, attempt / "outcome.json", {"status": "failed"})
    assert "provider secret" not in str(raised.value)
    assert stdout.read_bytes() == b"stream"
    assert stderr.read_bytes() == b"provider failure"
    assert {item.name for item in attempt.iterdir()} == {"stdout.jsonl", "stderr.log"}


def test_captured_run_fanout_manifest_is_private_and_exclusive(tmp_path):
    path = tmp_path / "fanout.json"
    persist(tmp_path, path, {"outcomes": []})
    assert json.loads(path.read_bytes()) == {"outcomes": []}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, path, {})


def test_writer_explicit_boundary_refuses_wrong_run_and_prepared_tree(tmp_path):
    attempt = create(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    prepared = tmp_path / "review-inputs"
    prepared.mkdir()
    for path in (outside / "fanout.json", prepared / "fanout.json", attempt / ".." / "escape.json"):
        with pytest.raises(module().SessionArtifactError):
            persist(tmp_path, path, {})
    assert list(prepared.iterdir()) == []
    assert list(outside.iterdir()) == []


def test_exclusive_temporary_creation_never_deletes_another_file(tmp_path, monkeypatch):
    from types import SimpleNamespace

    attempt = create(tmp_path)
    existing = attempt / ".scrutare-collision.tmp"
    existing.write_bytes(b"other owner's data")
    monkeypatch.setattr(module(), "uuid4", lambda: SimpleNamespace(hex="collision"))
    with pytest.raises(module().SessionArtifactError):
        persist(tmp_path, attempt / "outcome.json", {})
    assert existing.is_file(), "an unowned temporary collision was deleted"
    assert existing.read_bytes() == b"other owner's data"
    assert not (attempt / "outcome.json").exists()
