"""Exercise the standalone host adapter with synthetic runner data and processes."""

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[1] / "src/scrutare/interfaces/action.py"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


@pytest.fixture
def runner(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    temporary = tmp_path / "runner-temp"
    temporary.mkdir()
    event = tmp_path / "event.json"
    payload = {
        "number": 17,
        "action": "opened",
        "pull_request": {
            "number": 17,
            "base": {"sha": BASE_SHA, "repo": {"full_name": "Owner/Repo"}},
            "head": {"sha": "c" * 40, "repo": {"full_name": "Owner/Repo"}},
        },
    }
    event.write_text(json.dumps(payload))
    output = tmp_path / "output"
    output.touch()
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "Owner/Repo",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "RUNNER_TEMP": str(temporary),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(output),
        "SCRUTARE_ACTION_CONFIG": "scrutare.yaml",
        "SCRUTARE_ACTION_IMAGE": "ghcr.io/vividynamics/scrutare:2026.10.1",
    }
    return env, payload


def invoke(env, command):
    return subprocess.run(
        [sys.executable, str(ADAPTER), command],
        env=env,
        capture_output=True,
        check=False,
    )


def outputs(env):
    return dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())


def prepare(env):
    result = invoke(env, "prepare")
    assert result.returncode == 0, result.stderr
    return outputs(env)


def update_event(env, payload):
    Path(env["GITHUB_EVENT_PATH"]).write_text(json.dumps(payload))


def test_prepare_owns_unique_private_state_and_checkout(runner):
    env, _ = runner
    first = prepare(env)
    Path(env["GITHUB_OUTPUT"]).write_text("")
    second = prepare(env)
    assert set(first) == {
        "state-file",
        "checkout-path",
        "repository",
        "base-sha",
        "evidence-root",
        "artifact-name",
    }
    assert first["repository"] == "Owner/Repo"
    assert first["base-sha"] == BASE_SHA
    assert first["state-file"] != second["state-file"]
    assert first["checkout-path"] != second["checkout-path"]
    assert first["artifact-name"] != second["artifact-name"]
    state_path = Path(first["state-file"])
    assert state_path.is_relative_to(Path(env["RUNNER_TEMP"]))
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(Path(first["evidence-root"]).stat().st_mode) == 0o700
    checkout = Path(first["checkout-path"])
    assert not checkout.is_absolute()
    assert (Path(env["GITHUB_WORKSPACE"]) / checkout).is_dir()
    assert "token" not in state_path.read_text().lower()


@pytest.mark.parametrize(
    "name,value",
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REPOSITORY", "Other/Repo"),
        ("GITHUB_REPOSITORY", "Owner/Repo\nverdict=approve"),
        ("GITHUB_RUN_ID", "123\nverdict=approve"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("SCRUTARE_ACTION_CONFIG", "/tmp/config.yaml"),
        ("SCRUTARE_ACTION_CONFIG", "../config.yaml"),
        ("SCRUTARE_ACTION_CONFIG", "nested/../config.yaml"),
        ("SCRUTARE_ACTION_CONFIG", "config\nhead-sha=bad"),
        ("SCRUTARE_ACTION_CONFIG", "nested\\config.yaml"),
    ],
)
def test_prepare_refuses_unsafe_event_and_scalar_inputs(runner, name, value):
    env, _ = runner
    env[name] = value
    result = invoke(env, "prepare")
    assert result.returncode == 1
    assert outputs(env) == {}
    assert list(Path(env["RUNNER_TEMP"]).iterdir()) == []


@pytest.mark.parametrize("number", [0, -1, True, "17", None])
def test_prepare_requires_positive_integer_pr(runner, number):
    env, payload = runner
    payload["number"] = number
    payload["pull_request"]["number"] = number
    update_event(env, payload)
    assert invoke(env, "prepare").returncode == 1
    assert outputs(env) == {}


@pytest.mark.parametrize("sha", ["a" * 39, "g" * 40, "a" * 40 + "\nverdict=approve"])
def test_prepare_requires_exact_base_sha(runner, sha):
    env, payload = runner
    payload["pull_request"]["base"]["sha"] = sha
    update_event(env, payload)
    assert invoke(env, "prepare").returncode == 1
    assert outputs(env) == {}


