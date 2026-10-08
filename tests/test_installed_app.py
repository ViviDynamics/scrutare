"""App worker invokes the candidate installed wheel and real nare CLI offline."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from test_app import settings
from test_installed_review import GH_WORKER
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import offline_runtime, text
from test_review_inputs import SOURCE

from scrutare.interfaces.app import JobStore
from scrutare.interfaces.app_worker import run_job


def test_app_posts_through_installed_engine_and_retains_real_evidence(
        tmp_path, monkeypatch, installed, wheel_cli):
    _, python, _, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    monkeypatch.setenv("SCRUTARE_APP_NARE_EXECUTABLE", str(runtime.executable))
    (tmp_path / "offline-spec.json").write_text(json.dumps({
        "default": {"replies": [text({"findings": []})]}}))
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    worker = GH_WORKER.read_text().replace(
        'run, = Path(".scrutare/runs").iterdir()',
        'run = max((p for p in Path(".scrutare/runs").iterdir() '
        'if (p / "metadata.json").is_file()), '
        'key=lambda p: (p / "metadata.json").stat().st_mtime_ns)')
    gh.write_text(f"#!{sys.executable}\n" + worker)
    gh.chmod(0o700)
    spec = tmp_path / "gh-spec.json"
    spec.write_text(json.dumps({
        "case": "approval", "diff": SOURCE.decode(),
        "files": [{"filename": "src/app.py", "status": "modified"}],
        "metadata": {"number": 12, "state": "open", "merged": False,
                     "head": {"sha": "a" * 40}, "changed_files": 1,
                     "base": {"ref": "main", "sha": "base123",
                              "repo": {"full_name": "owner/repo"}}}}))
    config = replace(settings(tmp_path), model_origins=("https://api.anthropic.com",))
    store = JobStore(tmp_path / "jobs.sqlite")
    store.enqueue("opened", "owner/repo", 12, 456)
    class Client:
        def installation_token(self, repository, installation):
            return "offline-installation-token"
        def configuration(self, repository, token):
            return b"personas: [security]\nmodels: {default: {model: offline-model}}\n"
    def runner(argv, **kwargs):
        # Substitute only external GitHub transport and nare's offline vendor factory.
        argv[0] = str(python)
        kwargs["env"].update(PATH=f"{binary}:/usr/bin:/bin", SCRUTARE_GH_SPEC=str(spec),
                             SCRUTARE_GH_LOG=str(tmp_path / "gh.jsonl"))
        return subprocess.run(argv, timeout=60, **kwargs)
    first = store.claim()
    run_job(config, store, first, client=Client(), runner=runner)
    posted = store.get(first["id"])
    assert posted["status"] == "posted", posted
    run = Path(posted["result"]["run_dir"])
    assert posted["result"] == json.loads((run / "result.json").read_bytes())
    assert posted["result"]["verdict"] == "approve"
    assert (run / "artifacts.json").is_file() and (run / "posting.json").is_file()
    assert (run / "sessions/security/attempt-0001/session.json").is_file()
    observations = json.loads((run / "sessions/security/attempt-0001/"
                              "offline-observations.json").read_bytes())
    assert observations["credential_names"] == []
    assert observations["guard_violations"] == []
    # Push and API jobs share repository history, while each capture is immutable.
    store.enqueue("push", "owner/repo", 12, 456)
    second = store.claim()
    run_job(config, store, second, client=Client(), runner=runner)
    next_run = Path(store.get(second["id"])["result"]["run_dir"])
    assert next_run != run and next_run.parent == run.parent
    from scrutare.replay import replay_run
    assert replay_run(run).exit_code == replay_run(next_run).exit_code == 0
