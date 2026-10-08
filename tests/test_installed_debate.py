"""Installed wheel executes actual nare perspective and chair sessions offline."""

import json
import subprocess
import sys

import pytest
from test_installed_review import GH_WORKER, load
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import (
    FINDING,
    offline_runtime,
    text,
)
from test_nare_cli_integration import (
    installed as installed,
)
from test_review_inputs import SOURCE


@pytest.mark.parametrize(
    "converged,category,want,rounds",
    [
        (True, "correctness", "changes_requested", 1),
        (True, "style", "approve", 1),
        (False, "correctness", "escalated", 2),
    ],
)
def test_installed_debate_pipeline(
    tmp_path, installed, wheel_cli, converged, category, want, rounds
):
    _, python, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    chosen = {"findings": [dict(FINDING["findings"][0], category=category)], "converged": converged}
    scenarios = [
        {
            "purpose": "review",
            "attempt": "attempt-0001",
            "prompt_prefix": "Review the captured",
            "scenario": {"replies": [text(FINDING)]},
        }
    ]
    for number in range(1, 3):
        scenarios += [
            {
                "purpose": "review",
                "attempt": f"attempt-{number + 2:04d}",
                "prompt_prefix": "Reconsider your position",
                "scenario": {"replies": [text(FINDING)]},
            },
            {
                "purpose": "review",
                "attempt": f"attempt-{number:04d}",
                "prompt_prefix": "Arbitrate as senior developer",
                "scenario": {"replies": [text(chosen)]},
            },
        ]
    (tmp_path / "offline-spec.json").write_text(json.dumps({"scenarios": scenarios}))
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_WORKER.read_text())
    gh.chmod(0o700)
    gh_spec, gh_log = tmp_path / "gh-spec.json", tmp_path / "gh.jsonl"
    gh_spec.write_text(
        json.dumps(
            {
                "case": "blocking",
                "diff": SOURCE.decode(),
                "files": [{"filename": "src/app.py", "status": "modified"}],
                "metadata": {
                    "number": 12,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "a" * 40},
                    "changed_files": 1,
                    "base": {"ref": "main", "sha": "base123", "repo": {"full_name": "owner/repo"}},
                },
            }
        )
    )
    working = tmp_path / "outside"
    working.mkdir()
    (working / "scrutare.yaml").write_text(
        "strategy: debate\nrounds: {max: 2}\npersonas: [security]\n"
        "models: {default: {provider: openai, model: offline-model, "
        "base_url: 'https://offline.invalid/v1'}}\n"
        "budgets: {per_persona_tokens: 100, review_max_tokens: 200}\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{binary}:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "SCRUTARE_GH_SPEC": str(gh_spec),
        "SCRUTARE_GH_LOG": str(gh_log),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable)],
        cwd=working,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode()
    envelope = json.loads(result.stdout)
    assert envelope["verdict"] == want
    (run,) = (working / ".scrutare/runs").iterdir()
    debate = load(run / "debate.json")
    assert len(debate["rounds"]) == rounds
    assert debate["ledger"]["usage"]["total"] == 15 * (1 + 2 * rounds)
    assert debate["ledger"]["allocations"] == {"security": 100, "debate-chair": 100}
    assert debate["ledger"]["active_reservations"] == []
    for attempt in run.glob("sessions/*/attempt-*"):
        invocation = load(attempt / "invocation.json")
        observations = load(attempt / "offline-observations.json")
        session = load(attempt / "session.json")
        assert observations["guard_violations"] == observations["credential_names"] == []
        assert observations["version"] == "2026.10.4"
        assert session["policy"] == {"tools": ["read"], "root": str(run / "review-inputs")}
        assert session["budget"]["tokens"] == invocation["invocation_limit"]
        if attempt.parent.name == "debate-chair":
            prompt = invocation["argv"][2]
            assert '"verified_pool"' in prompt and '"positions"' in prompt
            assert '"persona":"security"' in prompt
    replay = subprocess.run(
        [str(python), "-m", "scrutare", "replay", str(run)],
        cwd=working,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert replay.returncode == 0, replay.stderr.decode()
    assert json.loads(replay.stdout)["saved_verdict"]["byte_identical"] is True
    posts = [json.loads(line) for line in gh_log.read_text().splitlines() if '"POST"' in line]
    assert len(posts) == 1
    assert json.loads(posts[0]["stdin"])["event"] == (
        "COMMENT" if want == "escalated" else "APPROVE" if want == "approve" else "REQUEST_CHANGES"
    )
