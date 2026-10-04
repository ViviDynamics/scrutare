"""Exercise CLI output and failures with only gh subprocesses replaced."""

import importlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scrutare.engine import github

VALID_CONFIG = b"# preserved comment\r\nmodels:\r\n  default:\r\n    model: test-model\r\n"
NORMALIZED_CONFIG = {
    "strategy": "panel",
    "rounds": {"max": 3},
    "personas": ["senior-dev", "junior-dev", "security", "devops"],
    "budgets": {"per_persona_tokens": 100000, "review_max_tokens": 500000},
    "models": {
        "default": {"provider": "anthropic", "base_url": None, "model": "test-model"},
        "overrides": {},
    },
    "verdict": {
        "blocking_categories": ["correctness", "security", "regression"],
        "advisory_categories": ["style", "consistency", "docs"],
    },
    "github": {
        "post_mode": "review",
        "human_reviewers": [],
        "paths": {"include": [], "exclude": ["docs/**", "*.md"]},
    },
}


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
    config.write_bytes(VALID_CONFIG)
    (tmp_path / "scrutare.yaml").write_bytes(b"invalid: [\n")
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
    assert (run / "config.yaml").read_bytes() == VALID_CONFIG
    assert json.loads((run / "config.json").read_text()) == NORMALIZED_CONFIG
    assert json.loads((run / "metadata.json").read_text())["pull_request"]["number"] == 12
    assert {p.name for p in run.iterdir()} == {
        "metadata.json",
        "config.yaml",
        "config.json",
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
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    assert cli_main(["review", "--pr", "12"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (Path(result["run_dir"]) / "config.yaml").read_bytes() == VALID_CONFIG
    assert json.loads((Path(result["run_dir"]) / "config.json").read_text()) == NORMALIZED_CONFIG
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
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)

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
    assert {
        "closed": "closed",
        "merged": "merged",
        "transport": "failed",
        "missing-gh": "gh",
    }[failure] in output.err.lower()
    assert not (tmp_path / ".scrutare/runs").exists()


def test_persistence_failure_is_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    (tmp_path / ".scrutare").write_text("blocked")
    assert cli_main(["review", "--pr", "https://github.com/owner/repo/pull/12"]) != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "filesystem" in output.err.lower()
    assert "Traceback" not in output.err


@pytest.mark.parametrize("argv", [[], ["review"], ["replay", "run"]])
def test_invalid_arguments_return_nonzero(argv: list[str]) -> None:
    assert cli_main(argv) != 0


def test_missing_default_config_precedes_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli_main(["review", "--pr", "12"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "config" in output.err and "accessible regular file" in output.err
    assert "Traceback" not in output.err
    assert gh == []
    assert not (tmp_path / ".scrutare").exists()


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b"models: [\n", ["config", "YAML"]),
        (VALID_CONFIG + b"stratgey: panel\n", ["stratgey", "allowed", "strategy"]),
        (VALID_CONFIG + b"strategy: invalid\n", ["strategy", "panel", "iterative", "debate"]),
        (b"models:\n  default:\n    provider: invalid\n    model: test-model\n",
         ["models.default.provider", "anthropic", "openai"]),
        (b"strategy: panel\n", ["models.default.model", "nonempty string"]),
        (VALID_CONFIG + b"rounds: {max: true}\n", ["rounds.max", "positive integer"]),
    ],
)
def test_invalid_config_precedes_network_and_creates_no_run(
    raw: bytes,
    expected: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(raw)
    assert cli_main(["review", "--pr", "12"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    for message in expected:
        assert message in output.err
    assert "Traceback" not in output.err
    assert gh == []
    assert not (tmp_path / ".scrutare").exists()


@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_config_artifacts_use_bytes_validated_before_github(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    gh: list[list[str]],
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "scrutare.yaml"
    config.write_bytes(VALID_CONFIG)
    original_run = github.subprocess.run

    def mutate(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if not gh:
            if mutation == "replace":
                config.write_bytes(b"models: [\n")
            else:
                config.unlink()
        return original_run(args, **kwargs)

    monkeypatch.setattr(github.subprocess, "run", mutate)
    assert cli_main(["review", "--pr", "12"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    run = Path(json.loads(output.out)["run_dir"])
    assert (run / "config.yaml").read_bytes() == VALID_CONFIG
    assert json.loads((run / "config.json").read_text()) == NORMALIZED_CONFIG