def test_prepare_rejects_mismatched_base_repository(runner):
    env, payload = runner
    payload["pull_request"]["base"]["repo"]["full_name"] = "Other/Repo"
    update_event(env, payload)
    assert invoke(env, "prepare").returncode == 1
    assert outputs(env) == {}


@pytest.mark.parametrize(
    "event,fork,want",
    [
        ("pull_request", False, 0),
        ("pull_request", True, 1),
        ("pull_request_target", True, 0),
    ],
)
def test_prepare_gates_forks_by_event_context(runner, event, fork, want):
    env, payload = runner
    env["GITHUB_EVENT_NAME"] = event
    if fork:
        payload["pull_request"]["head"]["repo"]["full_name"] = "Fork/Repo"
    update_event(env, payload)
    assert invoke(env, "prepare").returncode == want
    if want:
        assert outputs(env) == {}


@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize", "ready_for_review"])
def test_prepare_accepts_supported_pr_actions(runner, action):
    env, payload = runner
    payload["action"] = action
    update_event(env, payload)
    prepare(env)


def test_prepare_refuses_unsupported_pr_action(runner):
    env, payload = runner
    payload["action"] = "closed"
    update_event(env, payload)
    assert invoke(env, "prepare").returncode == 1
    assert outputs(env) == {}


def test_prepare_refuses_image_override(runner):
    env, _ = runner
    env["SCRUTARE_ACTION_IMAGE"] = "attacker/image:latest"
    assert invoke(env, "prepare").returncode == 1
    assert outputs(env) == {}


def install_docker(tmp_path, env, scenario):
    """Replace only the external container launcher, retaining real host I/O."""
    executable = tmp_path / "docker"
    executable.with_suffix(".json").write_text(json.dumps(scenario))
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "base = pathlib.Path(__file__)\n"
        "args = sys.argv[1:]\n"
        "with base.with_suffix('.log').open('a') as log:\n"
        '    log.write(json.dumps(args) + "\\n")\n'
        'scenario = json.loads(base.with_suffix(".json").read_text())\n'
        'work = pathlib.Path(args[args.index("--workdir") + 1])\n'
        'mounts = [args[i+1] for i, value in enumerate(args) if value == "--volume"]\n'
        'config_mount = next(m for m in mounts if m.endswith(":/scrutare-config.yaml:ro"))\n'
        'config = pathlib.Path(config_mount.split(":")[0])\n'
        'base.with_suffix(".config").write_bytes(config.read_bytes())\n'
        'base.with_suffix(".mode").write_text(str(config.stat().st_mode & 0o777))\n'
        'run = work / ".scrutare/runs/captured"\n'
        "run.mkdir(parents=True, mode=0o700)\n"
        'record = {"schema_version": 1, "scrutare_version": "2026.10.1",\n'
        ' "status": "posted", "run_dir": str(run), "head_sha": "b" * 40,\n'
        ' "verdict": "approve", "rule": "no_blocking_findings", "panel_status": "complete",\n'
        ' "usage": {"input": 1, "output": 2, "cache_read": 0,\n'
        '           "cache_write": 0, "total": 3},\n'
        ' "accounting_complete": True, "review": {"review_id": 42, "html_url":\n'
        ' "https://github.com/Owner/Repo/pull/17#pullrequestreview-42", "commit_id": "b" * 40,\n'
        ' "body": "reviewed captured head", "state": "APPROVED", "login": "github-actions[bot]"}}\n'
        'record.update(scenario.get("record", {}))\n'
        'raw = (json.dumps(record, indent=2, sort_keys=True) + "\\n").encode()\n'
        'if scenario.get("duplicate"):\n'
        "    raw = raw.rsplit(b'}', 1)[0] + b', \"verdict\": \"approve\"}\\n'\n"
        'raw = scenario.get("stdout", raw.decode()).encode()\n'
        'saved = scenario.get("saved", raw.decode()).encode()\n'
        'result = run / "result.json"\n'
        "result.write_bytes(saved)\n"
        "result.chmod(0o600)\n"
        'if scenario.get("symlink_result"):\n'
        '    target = base.with_suffix(".outside")\n'
        "    target.write_bytes(saved)\n"
        "    result.unlink()\n"
        "    result.symlink_to(target)\n"
        "sys.stdout.buffer.write(raw)\n"
        'sys.stderr.buffer.write(scenario.get("stderr", "").encode())\n'
        'sys.exit(scenario.get("exit", 0))\n',
    )
    executable.chmod(0o755)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    return executable


