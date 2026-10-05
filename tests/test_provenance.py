"""Manifest bytes and filesystem ownership never change canonical deciding evidence."""

import hashlib
import importlib
import importlib.util
import json
import os
import stat

import pytest
from test_replay import bundle

from scrutare import __version__
from scrutare.replay import replay_run


def provenance():
    assert importlib.util.find_spec("scrutare.provenance") is not None, (
        "artifact manifest writer is missing"
    )
    return importlib.import_module("scrutare.provenance")


def test_manifest_hashes_exact_bytes_and_sorted_nested_inventory(tmp_path):
    module = provenance()
    originals = {
        "raw.jsonl": b'{"raw": "capture"}\r\n',
        "config.yaml": b"# preserve comment\r\n",
        "nested/évidence.bin": bytes(range(256)),
        "empty": b"",
        "nested/.posting.lock": b"excluded lock",
        ".scrutare-owned.tmp": b"excluded stage",
        "nested/.posting-owned.tmp": b"excluded stage",
        "nested/keep.tmp": b"ordinary file",
        "nested/artifacts.json": b"ordinary nested artifact",
    }
    for name, data in originals.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (tmp_path / "empty-directory").mkdir()
    path = module.write_artifact_manifest(tmp_path)
    assert path == tmp_path / "artifacts.json"
    result = json.loads(path.read_bytes())
    expected_names = ["config.yaml", "empty", "nested/artifacts.json", "nested/keep.tmp",
                      "nested/évidence.bin", "raw.jsonl"]
    assert result == {
        "schema_version": 1,
        "scrutare_version": __version__,
        "artifacts": [
            {"path": name, "sha256": hashlib.sha256(originals[name]).hexdigest(),
             "size_bytes": len(originals[name])} for name in expected_names
        ],
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals
    first = path.read_bytes()
    path.unlink()
    assert module.write_artifact_manifest(tmp_path).read_bytes() == first


@pytest.mark.parametrize("recorded_version", [None, "1900.1.0", "9999.12.0"])
def test_manifest_and_replay_version_leave_legacy_canonical_bytes_identical(
    tmp_path, recorded_version,
):
    module = provenance()
    original = bundle(tmp_path)
    (tmp_path / "config.yaml").write_bytes(b"# raw config\r\nstrategy: debate\r\n")
    if recorded_version:
        (tmp_path / "metadata.json").write_text(json.dumps({
            "scrutare_version": recorded_version,
        }))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    legacy = replay_run(tmp_path)
    assert legacy.exit_code == 0
    assert legacy.to_dict()["scrutare_version"] == __version__
    module.write_artifact_manifest(tmp_path)
    replay = replay_run(tmp_path)
    assert replay.exit_code == 0 and replay.saved_identical is True
    assert replay.verdict.to_bytes() == original.to_bytes()
    assert "scrutare_version" not in replay.verdict.to_dict()
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
    # Replay continues to decide from canonical evidence, without manifest verification.
    (tmp_path / "artifacts.json").write_bytes(b"not an integrity proof")
    assert replay_run(tmp_path).to_dict() == replay.to_dict()


@pytest.mark.parametrize("kind", ["regular", "link", "directory", "fifo"])
def test_existing_manifest_is_never_replaced(tmp_path, kind):
    module = provenance()
    path = tmp_path / "artifacts.json"
    if kind == "regular":
        path.write_bytes(b"prior manifest")
    elif kind == "link":
        path.symlink_to(tmp_path / "missing")
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    before = path.lstat()
    with pytest.raises(module.ProvenanceError):
        module.write_artifact_manifest(tmp_path)
    assert path.lstat() == before
    if kind == "regular":
        assert path.read_bytes() == b"prior manifest"
    assert not list(tmp_path.glob(".scrutare-*.tmp"))


@pytest.mark.parametrize("name", ["unsafe-secret", ".posting.lock", ".scrutare-stage.tmp"])
@pytest.mark.parametrize("kind", ["link", "directory-link", "fifo"])
def test_unsafe_entries_are_refused_even_when_named_as_exclusions(tmp_path, name, kind):
    module = provenance()
    outside = tmp_path / "outside"
    outside.mkdir()
    run = tmp_path / "run"
    run.mkdir()
    path = run / name
    if kind == "link":
        path.symlink_to(outside / "missing")
    elif kind == "directory-link":
        path.symlink_to(outside, target_is_directory=True)
    else:
        os.mkfifo(path)
    with pytest.raises(module.ProvenanceError) as error:
        module.write_artifact_manifest(run)
    assert "unsafe-secret" not in str(error.value)
    assert str(tmp_path) not in str(error.value)
    assert not (run / "artifacts.json").exists()
    assert not list(outside.iterdir())


@pytest.mark.parametrize("ancestor", [False, True])
def test_linked_run_or_ancestor_is_refused(tmp_path, ancestor):
    module = provenance()
    real = tmp_path / "real"
    real.mkdir()
    (real / "run").mkdir()
    linked = tmp_path / "link"
    linked.symlink_to(real if ancestor else real / "run", target_is_directory=True)
    with pytest.raises(module.ProvenanceError):
        module.write_artifact_manifest(linked / "run" if ancestor else linked)
    assert not (real / "run" / "artifacts.json").exists()


def test_manifest_install_race_preserves_competing_complete_bytes(tmp_path, monkeypatch):
    module = provenance()
    (tmp_path / "raw").write_bytes(b"capture")
    link = os.link

    def race(src, dst, **kwargs):
        if dst == "artifacts.json":
            (tmp_path / dst).write_bytes(b"concurrent manifest")
        return link(src, dst, **kwargs)

    monkeypatch.setattr(os, "link", race)
    with pytest.raises(module.ProvenanceError):
        module.write_artifact_manifest(tmp_path)
    assert (tmp_path / "artifacts.json").read_bytes() == b"concurrent manifest"
    assert not list(tmp_path.glob(".scrutare-*.tmp"))


@pytest.mark.parametrize("mutation", ["replace", "modify"])
def test_ordinary_file_changes_during_hashing_abort_manifest(tmp_path, monkeypatch, mutation):
    module = provenance()
    path = tmp_path / "raw"
    path.write_bytes(b"original data")
    read = os.read
    changed = False

    def race(fd, size):
        nonlocal changed
        data = read(fd, size)
        if data and not changed:
            changed = True
            if mutation == "replace":
                path.unlink()
            path.write_bytes(b"changed evidence")
        return data

    monkeypatch.setattr(os, "read", race)
    with pytest.raises(module.ProvenanceError):
        module.write_artifact_manifest(tmp_path)
    assert path.read_bytes() == b"changed evidence"
    assert not (tmp_path / "artifacts.json").exists()


def test_hashing_uses_bounded_reads_and_installs_only_complete_manifest(tmp_path, monkeypatch):
    module = provenance()
    data = bytes(range(256)) * 16384
    (tmp_path / "large").write_bytes(data)
    read = os.read
    link = os.link
    reads = []

    def bounded(fd, size):
        assert 0 < size <= 1024 * 1024
        reads.append(size)
        return read(fd, size)

    def complete(src, dst, **kwargs):
        assert not (tmp_path / "artifacts.json").exists()
        staged = json.loads((tmp_path / src).read_bytes())
        assert staged["artifacts"] == [{"path": "large", "size_bytes": len(data),
                                        "sha256": hashlib.sha256(data).hexdigest()}]
        return link(src, dst, **kwargs)

    monkeypatch.setattr(os, "read", bounded)
    monkeypatch.setattr(os, "link", complete)
    module.write_artifact_manifest(tmp_path)
    assert len(reads) > 1
    assert not list(tmp_path.glob(".scrutare-*.tmp"))
