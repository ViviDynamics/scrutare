"""Exercise offline replay at CLI, module and console boundaries."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_replay import bundle
from test_replay_posting import forbid_external, produced, read, write

from scrutare.interfaces.cli import main
from scrutare.replay import replay_run


def block_live_paths(monkeypatch):
    forbid_external(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("Replay entered live review, model or ambient configuration paths")

    for target in (
        "scrutare.interfaces.cli.parse_config", "scrutare.interfaces.cli.resolve_pr",
        "scrutare.interfaces.cli.GitHubClient", "scrutare.interfaces.cli.ingest_pr",
    ):
        monkeypatch.setattr(target, forbidden)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.stat().st_mode, path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in [root, *root.rglob("*")]
    }


@pytest.mark.parametrize("category,exhausted,verdict,rule", [
    ("docs", False, "approve", "no_blocking_findings"),
    ("security", False, "changes_requested", "any_blocking_finding"),
    ("security", True, "escalated", "rounds_exhausted_without_convergence"),
])
def test_replay_dispatches_offline_and_matches_python_result_without_writes(
    tmp_path, monkeypatch, capsys, category, exhausted, verdict, rule,
):
    root = tmp_path / "run artifacts with spaces"
    root.mkdir()
    run, _ = produced(root, category=category, exhausted=exhausted)
    expected = replay_run(run).to_dict()
    cwd = tmp_path / "unrelated working directory"
    cwd.mkdir()
    # Invalid ambient config must never participate in replay.
    (cwd / "scrutare.yaml").write_bytes(b"untrusted invalid config: [")
    monkeypatch.chdir(cwd)
    before = snapshot(tmp_path)
    block_live_paths(monkeypatch)
    assert main(["replay", str(run)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    document = json.loads(output.out)
    assert document == expected
    assert document["status"] == "identical"
    assert document["verdict"] == verdict
    assert document["rule"] == rule
    assert document["saved_verdict"]["byte_identical"] is True
    assert document["posted_verdict"]["byte_identical"] is True
    assert document["exhaustion_basis"] == ("recorded_assertion" if exhausted else "none")
    assert document["reviewer_request_status"] == ("posted" if exhausted else "absent")
    assert snapshot(tmp_path) == before
    assert main(["replay", str(run)]) == 0
    assert capsys.readouterr().out == output.out


def test_artifact_only_saved_identity_accepts_future_producer_and_extra_files(
    tmp_path, monkeypatch, capsys,
):
    bundle(tmp_path, exhausted=True)
    (tmp_path / "metadata.json").write_text(json.dumps({"scrutare_version": "9999.0.0"}))
    (tmp_path / "future-artifact.json").write_bytes(b"uninterpreted extra data")
    (tmp_path / "scrutare.yaml").write_bytes(b"invalid ambient config: [")
    monkeypatch.chdir(tmp_path)
    for path in tmp_path.iterdir():
        path.chmod(0o444)
    tmp_path.chmod(0o555)
    before = snapshot(tmp_path)
    block_live_paths(monkeypatch)
    assert main(["replay", str(tmp_path)]) == 0
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert output.err == ""
    assert document["status"] == "identical"
    assert document["saved_verdict"]["byte_identical"] is True
    assert document["posted_verdict"] == {
        "byte_identical": None, "sha256": None, "status": "absent",
    }
    assert document["exhaustion_basis"] == "unverified_recorded_assertion"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("change", ["source", "policy", "encoding"])
def test_replay_differences_return_one_with_named_finding_and_rule_changes(
    tmp_path, monkeypatch, capsys, change,
):
    original = bundle(tmp_path)
    if change == "source":
        findings = read(tmp_path, "findings.json")
        findings[0]["sources"][0]["category"] = "docs"
        write(tmp_path, findings, "findings.json")
    elif change == "policy":
        policy = read(tmp_path, "config.json")
        policy["verdict"] = {
            "blocking_categories": ["correctness", "regression"],
            "advisory_categories": ["security", "style", "consistency", "docs"],
        }
        write(tmp_path, policy, "config.json")
    else:
        (tmp_path / "verdict.json").write_text(json.dumps(original.to_dict()))
    expected = replay_run(tmp_path)
    block_live_paths(monkeypatch)
    assert main(["replay", str(tmp_path)]) == 1
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert output.err == ""
    assert document == expected.to_dict()
    assert document["status"] == "different"
    changes = {d["path"]: d for d in document["differences"]}
    if change == "encoding":
        assert set(changes) == {"verdict.encoding"}
        assert changes["verdict.encoding"]["kind"] == "encoding"
    else:
        assert changes["verdict.rule"]["before"] == "any_blocking_finding"
        assert changes["verdict.rule"]["after"] == "no_blocking_findings"
        if change == "source":
            difference = changes["verdict.findings[0].sources[0].category"]
            assert "src/café.py" in difference["finding"]
            assert "Wrong result" in difference["finding"]


@pytest.mark.parametrize("artifact", ["findings.json", "config.json", "verdict.json"])
@pytest.mark.parametrize("damage", ["missing", "invalid", "unsupported"])
def test_unavailable_or_invalid_artifacts_return_two_with_safe_diagnostics(
    tmp_path, monkeypatch, capsys, artifact, damage,
):
    bundle(tmp_path)
    target = tmp_path / artifact
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(b"SECRET\x1b[31m" if damage == "invalid"
                           else b'{"schema_version":999}')
    before = snapshot(tmp_path)
    block_live_paths(monkeypatch)
    assert main(["replay", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "Traceback" not in output.err and "SECRET" not in output.err + output.out
    assert "\x1b" not in output.err + output.out
    if artifact == "verdict.json":
        document = json.loads(output.out)
        assert document["status"] == "incomplete"
        assert document["verdict"] is None
        assert output.err == ""
    else:
        assert output.out == ""
        field = ({"findings.json": "findings", "config.json": "config.verdict"}[artifact]
                 if damage == "unsupported" else artifact)
        assert f"scrutare: {field}:" in output.err
    assert snapshot(tmp_path) == before


def test_missing_run_reports_safe_error_without_echoing_untrusted_directory(
    tmp_path, monkeypatch, capsys,
):
    block_live_paths(monkeypatch)
    assert main(["replay", str(tmp_path / "SECRET\x1b[31m\nrun")]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "findings.json" in output.err
    assert "SECRET" not in output.err and "\x1b" not in output.err
    assert "Traceback" not in output.err


def test_untrusted_finding_differences_are_json_escaped(tmp_path, monkeypatch, capsys):
    bundle(tmp_path)
    findings = read(tmp_path, "findings.json")
    hostile = "\x1b[31mline\n\t\"quoted\"\\path"
    findings[0]["sources"][0]["reason"] = hostile
    write(tmp_path, findings, "findings.json")
    block_live_paths(monkeypatch)
    assert main(["replay", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    assert "\x1b" not in output.out
    changes = {d["path"]: d for d in json.loads(output.out)["differences"]}
    assert changes["verdict.findings[0].sources[0].reason"]["after"] == hostile


@pytest.mark.parametrize("request_status", ["prepared", "sending", "unknown", "rejected"])
def test_pending_request_delivery_is_separate_from_posted_verdict_identity(
    tmp_path, monkeypatch, capsys, request_status,
):
    run, _ = produced(tmp_path, exhausted=True)
    state = read(run, "escalation.json")
    state.update(status=request_status, receipt=None, failure=None, retry_at=None, http_status=None)
    state["attempts"] = 0 if request_status == "prepared" else 1
    if request_status == "rejected":
        state.update(failure="permanent", http_status=403)
    write(run, state, "escalation.json")
    block_live_paths(monkeypatch)
    assert main(["replay", str(run)]) == 0
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert output.err == ""
    assert document["status"] == "identical"
    assert document["posted_verdict"]["byte_identical"] is True
    assert document["reviewer_request_status"] == request_status


@pytest.mark.parametrize("argv", [
    ["replay"], ["replay", "a", "b"], ["replay", "a", "--config", "b"],
    ["replay", "--pr", "12"],
])
def test_replay_usage_errors_return_two(argv, monkeypatch, capsys):
    block_live_paths(monkeypatch)
    assert main(argv) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "usage:" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("entrypoint", ["module", "console"])
@pytest.mark.parametrize("change,exit_code,status", [
    ("identical", 0, "identical"), ("encoding", 1, "different"),
    ("missing", 2, "incomplete"),
])
def test_process_entrypoints_replay_without_auth_config_or_external_operations(
    tmp_path, entrypoint, change, exit_code, status,
):
    run = tmp_path / "offline run with spaces"
    run.mkdir()
    original = bundle(run)
    if change == "encoding":
        (run / "verdict.json").write_text(json.dumps(original.to_dict()))
    elif change == "missing":
        (run / "verdict.json").unlink()
    cwd = tmp_path / "unrelated cwd"
    cwd.mkdir()
    guard = tmp_path / "startup guards"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sys, socket, subprocess\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('offline replay attempted external operation')\n"
        "subprocess.Popen = subprocess.run = forbidden\n"
        "socket.socket = socket.create_connection = forbidden\n"
        "class NoModels:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname.split('.')[0] in ('nare', 'openai', 'anthropic'):\n"
        "            forbidden()\n"
        "sys.meta_path.insert(0, NoModels())\n"
        "import scrutare.interfaces.cli as cli\n"
        "cli.parse_config = cli.resolve_pr = cli.GitHubClient = cli.ingest_pr = forbidden\n"
    )
    environment = {
        "PATH": "", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(guard), str(Path(__file__).parents[1] / "src"))),
        "LC_ALL": "C.UTF-8",
    }
    command = ([sys.executable, "-m", "scrutare"] if entrypoint == "module"
               else [str(Path(sys.executable).with_name("scrutare"))])
    before = snapshot(tmp_path)
    result = subprocess.run([*command, "replay", str(run)], cwd=cwd, env=environment,
                            capture_output=True, text=True, check=False)
    assert result.returncode == exit_code, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout)["status"] == status
    assert len(result.stdout.splitlines()) == 1
    assert snapshot(tmp_path) == before
