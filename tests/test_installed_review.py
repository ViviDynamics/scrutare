"""Installed wheel through real capture, nare, panel, poster and offline replay.

Only GitHub's executable boundary and nare's vendor factory are substituted.
A missing post, fabricated empty output or drift in canonical bytes must fail here.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_nare_cli_integration import (
    BAD_ANCHOR,
    CORRECTED,
    FINDING,
    offline_runtime,
    text,
    tool,
)
from test_nare_cli_integration import (
    installed as installed,
)
from test_review_inputs import DOCS, SOURCE

ROOT = Path(__file__).resolve().parents[1]
GH_WORKER = ROOT / "tests/helpers/installed_gh.py"
VERSION = "2026.10.2"
PRIVATE_ERROR = "PRIVATE_PROVIDER_ERROR_SENTINEL"


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def wheel_cli(tmp_path_factory):
    root = tmp_path_factory.mktemp("installed-wheel")
    # Build this exact source, without trusting stale dist/ contents.
    for command in (
        ["uv", "build", "--python", sys.executable, "--wheel", "--out-dir", str(root / "dist")],
        ["uv", "venv", "--python", sys.executable, str(root / "venv")],
    ):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr.decode()
    wheel, = (root / "dist").glob("*.whl")
    python = root / "venv/bin/python"
    result = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode()
    return root, python, root / "venv/bin/scrutare", wheel


CASES = [
    ("approval", {"replies": [tool(), text({"findings": []})]}, 100, 0, "approve"),
    ("force-push", {"replies": [text(FINDING)]}, 100, 0, "changes_requested"),
    ("blocking", {"replies": [text(FINDING)]}, 100, 0, "changes_requested"),
    ("correction", {"replies": [text(BAD_ANCHOR)]}, 100, 0, "changes_requested"),
    ("comment", {"replies": [text(FINDING)]}, 100, 0, "changes_requested"),
    ("partial", {"replies": [tool(document=FINDING)]}, 1, 0, "changes_requested"),
    ("partial-empty", {"replies": [tool(document={"findings": []})]}, 1, 0, "approve"),
    ("mixed", {"replies": [text({"findings": []})], "barrier_participants": 2},
     100, 1, None),
    ("after-document", {"replies": [tool(document={"findings": []}),
                                   {"error": PRIVATE_ERROR}]}, 100, 1, None),
    ("failed", {"error": "offline initial failure"}, 100, 1, None),
    ("failed-shell", {"error": "offline initial failure"}, 100, 1, None),
    ("missing", {"replies": [tool()]}, 1, 1, None),
    ("closed", {"replies": [text(FINDING)]}, 100, 1, "changes_requested"),
    ("uncertain", {"replies": [text(FINDING)]}, 100, 1, "changes_requested"),
    ("iterative", {}, 100, 1, None),
    ("debate", {}, 100, 1, None),
    ("no-runtime", {}, 100, 1, None),
]


@pytest.mark.parametrize("case,initial,limit,exit_code,verdict", CASES,
                         ids=[item[0] for item in CASES])
def test_installed_review_pipeline(
    tmp_path, installed, wheel_cli, request, case, initial, limit, exit_code, verdict,
):
    shell_caller = case == "failed-shell"
    if shell_caller:
        case = "failed"
    root, python, console, wheel = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    spec = {"scenarios": [
        {"purpose": "review", "attempt": "attempt-0001", "prompt_prefix": "Review the captured",
         "scenario": initial},
        {"purpose": "reanchor", "attempt": "attempt-0002",
         "prompt_prefix": "Correct only the anchors", "scenario": {"replies": [text(CORRECTED)]}},
    ]}
    (tmp_path / "offline-spec.json").write_text(json.dumps(spec))
    home = tmp_path / "home"
    home.mkdir()
    working = tmp_path / "outside checkout café"
    working.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_WORKER.read_text())
    gh.chmod(0o700)
    gh_spec = tmp_path / "gh-spec.json"
    gh_log = tmp_path / "gh.jsonl"
    gh_spec.write_text(json.dumps({
        "case": case, "diff": (SOURCE + DOCS).decode(),
        "files": [{"filename": "src/app.py", "status": "modified", "patch": "HOSTILE_FIELD"},
                  {"filename": "docs/secret.md", "status": "modified"}],
        "metadata": {"number": 12, "state": "open", "merged": False,
                     "head": {"sha": "a" * 40}, "changed_files": 2,
                     "body": "RAW_BODY_SENTINEL", "base": {"ref": "main", "sha": "base123",
                     "repo": {"full_name": "owner/repo"}}},
    }))
    personas = ["senior-dev", "security"] if case == "mixed" else ["senior-dev"]
    config = (
        "# exact raw config: café\r\n"
        f"personas: {json.dumps(personas)}\r\n"
        "models: {default: {provider: openai, model: offline-model, "
        "base_url: 'https://offline.invalid/v1'}}\r\n"
        f"budgets: {{review_max_tokens: {limit * len(personas)}, per_persona_tokens: {limit}}}\r\n"
        f"strategy: {case if case in ('iterative', 'debate') else 'panel'}\r\n"
        f"github: {{post_mode: {'comment' if case == 'comment' else 'review'}}}\r\n"
    ).encode()
    (working / "scrutare.yaml").write_bytes(config)
    env = {"PATH": f"{binary}:/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
           "SCRUTARE_GH_SPEC": str(gh_spec), "SCRUTARE_GH_LOG": str(gh_log),
           "PYTHONDONTWRITEBYTECODE": "1"}
    commands = []

    def execute(arguments, name):
        result = subprocess.run([str(a) for a in arguments], cwd=working, env=env,
                                capture_output=True, timeout=60)
        (tmp_path / f"{name}.stdout").write_bytes(result.stdout)
        (tmp_path / f"{name}.stderr").write_bytes(result.stderr)
        commands.append({"argv": [str(a) for a in arguments], "cwd": str(working),
                         "exit": result.returncode, "stdout": f"{name}.stdout",
                         "stderr": f"{name}.stderr"})
        return result

    try:
        identity = execute([python, "-c",
            "import importlib.metadata,json,pathlib,scrutare; "
            "from scrutare.personas import load_persona; "
            "print(json.dumps({'path':str(pathlib.Path(scrutare.__file__).resolve()),"
            "'version':scrutare.__version__,'metadata':importlib.metadata.version('scrutare'),"
            "'systems':{name:load_persona(name).system_prompt for name in "
            f"{personas!r}" + "}}))"], "identity")
        assert identity.returncode == 0, identity.stderr
        info = json.loads(identity.stdout)
        assert Path(info["path"]).is_relative_to(root / "venv")
        assert "site-packages" in Path(info["path"]).parts
        assert info["version"] == info["metadata"] == VERSION
        for name, entrypoint in (("console-version", [console]),
                                 ("module-version", [python, "-m", "scrutare"])):
            version = execute([*entrypoint, "--version"], name)
            assert version.returncode == 0 and version.stdout == f"scrutare {VERSION}\n".encode()
        if case == "mixed":
            # Select each persona at the existing factory seam; the real fan-out must
            # reach the two-process barrier before either provider can finish.
            spec = {"systems": {
                info["systems"]["senior-dev"]: initial,
                info["systems"]["security"]: {
                    "error": PRIVATE_ERROR, "barrier_participants": 2,
                },
            }}
            (tmp_path / "offline-spec.json").write_text(json.dumps(spec))
        entrypoint = [python, "-m", "scrutare"] if case == "comment" or shell_caller else [console]
        arguments = [*entrypoint, "review", "--pr", "12", "--nare-executable",
                     tmp_path / "missing-nare" if case == "no-runtime" else runtime.executable]
        if shell_caller:
            arguments = ["/bin/bash", "-e", "-c",
                         '"$@"\nprintf continued > continuation-marker\n', "review-caller",
                         *arguments]
        result = execute(arguments, "review")
        assert result.returncode == exit_code, result.stderr.decode()
        if shell_caller:
            assert not (working / "continuation-marker").exists()
        calls = [json.loads(line) for line in gh_log.read_text().splitlines()] if (
            gh_log.exists()) else []
        assert all(call["credential_names"] == [] for call in calls)
        posts = [call for call in calls if "POST" in call["args"]]
        if case == "force-push":
            # Observe the external transition before checking capture binding, so
            # an unchanged fake head cannot make this success proof vacuous.
            metadata_calls = [call for call in calls if call["metadata_response"] is not None]
            assert len(metadata_calls) == 3
            assert [call["metadata_response"]["head"]["sha"] for call in metadata_calls] == [
                "a" * 40, "a" * 40, "b" * 40]
            assert all(call["metadata_response"]["state"] == "open"
                       and call["metadata_response"]["merged"] is False for call in metadata_calls)
            assert calls.index(metadata_calls[-1]) < calls.index(posts[0])
        if case in ("iterative", "debate", "no-runtime"):
            assert calls == [] and not (working / ".scrutare").exists()
            assert result.stdout == b""
            assert (b"not yet implemented" if case != "no-runtime" else
                    b"cannot locate an executable nare runtime") in result.stderr
            return
        run, = (working / ".scrutare/runs").iterdir()
        if case == "force-push":
            captured = load(run / "metadata.json")
            assert captured["head_sha"] == captured["pull_request"]["head"]["sha"] == "a" * 40
        assert (run / "config.yaml").read_bytes() == config
        assert (run / "diff.patch").read_bytes() == SOURCE + DOCS
        assert (run / "review-inputs/diff.patch").read_bytes() == SOURCE
        assert {p.name for p in (run / "review-inputs").iterdir()} == {
            "diff.patch", "files.json", "context.json"}
        manifest = load(run / "artifacts.json")
        assert manifest["scrutare_version"] == VERSION
        inventory = [{"path": p.relative_to(run).as_posix(), "sha256": digest(p),
                      "size_bytes": p.stat().st_size}
                     for p in sorted(run.rglob("*")) if p.is_file()
                     and p.name not in ("artifacts.json", ".posting.lock")]
        assert manifest["artifacts"] == inventory
        panel = load(run / "panel.json")
        assert panel["head_sha"] == "a" * 40 and panel["strategy"] == "panel"
        assert panel["scrutare_version"] == VERSION
        assert panel["convergence_passes"] == 1
        assert panel["ledger"]["active_reservations"] == []
        attempts = sorted(run.glob("sessions/*/attempt-*"),
                          key=lambda path: (personas.index(path.parent.name), path.name))
        assert len(attempts) == (2 if case in ("correction", "mixed") else 1)
        ledger = panel["ledger"]
        assert ledger["configured"] == {"per_persona_tokens": limit,
                                         "review_max_tokens": limit * len(personas)}
        assert ledger["allocations"] == {name: limit for name in personas}
        assert ledger["accounting_complete"] is (
            case not in ("failed", "mixed", "after-document"))
        assert ledger["overshoot_tokens"] == (
            14 if case in ("partial", "partial-empty", "missing") else 0)
        totals = []
        for attempt in attempts:
            observed = load(attempt / "offline-observations.json")
            invocation = load(attempt / "invocation.json")
            session = load(attempt / "session.json")
            outcome = load(attempt / "result.json")
            totals.append(outcome["usage"]["total"])
            expected_limit = 85 if attempt.name == "attempt-0002" else limit
            assert outcome["allocated_tokens"] == invocation["allocated_tokens"] == expected_limit
            assert outcome["invocation_limit"] == invocation["invocation_limit"] == expected_limit
            assert outcome["overshoot_tokens"] == (
                14 if case in ("partial", "partial-empty", "missing") else 0)
            if case in ("failed", "missing", "partial", "partial-empty", "mixed",
                        "after-document"):
                # Literal expectations distinguish missing output from valid empty
                # evidence and preserve actual usage even when confidence is lost.
                expected = {
                    "failed": ("failed", "provider", False, False, 0),
                    "missing": ("partial", "budget", False, True, 15),
                    "partial": ("partial", "budget", True, True, 15),
                    "partial-empty": ("partial", "budget", True, True, 15),
                    "after-document": ("failed", "provider", True, False, 15),
                    "mixed": (("failed", "provider", False, False, 0)
                              if attempt.parent.name == "security"
                              else ("complete", "done", True, True, 15)),
                }[case]
                assert tuple(outcome[key] for key in (
                    "status", "reason", "output_available", "accounting_complete")) + (
                    outcome["usage"]["total"],) == expected
            recorded, = [entry for entry in ledger["sessions"]
                         if entry["session_key"] == outcome["session_key"]]
            assert recorded["persona"] == attempt.parent.name
            assert recorded["usage"] == outcome["usage"]
            assert observed["guard_violations"] == observed["credential_names"] == []
            assert observed["nare_controls"] == {} and observed["version"] == "2026.10.4"
            assert observed["arguments"] == invocation["argv"][1:]
            assert observed["selection"]["purpose"] == (
                "reanchor" if attempt.name == "attempt-0002" else "review")
            assert observed["selection"]["attempt"] == attempt.name
            factory, = observed["factory_calls"]
            assert factory["system"] == info["systems"][attempt.parent.name]
            assert (factory["provider"], factory["model"], factory["base_url"]) == (
                "openai", "offline-model", "https://offline.invalid/v1")
            assert session["policy"] == {"tools": ["read"],
                                         "root": str(run / "review-inputs")}
            assert all([t["name"] for t in call["tools"]] == ["read"]
                       for call in observed["calls"])
            assert all(Path(p).is_relative_to(attempt) for p in observed["writes"])
            terminal = json.loads((attempt / "stdout.jsonl").read_text().splitlines()[-1])
            assert session["usage"] == terminal["usage"]
            assert session["output"] == terminal["output"]
            assert session["schema_retried"] is False
            if outcome["accounting_complete"]:
                assert session["usage"] == {"cost": None, **{
                    key: outcome["usage"][key] for key in
                    ("input", "output", "cache_read", "cache_write")}}
            assert session["budget"]["tokens"] == invocation["invocation_limit"]
            visible = json.dumps(observed["calls"])
            assert all(s not in visible for s in (
                "EXCLUDED_SENTINEL", "RAW_BODY_SENTINEL", "DISCUSSION_SENTINEL", "HOSTILE_FIELD"))
        assert sum(totals) == panel["ledger"]["usage"]["total"]
        assert len(ledger["sessions"]) == len(attempts)
        for entry in ledger["personas"]:
            assert entry["allocated_tokens"] == limit
            assert entry["overshoot_tokens"] == (
                14 if case in ("partial", "partial-empty", "missing") else 0)
            persona_attempts = [a for a in attempts if a.parent.name == entry["persona"]]
            assert entry["usage"]["total"] == sum(
                load(a / "result.json")["usage"]["total"] for a in persona_attempts)
        if case == "correction":
            assert [load(a / "invocation.json")["invocation_limit"] for a in attempts] == [100, 85]
            assert totals == [15, 15]
        if case in ("failed", "missing", "mixed", "after-document"):
            assert panel["corrections"] == [] and panel["verification"] is None
            assert panel["reason"] == "initial"
            assert all(attempt.name == "attempt-0001" for attempt in attempts)
            assert not any((run / name).exists() for name in (
                "posting.json", "review-payload.json", ".posting.lock"))
            if case in ("mixed", "after-document"):
                failed = attempts[-1] if case == "mixed" else attempts[0]
                failed_outcome = load(failed / "result.json")
                assert failed_outcome["persona"] == ("security" if case == "mixed"
                                                     else "senior-dev")
                assert failed_outcome["status"] == "failed"
                assert failed_outcome["reason"] == "provider"
                assert failed_outcome["accounting_complete"] is False
                assert PRIVATE_ERROR in (failed / "stdout.jsonl").read_text()
                # Provider detail stays in private captures, not public diagnostics.
                assert PRIVATE_ERROR.encode() not in result.stderr
                assert PRIVATE_ERROR not in json.dumps(panel)
                assert PRIVATE_ERROR not in (failed / "result.json").read_text()
                assert panel["ledger"]["accounting_complete"] is False
                if case == "mixed":
                    clean, broken = [load(a / "offline-observations.json") for a in attempts]
                    assert clean["pid"] != broken["pid"]
                    assert len(clean["calls"]) == len(broken["calls"]) == 1
                    assert broken["calls"][0]["start"] < clean["calls"][0]["end"]
                    assert all((a / "transport-ready.json").is_file() for a in attempts)
                    sibling = load(attempts[0] / "result.json")
                    assert sibling["status"] == "complete" and sibling["output_available"] is True
                    assert sibling["findings"] == []
                    assert load(attempts[0] / "session.json")["output"] == {"findings": []}
                else:
                    observed = load(failed / "offline-observations.json")
                    assert len(observed["calls"]) == 2
                    assert load(failed / "session.json")["output"] == {"findings": []}
                    assert failed_outcome["usage"]["total"] == 15
                    persisted = load(failed / "session.json")
                    read_result, = persisted["messages"][-1]["content"]
                    assert read_result == {"type": "tool_result", "tool_use_id": "call",
                                           "content": "\n".join(SOURCE.decode().splitlines()),
                                           "is_error": False}
                    assert observed["calls"][1]["messages"] == persisted["messages"]
                    assert failed_outcome["output_available"] is True
                    assert failed_outcome["findings"] == []

            assert posts == [] and result.stdout == b"" and b"Run: " in result.stderr
            assert panel["status"] == "failed"
            assert not (run / "verdict.json").exists() and not (run / "findings.json").exists()
            assert not (run / "result.json").exists()
            return
        assert panel["status"] == (
            "partial" if case in ("partial", "partial-empty") else "complete")
        assert panel["ledger"]["accounting_complete"] is True
        if case in ("partial", "partial-empty"):
            assert totals == [15] and load(attempts[0] / "result.json")["status"] == "partial"
        canonical = load(run / "verdict.json")
        assert canonical["verdict"] == verdict and canonical["schema_version"] == 1
        assert set(canonical) == {"schema_version", "verdict", "rule", "config", "findings"}
        assert (run / "verdict.json").read_bytes() == (
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        assert isinstance(load(run / "findings.json"), list)
        if case == "partial-empty":
            assert load(run / "findings.json") == canonical["findings"] == []
        if case == "closed":
            assert posts == [] and result.stdout == b"" and b"closed or merged" in result.stderr
        else:
            post, = posts
            assert post["args"][:3] == ["api", "--method", "POST"]
            assert "repos/owner/repo/pulls/12/reviews" in post["args"]
            assert "--include" in post["args"] and "--input" in post["args"]
            assert post["artifacts_before_post"] == {
                name: digest(run / name) for name in
                ("diff.patch", "config.yaml", "config.json", "findings.json", "verdict.json")}
            assert post["stdin"].encode() == (run / "review-payload.json").read_bytes()
            payload = json.loads(post["stdin"])
            assert payload["commit_id"] == "a" * 40
            if case == "force-push":
                assert f"Head SHA: {'a' * 40}\n" in payload["body"]
                assert "b" * 40 not in payload["body"]
            assert payload["event"] == ("COMMENT" if case == "comment" else
                                          "APPROVE" if verdict == "approve" else "REQUEST_CHANGES")
            assert len(payload["comments"]) == (0 if verdict == "approve" else 1)
            if payload["comments"]:
                assert {key: payload["comments"][0][key] for key in ("path", "line", "side")} == {
                    "path": "src/app.py", "line": 1, "side": "RIGHT"}
            if case == "uncertain":
                assert result.stdout == b"" and b"uncertain" in result.stderr
                assert load(run / "posting.json")["status"] == "unknown"
            else:
                assert result.stdout == (run / "result.json").read_bytes()
                saved = json.loads(result.stdout)
                assert "café".encode() in result.stdout
                assert saved["status"] == "posted" and saved["verdict"] == verdict
                assert saved["scrutare_version"] == VERSION and saved["run_dir"] == str(run)
                assert saved["panel_status"] == panel["status"]
                assert saved["usage"] == panel["ledger"]["usage"]
                assert saved["accounting_complete"] is True
                assert saved["review"]["review_id"] == 901
                if case == "force-push":
                    posting = load(run / "posting.json")
                    assert posting["status"] == "posted" and posting["attempts"] == 1
                    assert posting["head_sha"] == saved["head_sha"] == "a" * 40
                    assert posting["receipt"] == saved["review"]
                    assert saved["review"]["commit_id"] == "a" * 40
                    assert saved["review"]["body"] == payload["body"]
                    assert posting["payload_sha256"] == digest(run / "review-payload.json")
        if exit_code:
            assert not (run / "result.json").exists() and b"Run: " in result.stderr
        before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
        before_gh = gh_log.read_bytes()
        replay = execute([python, "-m", "scrutare", "replay", run], "replay")
        replayed = json.loads(replay.stdout)
        assert replayed["saved_verdict"]["byte_identical"] is True
        assert replayed["saved_verdict"]["sha256"] == digest(run / "verdict.json")
        assert replayed["recomputed_sha256"] == digest(run / "verdict.json")
        assert replayed["scrutare_version"] == VERSION
        if exit_code == 0:
            assert replay.returncode == 0 and replayed["posted_verdict"]["byte_identical"] is True
        else:
            assert replayed["posted_verdict"]["byte_identical"] is None
        assert all(p.read_bytes() == content for p, content in before.items())
        assert gh_log.read_bytes() == before_gh
    finally:
        (tmp_path / "commands.json").write_text(json.dumps(commands, indent=2))
        (tmp_path / "wheel.json").write_text(json.dumps({"name": wheel.name,
                                                       "sha256": digest(wheel)}))
        destination = os.environ.get("SCRUTARE_TEST_EVIDENCE_DIR")
        if destination:
            target = Path(destination) / request.node.name
            target.mkdir(parents=True, exist_ok=False)
            # Only this scenario's regular files, no virtualenvs or other pytest fixtures.
            for path in tmp_path.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    saved = target / path.relative_to(tmp_path)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, saved)
