"""Check snapshot coherence and persisted artifacts without network requests."""

import importlib
import json
from collections import deque
from pathlib import Path

import pytest

from scrutare import __version__
from scrutare.engine.github import GitHubClient, GitHubError, PullRequestRef, PullRequestUnavailable

REF = PullRequestRef("owner", "repo", 12)
DIFF = "diff --git a/example.py b/example.py\n+print('héllo')\n"


def metadata(sha="abc123", **changes):
    return {
        "number": 12,
        "state": "open",
        "merged": False,
        "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
        "changed_files": 1,
        "head": {"sha": sha},
        "title": "A useful change",
        "body": "Prior context",
        **changes,
    }


class FakeClient(GitHubClient):
    def __init__(self, reads=None, failure=None):
        self.reads = deque(reads or [metadata(), metadata()])
        self.failure = failure
        self.snapshot = 0

    def get_pr(self, ref):
        assert ref == REF
        return self.reads.popleft()

    def get_diff(self, ref):
        assert ref == REF
        self.snapshot += 1
        return DIFF

    def get_files(self, ref):
        assert ref == REF
        if self.failure:
            raise self.failure
        return [{"filename": f"attempt-{self.snapshot}.py", "status": "modified"}]

    def get_reviews(self, ref):
        assert ref == REF
        return [{"id": 1, "state": "COMMENTED", "body": "Review context"}]

    def get_comments(self, ref):
        assert ref == REF
        return [{"id": 2, "body": "Issue context"}]

    def get_review_comments(self, ref):
        assert ref == REF
        return [{"id": 3, "path": "example.py", "line": 1, "body": "Inline context"}]


def ingestion():
    return importlib.import_module("scrutare.engine.ingestion")


def read_json(run, name):
    return json.loads((run / name).read_text())


def test_complete_artifact_structure_and_verbatim_config(tmp_path):
    config = tmp_path / "source.yaml"
    raw_config = b"# preserved comment\r\nstrategy: panel\r\n"
    config.write_bytes(raw_config)
    run = ingestion().ingest_pr(FakeClient(), REF, tmp_path / "runs", config)
    assert run.parent == tmp_path / "runs"
    assert {p.name for p in run.iterdir()} == {
        "diff.patch",
        "files.json",
        "reviews.json",
        "comments.json",
        "review_comments.json",
        "metadata.json",
        "config.yaml",
    }
    assert (run / "diff.patch").read_bytes() == DIFF.encode()
    assert (run / "config.yaml").read_bytes() == raw_config
    assert read_json(run, "files.json") == [{"filename": "attempt-1.py", "status": "modified"}]
    assert read_json(run, "reviews.json") == [
        {"id": 1, "state": "COMMENTED", "body": "Review context"}
    ]
    assert read_json(run, "comments.json") == [{"id": 2, "body": "Issue context"}]
    assert read_json(run, "review_comments.json") == [
        {"id": 3, "path": "example.py", "line": 1, "body": "Inline context"}
    ]
    assert read_json(run, "metadata.json") == {
        "schema_version": 1,
        "scrutare_version": __version__,
        "head_sha": "abc123",
        "repository": "owner/repo",
        "pr_number": 12,
        "pull_request": metadata(),
        "status": "ingested",
    }


def test_provided_config_bytes_and_normalized_data_are_persisted(tmp_path):
    raw = b"# exact bytes\r\nmodels: {default: {model: test-model}}\r\n"
    normalized = {"models": {"default": {"provider": "anthropic", "model": "test-model"}}}
    run = ingestion().ingest_pr(
        FakeClient(), REF, tmp_path / "runs", config_bytes=raw, config_data=normalized
    )
    assert (run / "config.yaml").read_bytes() == raw
    assert read_json(run, "config.json") == normalized


def test_conflicting_config_bytes_and_path_fail_before_capture(tmp_path):
    source = tmp_path / "source.yaml"
    source.write_bytes(b"legacy source")
    client = FakeClient()
    with pytest.raises(ValueError, match="config_bytes.*config_path|config_path.*config_bytes"):
        ingestion().ingest_pr(client, REF, tmp_path / "runs", source, config_bytes=b"bytes")
    assert client.snapshot == 0
    assert not (tmp_path / "runs").exists()


def test_config_json_write_failure_removes_only_fresh_run(tmp_path, monkeypatch):
    existing = tmp_path / "run-existing"
    existing.mkdir()
    (existing / "keep").write_text("saved")
    original = Path.write_text

    def fail_config_json(path, *args, **kwargs):
        if path.name == "config.json":
            assert (path.parent / "config.yaml").read_bytes() == b"original"
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_config_json)
    with pytest.raises(OSError, match="disk full"):
        ingestion().ingest_pr(
            FakeClient(), REF, tmp_path, config_bytes=b"original", config_data={"strategy": "panel"}
        )
    assert list(tmp_path.iterdir()) == [existing]
    assert (existing / "keep").read_text() == "saved"