def review_context(runner, tmp_path, scenario=None, config="scrutare.yaml"):
    env, _ = runner
    env["SCRUTARE_ACTION_CONFIG"] = config
    prepared = prepare(env)
    source = Path(env["GITHUB_WORKSPACE"]) / prepared["checkout-path"] / config
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"models:\n  default:\n    model: trusted-model\n")
    executable = install_docker(tmp_path, env, scenario or {})
    env["SCRUTARE_ACTION_STATE_FILE"] = prepared["state-file"]
    Path(env["GITHUB_OUTPUT"]).write_text("")
    return env, prepared, source, executable


@pytest.mark.parametrize("verdict", ["approve", "changes_requested", "escalated"])
def test_review_publishes_only_valid_cli_success(runner, tmp_path, verdict):
    env, prepared, source, docker = review_context(
        runner,
        tmp_path,
        {"record": {"verdict": verdict}},
        config="nested directory/配置 $(touch hacked).yaml",
    )
    env["GH_TOKEN"] = "synthetic-github-secret"
    env["OPENAI_API_KEY"] = "synthetic-openai-secret"
    env["ANTHROPIC_API_KEY"] = "synthetic-anthropic-secret"
    env["UNRELATED_SECRET"] = "synthetic-unrelated-secret"
    result = invoke(env, "review")
    assert result.returncode == 0, result.stderr
    published = outputs(env)
    assert published["verdict"] == verdict
    assert published["head-sha"] == HEAD_SHA  # Captured CLI SHA, never event head.
    assert set(published) == {"verdict", "head-sha", "run-dir"}
    run = Path(published["run-dir"])
    evidence = Path(prepared["evidence-root"])
    assert run.is_relative_to(evidence / "work/.scrutare/runs")
    assert (evidence / "cli-stdout.bin").read_bytes() == (run / "result.json").read_bytes()
    assert (evidence / "cli-stderr.bin").read_bytes() == b""
    calls = [json.loads(line) for line in docker.with_suffix(".log").read_text().splitlines()]
    assert len(calls) == 1
    args = calls[0]
    assert args[0] == "run"
    assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert args[-5:] == [
        "review",
        "--pr",
        "https://github.com/Owner/Repo/pull/17",
        "--config",
        "/scrutare-config.yaml",
    ]
    assert "ghcr.io/vividynamics/scrutare:2026.10.1" in args
    forwarded = [args[i + 1] for i, value in enumerate(args) if value == "--env"]
    assert set(forwarded) == {"HOME=/tmp", "GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
    assert all("synthetic-" not in arg for arg in args)
    assert docker.with_suffix(".config").read_bytes() == source.read_bytes()
    assert int(docker.with_suffix(".mode").read_text()) == 0o400
    assert stat.S_IMODE((run / "result.json").stat().st_mode) == 0o600
    assert not (source.parent / "hacked").exists()
    mounts = [args[i + 1] for i, value in enumerate(args) if value == "--volume"]
    assert f"{evidence / 'work'}:{evidence / 'work'}:rw" in mounts
    config_mount = next(m for m in mounts if m.endswith(":/scrutare-config.yaml:ro"))
    assert not Path(config_mount.split(":")[0]).is_relative_to(evidence)


@pytest.mark.parametrize("exit_code", [1, 2, 130])
@pytest.mark.parametrize("stdout", ["", '{"verdict":"approve"}'])
def test_review_preserves_cli_failure_without_retry_or_success(runner, tmp_path, exit_code, stdout):
    env, prepared, _, docker = review_context(
        runner,
        tmp_path,
        {
            "exit": exit_code,
            "stdout": stdout,
            "stderr": "delivery uncertain; Run: /unrelated/private\n",
        },
    )
    result = invoke(env, "review")
    assert result.returncode == exit_code
    assert outputs(env) == {}
    evidence = Path(prepared["evidence-root"])
    assert (evidence / "cli-stdout.bin").read_bytes() == stdout.encode()
    assert (
        evidence / "cli-stderr.bin"
    ).read_bytes() == b"delivery uncertain; Run: /unrelated/private\n"
    assert len(docker.with_suffix(".log").read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "scenario",
    [
        {"stdout": ""},
        {"stdout": "{broken"},
        {"stdout": "{}\n{}"},
        {"stdout": "[]"},
        {"record": {"scrutare_version": "2026.10.0"}},
        {"record": {"schema_version": True}},
        {"record": {"status": "failed"}},
        {"record": {"verdict": "approve\nhead-sha=bad"}},
        {"record": {"head_sha": HEAD_SHA + "\nverdict=approve"}},
        {"record": {"run_dir": "/unrelated/private"}},
        {"record": {"run_dir": "../outside"}},
        {"record": {"run_dir": ".scrutare/runs/../outside"}},
        {"saved": "different persisted bytes\n"},
        {"symlink_result": True},
    ],
)
def test_review_refuses_unvalidated_success_records(runner, tmp_path, scenario):
    env, prepared, _, docker = review_context(runner, tmp_path, scenario)
    result = invoke(env, "review")
    assert result.returncode == 1
    assert outputs(env) == {}
    assert (Path(prepared["evidence-root"]) / "cli-stdout.bin").exists()
    assert len(docker.with_suffix(".log").read_text().splitlines()) == 1


@pytest.mark.parametrize("kind", ["file", "parent", "directory"])
def test_review_refuses_symlink_or_nonregular_config(runner, tmp_path, kind):
    env, prepared, source, docker = review_context(runner, tmp_path, config="nested/scrutare.yaml")
    if kind == "file":
        external = tmp_path / "external.yaml"
        external.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(external)
    elif kind == "parent":
        external = tmp_path / "external"
        source.parent.rename(external)
        source.parent.symlink_to(external, target_is_directory=True)
    else:
        source.unlink()
        source.mkdir()
    assert invoke(env, "review").returncode == 1
    assert outputs(env) == {}
    assert not docker.with_suffix(".log").exists()
    assert (Path(prepared["evidence-root"]) / "cli-stdout.bin").read_bytes() == b""


@pytest.mark.parametrize(
    "scenario",
    [
        {"duplicate": True},
        {"record": {"usage": float("nan")}},
    ],
)
def test_review_refuses_ambiguous_or_nonfinite_json(runner, tmp_path, scenario):
    env, _, _, _ = review_context(runner, tmp_path, scenario)
    assert invoke(env, "review").returncode == 1
    assert outputs(env) == {}


def test_review_cancellation_stops_only_owned_container_and_reaps_launcher(runner, tmp_path):
    env, prepared, _, docker = review_context(runner, tmp_path)
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, signal, sys, time\n"
        "base = pathlib.Path(__file__)\n"
        'with base.with_suffix(".log").open("a") as log:\n'
        '    log.write(json.dumps(sys.argv[1:]) + "\\n")\n'
        'if sys.argv[1] == "stop":\n'
        '    os.kill(int(base.with_suffix(".pid").read_text()), signal.SIGTERM)\n'
        "    sys.exit(0)\n"
        'base.with_suffix(".pid").write_text(str(os.getpid()))\n'
        "while True: time.sleep(0.1)\n",
    )
    wrapper = subprocess.Popen(
        [sys.executable, str(ADAPTER), "review"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not docker.with_suffix(".pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = int(docker.with_suffix(".pid").read_text())
        wrapper.send_signal(signal.SIGTERM)
        wrapper.communicate(timeout=10)
        assert wrapper.returncode == 130
        assert outputs(env) == {}
        calls = [json.loads(line) for line in docker.with_suffix(".log").read_text().splitlines()]
        name = Path(prepared["state-file"]).parent.name
        assert calls[-1] == ["stop", "--time", "10", name]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.communicate()
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_prepare_refuses_nonscalar_action_without_a_traceback(runner):
    env, payload = runner
    payload["action"] = []
    update_event(env, payload)
    result = invoke(env, "prepare")
    assert result.returncode == 1
    assert b"Traceback" not in result.stderr
    assert outputs(env) == {}
    assert list(Path(env["RUNNER_TEMP"]).iterdir()) == []
