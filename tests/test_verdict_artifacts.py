"""Verdict persistence writes canonical evidence without disturbing the run."""

import json
from pathlib import Path

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict


def sample_verdict():
    sources = [
        Finding(Anchor("src/café.py", 2, "LEFT"), "style", " Wrong\tresult ",
                "Use consistent results", "junior-dev"),
        Finding(Anchor("src/café.py", 2, "LEFT"), "security", "Wrong result",
                "Exposes private data 🔒", "security"),
        Finding(Anchor("README.md", 5), "docs", "Missing example",
                "Readers cannot run it", "docs"),
    ]
    sources.insert(2, sources[0])
    return derive_verdict(dedupe_findings(sources), VerdictSettings())


def test_writer_persists_canonical_bytes_and_every_group_source_and_rule(tmp_path):
    from scrutare.findings import write_verdict

    verdict = sample_verdict()
    unrelated = tmp_path / "diff.patch"
    unrelated.write_bytes(b"original diff")
    destination = write_verdict(tmp_path, verdict)
    assert destination == tmp_path / "verdict.json"
    assert destination.read_bytes() == verdict.to_bytes()
    assert "café".encode() in destination.read_bytes()
    assert json.loads(destination.read_bytes()) == {
        "schema_version": 1,
        "verdict": "changes_requested",
        "rule": "any_blocking_finding",
        "config": {
            "blocking_categories": ["correctness", "security", "regression"],
            "advisory_categories": ["style", "consistency", "docs"],
        },
        "findings": [{
            "file": "src/café.py", "line": 2, "side": "LEFT", "problem": " Wrong\tresult ",
            "categories": ["security", "style"], "personas": ["junior-dev", "security"],
            "reasons": ["Use consistent results", "Exposes private data 🔒"],
            "blocking": True, "blocking_categories": ["security"],
            "sources": [{
                "file": "src/café.py", "line": 2, "side": "LEFT", "category": "style",
                "problem": " Wrong\tresult ", "reason": "Use consistent results",
                "persona": "junior-dev",
            }, {
                "file": "src/café.py", "line": 2, "side": "LEFT", "category": "security",
                "problem": "Wrong result", "reason": "Exposes private data 🔒",
                "persona": "security",
            }, {
                "file": "src/café.py", "line": 2, "side": "LEFT", "category": "style",
                "problem": " Wrong\tresult ", "reason": "Use consistent results",
                "persona": "junior-dev",
            }],
        }, {
            "file": "README.md", "line": 5, "side": "RIGHT", "problem": "Missing example",
            "categories": ["docs"], "personas": ["docs"], "reasons": ["Readers cannot run it"],
            "blocking": False, "blocking_categories": [],
            "sources": [{
                "file": "README.md", "line": 5, "side": "RIGHT", "category": "docs",
                "problem": "Missing example", "reason": "Readers cannot run it",
                "persona": "docs",
            }],
        }],
    }
    assert unrelated.read_bytes() == b"original diff"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["diff.patch", "verdict.json"]


def test_repeated_write_is_byte_identical(tmp_path):
    from scrutare.findings import write_verdict

    first = write_verdict(tmp_path, sample_verdict()).read_bytes()
    second = write_verdict(tmp_path, sample_verdict()).read_bytes()
    assert first == second
    assert list(tmp_path.iterdir()) == [tmp_path / "verdict.json"]


def test_changed_verdict_replaces_previous_artifact(tmp_path):
    from scrutare.findings import write_verdict

    write_verdict(tmp_path, sample_verdict())
    changed = derive_verdict([], VerdictSettings())
    destination = write_verdict(tmp_path, changed)
    assert destination.read_bytes() == changed.to_bytes()
    artifact = json.loads(destination.read_bytes())
    assert artifact["verdict"] == "approve"
    assert artifact["rule"] == "no_blocking_findings"
    assert artifact["findings"] == []


def test_atomic_replacement_keeps_previous_bytes_until_complete_sibling_is_ready(
    tmp_path, monkeypatch,
):
    from scrutare.findings import write_verdict

    destination = tmp_path / "verdict.json"
    destination.write_bytes(b"previous verdict")
    verdict = sample_verdict()
    replace = Path.replace
    observed = []

    def observe_replacement(source, target):
        observed.append((source.parent, source.read_bytes(), target.read_bytes()))
        assert source != target
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", observe_replacement)
    write_verdict(tmp_path, verdict)
    assert observed == [(tmp_path, verdict.to_bytes(), b"previous verdict")]
    assert destination.read_bytes() == verdict.to_bytes()
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("is_file", [False, True])
def test_missing_or_non_directory_run_fails_safely_without_creating_a_run(tmp_path, is_file):
    from scrutare.findings import VerdictArtifactError, write_verdict

    run_dir = tmp_path / "secret-run"
    if is_file:
        run_dir.write_bytes(b"existing file")
    with pytest.raises(VerdictArtifactError, match="directory") as error:
        write_verdict(run_dir, sample_verdict())
    assert "secret-run" not in str(error.value)
    assert error.value.__suppress_context__
    if is_file:
        assert run_dir.read_bytes() == b"existing file"
    else:
        assert not run_dir.exists()


