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
        "diff.patch", "files.json", "reviews.json", "comments.json",
        "review_comments.json", "metadata.json", "config.yaml",
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
