"""Exercise full review CLI with controlled runtime and GitHub transport boundaries."""

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_panel import install
from test_post_review import SHA, FakePoster

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
            output = (
                "diff --git a/a.py b/a.py\n"
                "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            )
        elif "--paginate" in args:
            output = (
                '[[{"filename":"a.py","status":"modified"}]]'
                if args[2].endswith('/files') else '[[{"id":1}]]'
            )
        else:
            output = json.dumps(
                {
                    "number": 12,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": SHA},
                    "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
                    "changed_files": 1,
                }
            )
        return subprocess.CompletedProcess(args, 0, output.encode("utf-8"), b"")

    monkeypatch.setattr(github.subprocess, "run", run)
    return calls


@pytest.fixture(autouse=True)
def engine_and_poster(monkeypatch):
    # Runtime execution and GitHub delivery are the only replaced boundaries.
    from scrutare.poster import posting
    install(monkeypatch, {})
    monkeypatch.setattr(posting, "ReviewClient", lambda **kwargs: FakePoster())


@pytest.fixture(autouse=True)
def present_default_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "bin" / "nare"
    runtime.parent.mkdir()
    runtime.symlink_to(sys.executable)
    monkeypatch.setenv("PATH", str(runtime.parent))


def test_review_snapshots_config_and_reports_persisted_delivery(
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
    assert result["status"] == "posted"
    assert result["head_sha"] == SHA
    assert result["scrutare_version"] == "2026.10.2"
    assert result["verdict"] == "approve"
    assert result["review"]["state"] == "APPROVED"
    assert result["panel_status"] == "complete"
    assert result["accounting_complete"]
    assert (Path(result["run_dir"]) / "result.json").read_bytes() == captured.out.encode()
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
        "effective-files.json",
        "review-inputs",
        "sessions", "fanout.json", "panel.json", "findings.json", "verdict.json",
        "posting.json", "review-payload.json", ".posting.lock", "result.json", "artifacts.json",
    }

    assert json.loads((run / "effective-files.json").read_text())["files"] == ["a.py"]
    root = run / "review-inputs"
    assert {p.name for p in root.iterdir()} == {"diff.patch", "files.json", "context.json"}
    assert (root / "diff.patch").read_bytes() == (
        b"diff --git a/a.py b/a.py\n"
        b"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    )


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
    assert capsys.readouterr().out == "scrutare 2026.10.2\n"


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
                "head": {"sha": SHA},
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


def test_cli_preparation_failure_has_a_safe_error_and_removes_fresh_run(
    tmp_path, monkeypatch, capsys, gh,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    original = github.subprocess.run

    def invalid_diff(args, **kwargs):
        result = original(args, **kwargs)
        if "--header" in args:
            return subprocess.CompletedProcess(args, 0, b"HOSTILE_DIFF_DIAGNOSTIC", b"")
        return result

    monkeypatch.setattr(github.subprocess, "run", invalid_diff)
    assert cli_main(["review", "--pr", "https://github.com/owner/repo/pull/12"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "review inputs" in output.err.lower() or "prepar" in output.err.lower()
    assert "HOSTILE" not in output.err and "Traceback" not in output.err
    assert list((tmp_path / ".scrutare/runs").iterdir()) == []


def test_unavailable_nare_rejected_before_any_github(tmp_path, monkeypatch, capsys, gh):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    assert cli_main(["review", "--pr", "12", "--nare-executable", "/absent/nare"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "executable" in output.err
    assert gh == [] and not (tmp_path / ".scrutare").exists()


def test_explicit_runtime_and_interruption_have_exit_130_after_cleanup(
        tmp_path, monkeypatch, capsys, gh):
    import asyncio

    from scrutare.engine import fanout
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    async def interrupted(*args, **kwargs):
        try:
            raise asyncio.CancelledError
        finally:
            await asyncio.sleep(0)
            (kwargs["artifact_directory"] / "cleanup.json").write_text("{}")
    monkeypatch.setattr(fanout, "run_persona_session", interrupted)
    assert cli_main(["review", "--pr", "12", "--nare-executable", sys.executable]) == 130
    output = capsys.readouterr()
    assert output.out == "" and "interrupt" in output.err.lower()
    run = next((tmp_path / ".scrutare/runs").iterdir())
    assert str(run.relative_to(tmp_path)) in output.err
    assert (run / "artifacts.json").is_file()


@pytest.mark.parametrize("failure", ["panel", "result", "manifest"])
def test_cli_failure_never_prints_success_and_identifies_run(
        tmp_path, monkeypatch, capsys, gh, failure):
    import scrutare.engine.review as review
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    if failure == "panel":
        install(monkeypatch, {}, initial_available=False)
    else:
        def broken(*args, **kwargs):
            raise OSError("SECRET")
        monkeypatch.setattr(review, "write_owned_bytes" if failure == "result"
                            else "write_artifact_manifest", broken)
    assert cli_main(["review", "--pr", "12"]) == 1
    output = capsys.readouterr()
    run = next((tmp_path / ".scrutare/runs").iterdir())
    assert output.out == "" and str(run.relative_to(tmp_path)) in output.err
    assert "SECRET" not in output.err
    assert ("confirmed" in output.err.lower()) == (failure != "panel")


def test_no_stdout_until_result_and_manifest_are_installed(tmp_path, monkeypatch, capsys, gh):
    from scrutare.engine import review
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    original = review.write_artifact_manifest
    def manifest(run):
        assert capsys.readouterr().out == ""
        assert json.loads((run / "result.json").read_bytes())["status"] == "posted"
        return original(run)
    monkeypatch.setattr(review, "write_artifact_manifest", manifest)
    assert cli_main(["review", "--pr", "12"]) == 0
    output = capsys.readouterr()
    run = Path(json.loads(output.out)["run_dir"])
    assert (run / "result.json").read_bytes() == output.out.encode()
    assert (run / "artifacts.json").is_file()


@pytest.mark.parametrize("mode", ["review", "comment"])
def test_blocking_verdict_is_successfully_delivered_with_exit_zero(
        tmp_path, monkeypatch, capsys, gh, mode):
    from test_panel import finding
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(
        VALID_CONFIG + f"github: {{post_mode: {mode}}}\n".encode())
    install(monkeypatch, {"security": (finding(file="a.py"),)})
    assert cli_main(["review", "--pr", "12"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "changes_requested"
    assert data["review"]["state"] == ("COMMENTED" if mode == "comment"
                                        else "CHANGES_REQUESTED")


def test_stdout_bytes_match_persisted_utf8_even_with_non_utf8_terminal(
        tmp_path, monkeypatch, gh):
    import io

    from test_panel import finding
    tmp_path = tmp_path / "révision"
    tmp_path.mkdir()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    install(monkeypatch, {"security": (finding(file="a.py", problem="Sécurité"),)})
    stream = io.BytesIO()
    terminal = io.TextIOWrapper(stream, encoding="ascii", errors="backslashreplace")
    monkeypatch.setattr(sys, "stdout", terminal)
    assert cli_main(["review", "--pr", "12"]) == 0
    terminal.flush()
    run = next((tmp_path / ".scrutare/runs").iterdir())
    assert stream.getvalue() == (run / "result.json").read_bytes()


@pytest.mark.parametrize("phase", ["posting", "result", "manifest", "confirmation"])
def test_native_sigint_retains_run_and_confirmed_posting(
        tmp_path, monkeypatch, capsys, gh, phase):
    import signal

    from scrutare.engine import review
    from scrutare.poster import posting
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    receipt_returned = phase != "posting"
    if phase == "posting":
        class InterruptedPoster(FakePoster):
            def create_review(self, ref, payload):
                nonlocal receipt_returned
                receipt = super().create_review(ref, payload)
                signal.raise_signal(signal.SIGINT)
                receipt_returned = True
                return receipt
        monkeypatch.setattr(posting, "ReviewClient", lambda **kwargs: InterruptedPoster())
    elif phase == "confirmation":
        original = posting.atomic_write
        def interrupted_confirmation(path, data):
            original(path, data)
            if path.name == "posting.json" and json.loads(data)["status"] == "posted":
                # First signal requests task cancellation, the second interrupts synchronous code.
                signal.raise_signal(signal.SIGINT)
                signal.raise_signal(signal.SIGINT)
        monkeypatch.setattr(posting, "atomic_write", interrupted_confirmation)
    else:
        name = "write_owned_bytes" if phase == "result" else "write_artifact_manifest"
        original = getattr(review, name)
        def interrupted_write(*args, **kwargs):
            original(*args, **kwargs)
            signal.raise_signal(signal.SIGINT)
        monkeypatch.setattr(review, name, interrupted_write)
    assert cli_main(["review", "--pr", "12"]) == 130
    output = capsys.readouterr()
    run = next((tmp_path / ".scrutare/runs").iterdir())
    assert output.out == ""
    assert str(run.relative_to(tmp_path)) in output.err
    assert ("confirmed" in output.err) == receipt_returned
    assert "interrupt" in output.err
    assert json.loads((run / "posting.json").read_bytes())["status"] == (
        "posted" if receipt_returned else "sending")
    assert (run / "artifacts.json").is_file()


@pytest.mark.parametrize("outcome", ["return", "rejected", "interrupt"])
def test_native_sigint_during_escalation_preserves_first_confirmed_review(
        tmp_path, monkeypatch, capsys, gh, outcome):
    import signal

    from scrutare.engine import panel
    from scrutare.findings import Exhaustion, derive_verdict
    from scrutare.poster import PostingRejected, ReviewerRequestReceipt, escalation
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(
        VALID_CONFIG + b"github: {human_reviewers: [alice]}\n")
    derive = panel.derive_verdict
    # The real panel derives/installs a real exhausted Verdict at this controlled engine seam.
    def exhausted(findings, config):
        result = derive(findings, config)
        return derive_verdict(result.findings, config, exhaustion=Exhaustion("panel", 3, 3))
    monkeypatch.setattr(panel, "derive_verdict", exhausted)
    class InterruptedEscalator(FakePoster):
        def request_reviewers(self, ref, reviewers):
            signal.raise_signal(signal.SIGINT)
            if outcome == "rejected":
                raise PostingRejected("Request rejected.", status=422)
            if outcome == "interrupt":
                signal.raise_signal(signal.SIGINT)
            return ReviewerRequestReceipt(reviewers, "post_response")
    monkeypatch.setattr(escalation, "ReviewClient", lambda **kwargs: InterruptedEscalator())
    assert cli_main(["review", "--pr", "12"]) == 130
    output = capsys.readouterr()
    run = next((tmp_path / ".scrutare/runs").iterdir())
    assert output.out == "" and str(run.relative_to(tmp_path)) in output.err
    assert "confirmed" in output.err and "interrupt" in output.err
    assert json.loads((run / "posting.json").read_bytes())["status"] == "posted"
    assert (run / "artifacts.json").is_file()


def test_native_sigint_during_failed_run_snapshot_keeps_unconfirmed_context(
        tmp_path, monkeypatch, capsys, gh):
    import signal

    from scrutare.engine import review
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(VALID_CONFIG)
    install(monkeypatch, {}, initial_available=False)
    original = review.write_artifact_manifest
    def interrupted_manifest(run):
        original(run)
        signal.raise_signal(signal.SIGINT)
    monkeypatch.setattr(review, "write_artifact_manifest", interrupted_manifest)
    assert cli_main(["review", "--pr", "12"]) == 130
    output = capsys.readouterr()
    run = next((tmp_path / ".scrutare/runs").iterdir())
    assert output.out == "" and str(run.relative_to(tmp_path)) in output.err
    assert "interrupt" in output.err and "confirmed" not in output.err
    assert (run / "artifacts.json").is_file()
    assert not (run / "posting.json").exists()