@pytest.mark.parametrize("invalid", [None, {}, "secret-verdict", True])
def test_writer_rejects_untyped_verdict_before_any_io(tmp_path, monkeypatch, invalid):
    from scrutare.findings import VerdictArtifactError, write_verdict

    def forbidden_io(path):
        pytest.fail("Invalid verdict must be rejected before directory inspection")

    monkeypatch.setattr(Path, "is_dir", forbidden_io)
    with pytest.raises(VerdictArtifactError, match="Verdict") as error:
        write_verdict(tmp_path, invalid)
    assert "secret-verdict" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_encoding_failure_is_safe_and_preserves_prior_artifact_without_io(tmp_path, monkeypatch):
    from scrutare.findings import VerdictArtifactError, write_verdict

    destination = tmp_path / "verdict.json"
    destination.write_bytes(b"previous verdict")
    verdict = derive_verdict(dedupe_findings([
        Finding(Anchor("src/main.py", 2), "security", "secret\ud800", "impact", "security"),
    ]), VerdictSettings())

    def forbidden_io(path):
        pytest.fail("Encoding must complete before directory inspection")

    monkeypatch.setattr(Path, "is_dir", forbidden_io)
    with pytest.raises(VerdictArtifactError, match="encod") as error:
        write_verdict(tmp_path, verdict)
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__
    assert destination.read_bytes() == b"previous verdict"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("stage", ["create", "write", "replace"])
@pytest.mark.parametrize("existing", [False, True])
def test_io_failures_are_safe_clean_temps_and_preserve_prior_run(
    tmp_path, monkeypatch, stage, existing,
):
    from scrutare.findings import VerdictArtifactError, artifacts, write_verdict

    destination = tmp_path / "verdict.json"
    if existing:
        destination.write_bytes(b"previous verdict")
    unrelated = tmp_path / "diff.patch"
    unrelated.write_bytes(b"original diff")

    def fail_create(*args, **kwargs):
        raise OSError("secret error details")

    # Real temporary files are kept for write/replace faults so cleanup is observed on disk.
    original_temp = artifacts.NamedTemporaryFile

    def fail_write(*args, **kwargs):
        temporary = original_temp(*args, **kwargs)
        original_write = temporary.write

        def partial_write(data):
            original_write(data[:8])
            temporary.flush()
            raise OSError("secret error details")

        temporary.write = partial_write
        return temporary

    def fail_replace(source, target):
        assert source.parent == tmp_path
        assert source.read_bytes() == sample_verdict().to_bytes()
        raise OSError("secret error details")

    if stage == "create":
        monkeypatch.setattr(artifacts, "NamedTemporaryFile", fail_create)
    elif stage == "write":
        monkeypatch.setattr(artifacts, "NamedTemporaryFile", fail_write)
    else:
        monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(VerdictArtifactError, match="verdict.json") as error:
        write_verdict(tmp_path, sample_verdict())
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__
    assert unrelated.read_bytes() == b"original diff"
    if existing:
        assert destination.read_bytes() == b"previous verdict"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["diff.patch", "verdict.json"]
    else:
        assert not destination.exists()
        assert list(tmp_path.iterdir()) == [unrelated]


def test_cleanup_failure_reports_safe_actionable_error_and_preserves_old_artifact(
    tmp_path, monkeypatch,
):
    from scrutare.findings import VerdictArtifactError, write_verdict

    destination = tmp_path / "verdict.json"
    destination.write_bytes(b"previous verdict")

    def fail_replace(source, target):
        raise OSError("secret replace details")

    def fail_cleanup(path, *, missing_ok=False):
        raise OSError("secret cleanup details")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(VerdictArtifactError, match="clean.*permissions") as error:
        write_verdict(tmp_path, sample_verdict())
    assert "secret" not in str(error.value)
    assert error.value.__suppress_context__
    assert destination.read_bytes() == b"previous verdict"