@pytest.mark.parametrize("changes", [{"state": "closed"}, {"merged": True}])
@pytest.mark.parametrize("final", [False, True])
def test_closed_or_merged_at_either_read_leaves_no_run(tmp_path, changes, final):
    reads = [metadata(), metadata(**changes)] if final else [metadata(**changes)]
    with pytest.raises(PullRequestUnavailable):
        ingestion().ingest_pr(FakeClient(reads), REF, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_changed_head_discards_inputs_and_retries(tmp_path):
    client = FakeClient([metadata("old"), metadata("new"), metadata("new"), metadata("new")])
    run = ingestion().ingest_pr(client, REF, tmp_path)
    assert read_json(run, "files.json") == [{"filename": "attempt-2.py", "status": "modified"}]
    assert read_json(run, "metadata.json")["head_sha"] == "new"
    assert len(list(tmp_path.iterdir())) == 1


def test_continuous_head_changes_stop_after_three_attempts(tmp_path):
    client = FakeClient([metadata(str(n)) for n in range(6)])
    with pytest.raises(GitHubError, match="head|changed|stable"):
        ingestion().ingest_pr(client, REF, tmp_path)
    assert client.snapshot == 3
    assert list(tmp_path.iterdir()) == []


def test_transport_failure_leaves_no_run(tmp_path):
    with pytest.raises(GitHubError, match="transport unavailable"):
        ingestion().ingest_pr(
            FakeClient(failure=GitHubError("transport unavailable")), REF, tmp_path
        )
    assert list(tmp_path.iterdir()) == []


def test_two_runs_preserve_existing_files(tmp_path):
    existing = tmp_path / "run-existing"
    existing.mkdir()
    sentinel = existing / "metadata.json"
    sentinel.write_bytes(b"old run")
    first = ingestion().ingest_pr(FakeClient(), REF, tmp_path)
    first_metadata = (first / "metadata.json").read_bytes()
    second = ingestion().ingest_pr(FakeClient(), REF, tmp_path)
    assert first != second
    assert len(list(tmp_path.iterdir())) == 3
    assert sentinel.read_bytes() == b"old run"
    assert (first / "metadata.json").read_bytes() == first_metadata
    assert not (first / "config.yaml").exists()


def test_write_failure_removes_only_fresh_run(tmp_path, monkeypatch):
    existing = tmp_path / "run-existing"
    existing.mkdir()
    (existing / "keep").write_text("saved")
    original = Path.write_text

    def fail_reviews(path, *args, **kwargs):
        if path.name == "reviews.json":
            assert (path.parent / "diff.patch").exists()
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_reviews)
    with pytest.raises(OSError, match="disk full"):
        ingestion().ingest_pr(FakeClient(), REF, tmp_path)
    assert list(tmp_path.iterdir()) == [existing]
    assert (existing / "keep").read_text() == "saved"


