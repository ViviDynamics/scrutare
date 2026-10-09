"""Installed adapter exposes diagnostics as data and never synthesizes findings."""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_installed_review import GH_WORKER
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import offline_runtime, text, tool
from test_repository_context import Objects, blob_id
from test_review_inputs import SOURCE


@pytest.mark.parametrize("assessment_enabled", [False, True])
def test_installed_static_analysis_data_only_and_replay_tamper(
    tmp_path, installed, wheel_cli, assessment_enabled,
):
    strategy = "panel"
    _, _, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    provider = Objects()
    objects = {}
    for repository, revision in (("owner/repo", "b" * 40), ("fork/repo", "a" * 40)):
        objects[f"repos/{repository}/git/commits/{revision}"] = {
            "sha": revision,
            "tree": {"sha": "c" * 40},
        }
        for sha in ("c" * 40, "e" * 40, "f" * 40, "d" * 40):
            objects[f"repos/{repository}/git/trees/{sha}"] = {
                "sha": sha,
                "tree": provider.get_tree(repository, sha),
                "truncated": False,
            }
        for data in provider.content.values():
            sha = blob_id(data)
            objects[f"repos/{repository}/git/blobs/{sha}"] = {
                "sha": sha,
                "size": len(data),
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
            }
    from scrutare.engine.repository_context import _artifact

    replies = [
        tool(args={"path": "static-analysis.json"}, call_id="analysis"),
        tool(args={"path": "repository-context.json"}),
        tool(args={"path": _artifact("head", "src/caller.py")}, call_id="caller"),
        tool(args={"path": _artifact("head", "tests/test_caller.py")}, call_id="test"),
        text({"findings": []}),
    ]
    (tmp_path / "offline-spec.json").write_text(
        json.dumps(
            {
                "default": {"replies": replies},
                "scenarios": [
                    {
                        "purpose": "review",
                        "attempt": "attempt-0001",
                        "prompt_prefix": "Arbitrate as senior",
                        "scenario": {"replies": [text({"findings": [], "converged": True})]},
                    }
                ],
            }
        )
    )
    working = tmp_path / "outside-checkout"
    working.mkdir()
    (working / "scrutare.yaml").write_text(
        f"strategy: {strategy}\nrounds: {{max: 2}}\npersonas: [senior-dev]\n"
        "models: {default: {provider: openai, model: offline-model, "
        'base_url: "https://offline.invalid/v1"}}\n'
        "budgets: {per_persona_tokens: 400, review_max_tokens: 1200}\n"
        "context: {enabled: true, related_paths: [src/caller.py, tests/test_caller.py]}\n"
        "analysis: {enabled: true}\n"
        + ("findings: {evidence: v2, assessment: {enabled: true, tokens: 200}}\n"
           if assessment_enabled else "")
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_WORKER.read_text())
    gh.chmod(0o700)
    spec = tmp_path / "gh-spec.json"
    spec.write_text(
        json.dumps(
            {
                "case": "context",
                "objects": objects,
                "diff": SOURCE.decode(),
                "files": [{"filename": "src/app.py", "status": "modified"}],
                "metadata": {
                    "number": 12,
                    "state": "open",
                    "merged": False,
                    "changed_files": 1,
                    "head": {"sha": "a" * 40, "repo": {"full_name": "fork/repo"}},
                    "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
                },
            }
        )
    )
    env = {
        "PATH": f"{binary}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "SCRUTARE_GH_SPEC": str(spec),
        "SCRUTARE_GH_LOG": str(tmp_path / "gh.jsonl"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    from test_static_analysis import document
    capture = tmp_path / "trusted-ci.json"
    ruff = subprocess.run(
        ["ruff", "check", "--isolated", "--select", "F821", "--output-format", "json",
         "--stdin-filename", "/captured/repo/src/caller.py", "-"],
        input=b"call_changed()\n", capture_output=True, timeout=10,
    )
    assert ruff.returncode == 1, ruff.stderr.decode()
    capture.write_text(json.dumps(document(diagnostics=json.loads(ruff.stdout))))
    result = subprocess.run(
        [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable),
         "--analysis-capture", str(capture)],
        cwd=working,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode()
    run = Path(json.loads(result.stdout)["run_dir"])
    assert json.loads((run / "review-inputs/static-analysis.json").read_text())["diagnostics"]
    assert json.loads((run / "verdict.json").read_text())["findings"] == []
    if assessment_enabled:
        assessment = json.loads((run / "assessment.json").read_text())
        assert assessment["status"] == "complete"
        assert assessment["allocation_tokens"] == 200
        assert assessment["candidates"] == assessment["assessments"] == []
    roots = list(run.rglob("review-inputs/repository-context.json"))
    assert roots
    observations = list(run.rglob("offline-observations.json"))
    serialized = "\n".join(path.read_text() for path in observations)
    assert "call_changed()" in serialized and "assert caller()" in serialized
    assert all(json.loads(path.read_bytes())["guard_violations"] == [] for path in observations)
    assert all(json.loads(path.read_bytes())["credential_names"] == [] for path in observations)
    replay = subprocess.run(
        [str(console), "replay", str(run)], cwd=working, env=env, capture_output=True, timeout=30
    )
    assert replay.returncode == 0, replay.stderr.decode()

    assert "F821" in serialized
    raw = run / "static-analysis/source.json"
    raw.write_text(json.dumps(document(status="failed")))
    replay = subprocess.run(
        [str(console), "replay", str(run)], cwd=working, env=env, capture_output=True, timeout=30
    )
    assert replay.returncode != 0
