"""Installed scrutare wheel and installed nare persist real cross-push history offline."""
import json
import os
import subprocess
import sys
from pathlib import Path

from test_installed_review import GH_WORKER
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import FINDING, offline_runtime, text
from test_nare_cli_integration import installed as installed
from test_review_inputs import SOURCE


def test_installed_iterative_cross_push_review(tmp_path, installed, wheel_cli):
    _, _, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    working = tmp_path / "outside-checkout"
    working.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    worker = GH_WORKER.read_text().replace(
        'run, = Path(".scrutare/runs").iterdir()',
        'run = max(Path(".scrutare/runs").glob("run-*"), key=lambda p: p.stat().st_mtime_ns)')
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + worker)
    gh.chmod(0o700)
    spec_path = tmp_path / "gh-spec.json"
    log = tmp_path / "gh.jsonl"
    env = {"PATH": f"{binary}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8",
           "SCRUTARE_GH_SPEC": str(spec_path), "SCRUTARE_GH_LOG": str(log),
           "PYTHONDONTWRITEBYTECODE": "1"}
    (working / "scrutare.yaml").write_text(
        "strategy: iterative\nrounds: {max: 2}\npersonas: [senior-dev]\n"
        "models: {default: {provider: openai, model: offline-model, "
        "base_url: 'https://offline.invalid/v1'}}\n"
        "budgets: {per_persona_tokens: 100, review_max_tokens: 100}\n")
    history = []
    for index, (head, patch, findings, verdict, sessions) in enumerate((
        ("a" * 40, SOURCE, FINDING, "changes_requested", 1),
        ("b" * 40, SOURCE, FINDING, "changes_requested", 0),
        ("c" * 40, SOURCE.replace(b"+new", b"+fixed"), {"findings": []}, "approve", 1),
        ("d" * 40, SOURCE.replace(b"+new", b"+unreviewed"), FINDING, "escalated", 0),
    )):
        (tmp_path / "offline-spec.json").write_text(json.dumps(
            {"default": {"replies": [text(findings)]}}))
        spec_path.write_text(json.dumps({
            "case": "iterative", "diff": patch.decode(),
            "files": [{"filename": "src/app.py", "status": "modified"}],
            "metadata": {"number": 12, "state": "open", "merged": False,
                         "head": {"sha": head}, "changed_files": 1,
                         "base": {"ref": "main", "sha": "base123",
                                  "repo": {"full_name": "owner/repo"}}},
        }))
        result = subprocess.run(
            [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable)],
            cwd=working, env=env, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stderr.decode()
        data = json.loads(result.stdout)
        assert data["verdict"] == verdict and data["head_sha"] == head
        run = Path(data["run_dir"])
        pool = json.loads((run / "iterative.json").read_bytes())
        assert pool["rounds_completed"] == (1 if index < 2 else 2)
        assert len(list(run.glob("iterative-round/sessions/*/attempt-0001"))) == sessions
        if index >= 2:
            assert pool["pool"][0]["disposition"] == "fixed"
        manifest = json.loads((run / "artifacts.json").read_bytes())
        assert any(item["path"] == "iterative.json" for item in manifest["artifacts"])
        assert (run / "verdict.json").read_bytes().endswith(b"\n")
        history.append(run)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    posts = [json.loads(call["stdin"]) for call in calls if "POST" in call["args"]]
    assert [post["commit_id"] for post in posts] == [character * 40 for character in "abcd"]
    assert [post["event"] for post in posts] == [
        "REQUEST_CHANGES", "REQUEST_CHANGES", "APPROVE", "COMMENT"]
    assert not any("DISCUSSION_SENTINEL" in path.read_text() for run in history
                   for path in run.glob("iterative-round/review-inputs/*"))
    assert all(call["credential_names"] == [] for call in calls)
    assert os.access(console, os.X_OK)