def test_missing_config_cleans_up_run(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingestion().ingest_pr(FakeClient(), REF, tmp_path / "runs", tmp_path / "missing.yaml")
    assert list((tmp_path / "runs").iterdir()) == []


@pytest.mark.parametrize("changes", [{"state": "closed"}, {"merged": True}])
def test_lifecycle_guard_rejects_unavailable_pr(changes):
    with pytest.raises(PullRequestUnavailable):
        ingestion().check_pr_open(FakeClient([metadata(**changes)]), REF)


def test_force_push_after_capture_keeps_stored_sha_and_lifecycle_guard_allows_it(tmp_path):
    client = FakeClient([metadata("captured"), metadata("captured"), metadata("pushed")])
    run = ingestion().ingest_pr(client, REF, tmp_path)
    ingestion().check_pr_open(client, REF)
    assert read_json(run, "metadata.json")["head_sha"] == "captured"


@pytest.mark.parametrize(
    "base",
    [
        {"ref": "release", "sha": "base123", "repo": {"full_name": "owner/repo"}},
        {"ref": "main", "sha": "base456", "repo": {"full_name": "owner/repo"}},
        {"ref": "main", "sha": "base123", "repo": {"full_name": "other/repo"}},
    ],
)
def test_base_change_with_unchanged_head_discards_diff_and_files(tmp_path, base):
    class AttemptDiffClient(FakeClient):
        def get_diff(self, ref):
            return super().get_diff(ref) + f"# attempt-{self.snapshot}\n"

    client = AttemptDiffClient(
        [
            metadata(),
            metadata(base=base),
            metadata(base=base),
            metadata(base=base),
        ]
    )
    run = ingestion().ingest_pr(client, REF, tmp_path)
    assert (run / "diff.patch").read_bytes() == (DIFF + "# attempt-2\n").encode()
    assert read_json(run, "files.json") == [{"filename": "attempt-2.py", "status": "modified"}]
    assert read_json(run, "metadata.json")["pull_request"]["base"] == base
    assert len(list(tmp_path.iterdir())) == 1


def test_continuous_base_changes_stop_without_persisting(tmp_path):
    client = FakeClient(
        [
            metadata(base={"ref": "main", "sha": str(n), "repo": {"full_name": "owner/repo"}})
            for n in range(6)
        ]
    )
    with pytest.raises(GitHubError, match="base|changed|stable"):
        ingestion().ingest_pr(client, REF, tmp_path)
    assert client.snapshot == 3
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("base", [None, {}, {"ref": "main", "sha": "base123", "repo": {}}])
def test_malformed_base_leaves_no_run(tmp_path, base):
    with pytest.raises(GitHubError, match="malformed PR metadata"):
        ingestion().ingest_pr(FakeClient([metadata(base=base), metadata(base=base)]), REF, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_real_subprocess_diff_bytes_survive_full_capture(tmp_path, fake_gh):
    run = ingestion().ingest_pr(GitHubClient(), REF, tmp_path / "runs")
    assert (run / "diff.patch").read_bytes() == fake_gh
    assert read_json(run, "files.json") == [{"filename": "example.py", "status": "modified"}]
    assert read_json(run, "metadata.json")["status"] == "ingested"


@pytest.mark.parametrize("expected, fetched", [(2, 1), (0, 1), (3001, 3000)])
def test_incomplete_file_capture_leaves_no_run(tmp_path, expected, fetched):
    class FilesClient(FakeClient):
        def get_files(self, ref):
            return [{"filename": f"file-{n}.py"} for n in range(fetched)]

    client = FilesClient([metadata(changed_files=expected), metadata(changed_files=expected)])
    with pytest.raises(GitHubError, match="Incomplete file capture.*GitHub.*limit"):
        ingestion().ingest_pr(client, REF, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_file_count_is_checked_against_captured_metadata(tmp_path):
    client = FakeClient([metadata(changed_files=1), metadata(changed_files=2)])
    with pytest.raises(GitHubError, match="Incomplete file capture"):
        ingestion().ingest_pr(client, REF, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_zero_changed_files_can_be_captured_completely(tmp_path):
    class EmptyFilesClient(FakeClient):
        def get_files(self, ref):
            return []

    run = ingestion().ingest_pr(
        EmptyFilesClient([metadata(changed_files=0), metadata(changed_files=0)]), REF, tmp_path
    )
    assert read_json(run, "files.json") == []
    assert read_json(run, "metadata.json")["pull_request"]["changed_files"] == 0


@pytest.mark.parametrize("invalid", [False, True])
def test_review_config_prepares_coherent_capture_or_cleans_fresh_run(tmp_path, invalid):
    from scrutare.config import parse_config

    class CoherentClient(FakeClient):
        def get_diff(self, ref):
            super().get_diff(ref)
            return (
                "diff --git a/example.py b/example.py\n"
                "--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n"
            )

        def get_files(self, ref):
            assert ref == REF
            return [{"filename": "wrong.py" if invalid else "example.py", "status": "modified"}]

    raw = b"models: {default: {model: test-model}}\n"
    config = parse_config(raw)
    existing = tmp_path / "run-existing"
    existing.mkdir()
    (existing / "keep").write_bytes(b"saved")
    if invalid:
        with pytest.raises(ValueError, match="[Pp]repar|review inputs"):
            ingestion().ingest_pr(CoherentClient(), REF, tmp_path, config_bytes=raw,
                                  config_data=config.to_dict(), review_config=config)
        assert list(tmp_path.iterdir()) == [existing]
    else:
        run = ingestion().ingest_pr(CoherentClient(), REF, tmp_path, config_bytes=raw,
                                    config_data=config.to_dict(), review_config=config)
        assert read_json(run, "effective-files.json")["files"] == ["example.py"]
        assert (run / "review-inputs/diff.patch").read_bytes() == (
            b"diff --git a/example.py b/example.py\n"
            b"--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n"
        )
        assert (run / "diff.patch").read_bytes() == (run / "review-inputs/diff.patch").read_bytes()
        assert read_json(run / "review-inputs", "files.json") == [
            {"filename": "example.py", "status": "modified"},
        ]
    assert (existing / "keep").read_bytes() == b"saved"


def test_complete_raw_count_is_checked_before_review_projection(tmp_path):
    from scrutare.config import parse_config

    raw = b"models: {default: {model: test-model}}\n"
    config = parse_config(raw)
    client = FakeClient([metadata(changed_files=2), metadata(changed_files=2)])
    with pytest.raises(GitHubError, match="Incomplete file capture"):
        ingestion().ingest_pr(client, REF, tmp_path, config_bytes=raw,
                              config_data=config.to_dict(), review_config=config)
    assert list(tmp_path.iterdir()) == []
