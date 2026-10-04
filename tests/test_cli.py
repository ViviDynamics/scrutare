"""Exercise CLI output and failures with only gh subprocesses replaced."""

import importlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scrutare.engine import github


def cli_main(argv: list[str]) -> int:
    assert importlib.util.find_spec("scrutare.interfaces") is not None, "CLI is not implemented"
    return importlib.import_module("scrutare.interfaces.cli").main(argv)


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        assert args[:2] in (["gh", "api"], ["gh", "repo"])
        assert "--method" not in args and "-X" not in args
        if args[1] == "repo":
            output = '{"nameWithOwner":"owner/repo"}'
        elif "--header" in args:
            output = "diff --git a/a.py b/a.py\n"
        elif "--paginate" in args:
            output = '[[{"id":1}]]'
        else:
            output = json.dumps(
                {
                    "number": 12,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "abc123"},
                    "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
                    "changed_files": 1,
                }
            )
        return subprocess.CompletedProcess(args, 0, output.encode("utf-8"), b"")

    monkeypatch.setattr(github.subprocess, "run", run)
    return calls


def test_review_snapshots_config_and_reports_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "settings.yaml"
    config.write_bytes(b"not parsed: [\n")
    assert (
        cli_main(
            ["review", "--pr", "https://github.com/owner/repo/pull/12", "--config", str(config)]
        )
        == 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result == {
        "status": "ingested",
        "head_sha": "abc123",
        "scrutare_version": "0.1.0",
        "run_dir": result["run_dir"],
    }
    run = Path(result["run_dir"])
    assert run.parent.resolve() == Path(".scrutare/runs").resolve()
    assert (run / "config.yaml").read_bytes() == b"not parsed: [\n"
    assert json.loads((run / "metadata.json").read_text())["pull_request"]["number"] == 12
    assert {p.name for p in run.iterdir()} == {
        "metadata.json",
        "config.yaml",
        "diff.patch",
        "files.json",
        "reviews.json",
        "comments.json",
        "review_comments.json",
    }


def test_number_uses_repository_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli_main(["review", "--pr", "12"]) == 0
    result = json.loads(capsys.readouterr().out)
    metadata = json.loads((Path(result["run_dir"]) / "metadata.json").read_text())
    assert metadata["repository"] == "owner/repo"
    assert metadata["pr_number"] == 12


def test_version_needs_no_github(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["missing", "directory", "unreadable"])
def test_config_failure_precedes_network(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    config = tmp_path / "config.yaml"
    if kind == "directory":
        config.mkdir()
    if kind == "unreadable":
        config.write_text("settings")
        original = Path.open

        def denied(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self == config:
                raise PermissionError("permission denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    assert cli_main(["review", "--pr", "12", "--config", str(config)]) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "config" in output.err.lower()
    assert "Traceback" not in output.err
    assert gh == []


@pytest.mark.parametrize("failure", ["closed", "merged", "transport", "missing-gh"])
def test_github_failures_have_nonzero_concise_errors(
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if failure == "missing-gh":
            raise FileNotFoundError
        if failure == "transport":
            return subprocess.CompletedProcess(args, 1, b"", b"secret-token")
        output = json.dumps(
            {
                "number": 12,
                "state": "closed" if failure == "closed" else "open",
                "merged": failure == "merged",
                "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
                "changed_files": 1,
                "head": {"sha": "abc123"},
            }
        )
        return subprocess.CompletedProcess(args, 0, output.encode("utf-8"), b"")

    monkeypatch.setattr(github.subprocess, "run", run)
    assert cli_main(["review", "--pr", "https://github.com/owner/repo/pull/12"]) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip()
    assert "Traceback" not in output.err and "secret-token" not in output.err
    assert not (tmp_path / ".scrutare/runs").exists()


def test_persistence_failure_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".scrutare").write_text("blocked")
    assert cli_main(["review", "--pr", "https://github.com/owner/repo/pull/12"]) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "filesystem" in output.err.lower()
    assert "Traceback" not in output.err


@pytest.mark.parametrize("argv", [[], ["review"], ["replay", "run"]])
def test_invalid_arguments_return_nonzero(argv: list[str]) -> None:
    assert cli_main(argv) != 0
