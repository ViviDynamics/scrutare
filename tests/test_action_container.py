"""Mandatory native Docker proof of the released Action's installed execution.

Only image acquisition, gh and nare's existing vendor factory are substituted.
The image is built from the actual candidate wheel, with no engine overlay.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_action_packaging import IMAGE, metadata
from test_nare_cli_integration import BAD_ANCHOR, CORRECTED, FINDING, text, tool
from test_review_inputs import DOCS, SOURCE

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026.10.1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def run(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, timeout=600, **kwargs)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result


@pytest.fixture(scope="session")
def candidate_image(tmp_path_factory):
    assert shutil.which("docker"), "native Docker is mandatory for Action proof"
    root = tmp_path_factory.mktemp("action-candidate")
    commands = []
    for name in ("Dockerfile", ".dockerignore"):
        shutil.copyfile(ROOT / name, root / name)
    build = ["uv", "build", "--python", sys.executable, "--wheel", "--out-dir", str(root / "dist")]
    result = run(build, cwd=ROOT)
    commands.append(
        {
            "argv": build,
            "stdout": result.stdout.decode(),
            "stderr": result.stderr.decode(),
            "exit": result.returncode,
        }
    )
    (wheel,) = (root / "dist").glob("*.whl")
    assert wheel.name == f"scrutare-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        for path in (ROOT / "src/scrutare").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                assert archive.read(path.relative_to(ROOT / "src").as_posix()) == path.read_bytes()
        package_metadata = archive.read(f"scrutare-{VERSION}.dist-info/METADATA")
        assert f"Version: {VERSION}\n".encode() in package_metadata
    tag = f"scrutare-action-candidate:{digest(wheel)[:24]}"
    build = [
        "docker",
        "build",
        "--build-arg",
        f"SCRUTARE_WHEEL=dist/{wheel.name}",
        "--tag",
        tag,
        str(root),
    ]
    result = run(build)
    commands.append(
        {
            "argv": build,
            "stdout": result.stdout.decode(),
            "stderr": result.stderr.decode(),
            "exit": result.returncode,
        }
    )
    identity = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/opt/scrutare/bin/python",
            tag,
            "-c",
            "import importlib.metadata,json,scrutare; "
            "print(json.dumps({'path':scrutare.__file__,'version':scrutare.__version__,"
            "'metadata':importlib.metadata.version('scrutare')}))",
        ]
    )
    info = json.loads(identity.stdout)
    assert info["path"].startswith("/opt/scrutare/") and "site-packages" in info["path"]
    assert info["version"] == info["metadata"] == VERSION
    nare = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/opt/nare/bin/nare",
            tag,
            "--version",
        ]
    )
    assert nare.stdout == b"2026.10.4\n"
    assert (
        run(["docker", "run", "--rm", "--network", "none", tag, "--version"]).stdout
        == f"scrutare {VERSION}\n".encode()
    )
    image_id = (
        run(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).stdout.decode().strip()
    )
    evidence = {
        "wheel": wheel.name,
        "wheel_sha256": digest(wheel),
        "image": tag,
        "image_id": image_id,
        "identity": info,
        "nare_version": nare.stdout.decode().strip(),
        "dockerfile_sha256": digest(ROOT / "Dockerfile"),
        "source_sha256": {
            p.relative_to(ROOT).as_posix(): digest(p)
            for p in (ROOT / "src/scrutare").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        },
        "commands": commands,
    }
    (root / "candidate.json").write_text(json.dumps(evidence, indent=2))
    destination = os.environ.get("SCRUTARE_TEST_EVIDENCE_DIR")
    if destination:
        target = Path(destination) / "action-candidate"
        target.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(root / "candidate.json", target / "candidate.json")
    return tag, evidence


CASES = [
    ("approval", {"replies": [tool(), text({"findings": []})]}, 0, "approve"),
    ("blocking", {"replies": [text(FINDING)]}, 0, "changes_requested"),
    ("correction", {"replies": [text(BAD_ANCHOR)]}, 0, "changes_requested"),
    ("failed", {"error": "offline initial failure"}, 1, None),
    ("uncertain", {"replies": [text(FINDING)]}, 1, None),
]


@pytest.mark.parametrize("case,initial,exit_code,verdict", CASES, ids=[c[0] for c in CASES])
def test_actual_action_container(tmp_path, candidate_image, case, initial, exit_code, verdict):
    tag, identity = candidate_image
    action = metadata()
    steps = action["runs"]["steps"]
    temporary = tmp_path / "runner temp café"
    workspace = tmp_path / "caller workspace café"
    temporary.mkdir()
    workspace.mkdir()
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "number": 12,
                "action": "synchronize",
                "pull_request": {
                    "number": 12,
                    "base": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
                    "head": {"sha": "c" * 40, "repo": {"full_name": "owner/repo"}},
                },
            }
        )
    )
    output = tmp_path / "output"
    output.touch()
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_TEMP": str(temporary),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_ACTION_PATH": str(ROOT),
        "SCRUTARE_ACTION_CONFIG": "scrutare.yaml",
        "SCRUTARE_ACTION_IMAGE": steps[0]["env"]["SCRUTARE_ACTION_IMAGE"],
    }
    run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", steps[0]["run"]], env=env)
    prepared = dict(line.split("=", 1) for line in output.read_text().splitlines())
    checkout = workspace / prepared["checkout-path"]
    config = (
        "# exact trusted bytes: café\r\npersonas: [senior-dev]\r\n"
        "models: {default: {provider: openai, model: offline-model, "
        "base_url: 'https://offline.invalid/v1'}}\r\n"
        "budgets: {review_max_tokens: 100, per_persona_tokens: 100}\r\n"
    ).encode()
    (checkout / "scrutare.yaml").write_bytes(config)
    # Host acquisition shim: leave all adapter arguments intact; replace only registry
    # acquisition and inject the established offline gh/vendor-factory boundaries.
    boundary = tmp_path / "offline boundary"
    boundary.mkdir()
    binary = boundary / "bin"
    binary.mkdir()
    shutil.copyfile(ROOT / "tests/helpers/nare_offline_worker.py", boundary / "worker.py")
    gh = binary / "gh"
    gh.write_text(
        "#!/opt/scrutare/bin/python\n" + (ROOT / "tests/helpers/installed_gh.py").read_text()
    )
    nare = binary / "nare"
    nare.write_text(
        "#!/opt/nare/bin/python -B\nimport runpy,sys\n"
        "sys.argv=['/offline/worker.py','/opt/nare/bin/nare',"
        "'/offline/spec.json',*sys.argv[1:]]\n"
        "runpy.run_path(sys.argv[0],run_name='__main__')\n"
    )
    for executable in (gh, nare):
        executable.chmod(0o700)
    (boundary / "spec.json").write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "purpose": "review",
                        "attempt": "attempt-0001",
                        "prompt_prefix": "Review the captured",
                        "scenario": initial,
                    },
                    {
                        "purpose": "reanchor",
                        "attempt": "attempt-0002",
                        "prompt_prefix": "Correct only the anchors",
                        "scenario": {"replies": [text(CORRECTED)]},
                    },
                ]
            }
        )
    )
    (boundary / "gh-spec.json").write_text(
        json.dumps(
            {
                "case": case,
                "diff": (SOURCE + DOCS).decode(),
                "files": [
                    {"filename": "src/app.py", "status": "modified"},
                    {"filename": "docs/secret.md", "status": "modified"},
                ],
                "metadata": {
                    "number": 12,
                    "state": "open",
                    "merged": False,
                    "head": {"sha": "a" * 40},
                    "changed_files": 2,
                    "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
                },
            }
        )
    )
    evidence_root = Path(prepared["evidence-root"])
    work = evidence_root / "work"
    host_bin = tmp_path / "host-bin"
    host_bin.mkdir()
    real_docker = shutil.which("docker")
    docker = host_bin / "docker"
    docker.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        "from pathlib import Path\nargs=sys.argv[1:]\n"
        f"assert args[0]=='run' and {IMAGE!r} in args\n"
        f"assert args[args.index('--user')+1]=={f'{os.getuid()}:{os.getgid()}'!r}\n"
        f"Path({str(tmp_path / 'docker-argv.json')!r}).write_text(json.dumps(args))\n"
        f"args[args.index({IMAGE!r})]={tag!r}\n"
        f"extra=['--network','none','--volume',{f'{boundary}:/offline:ro'!r},"
        "'--env','PATH=/offline/bin:/opt/scrutare/bin:/opt/nare/bin:/usr/bin:/bin',"
        "'--env','SCRUTARE_GH_SPEC=/offline/gh-spec.json',"
        f"'--env',{f'SCRUTARE_GH_LOG={work}/gh.jsonl'!r}]\n"
        f"os.execv({real_docker!r},[{real_docker!r},args[0],*extra,*args[1:]])\n"
    )
    docker.chmod(0o700)
    env["PATH"] = f"{host_bin}:{env['PATH']}"
    env["SCRUTARE_ACTION_STATE_FILE"] = prepared["state-file"]
    review = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-e",
            "-o",
            "pipefail",
            "-c",
            steps[2]["run"] + "\nprintf continued > continuation-marker",
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=90,
    )
    try:
        assert review.returncode == exit_code, (
            review.stderr + (evidence_root / "cli-stderr.bin").read_bytes()
        )
        assert (workspace / "continuation-marker").exists() is (exit_code == 0)
        assert (evidence_root / "cli-stdout.bin").read_bytes() == (
            b"" if exit_code else next(work.glob(".scrutare/runs/*/result.json")).read_bytes()
        )
        runs = list(work.glob(".scrutare/runs/*"))
        assert len(runs) == 1
        directory = runs[0]
        assert (directory / "config.yaml").read_bytes() == config
        assert (directory / "diff.patch").read_bytes() == SOURCE + DOCS
        manifest = load(directory / "artifacts.json")
        assert manifest["scrutare_version"] == VERSION
        inventory = [
            {
                "path": p.relative_to(directory).as_posix(),
                "sha256": digest(p),
                "size_bytes": p.stat().st_size,
            }
            for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name not in ("artifacts.json", ".posting.lock")
        ]
        assert manifest["artifacts"] == inventory
        panel = load(directory / "panel.json")
        assert panel["scrutare_version"] == VERSION
        attempts = sorted(directory.glob("sessions/*/attempt-*"))
        assert len(attempts) == (2 if case == "correction" else 1)
        for attempt in attempts:
            assert stat.S_IMODE((attempt / "session.json").stat().st_mode) == 0o600
            assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
            observed = load(attempt / "offline-observations.json")
            session = load(attempt / "session.json")
            terminal = json.loads((attempt / "stdout.jsonl").read_text().splitlines()[-1])
            outcome = load(attempt / "result.json")
            assert observed["version"] == "2026.10.4" and observed["python"].startswith("3.14.")
            assert observed["credential_names"] == observed["guard_violations"] == []
            assert observed["nare_controls"] == {}
            assert len(observed["factory_calls"]) == 1
            assert session["usage"] == terminal["usage"]
            assert session["output"] == terminal["output"]
            assert outcome["usage"]["total"] == (
                0 if case == "failed" else 30 if case == "approval" else 15
            )
            assert session["budget"]["tokens"] == (85 if attempt.name == "attempt-0002" else 100)
        assert panel["ledger"]["usage"]["total"] == sum(
            load(a / "result.json")["usage"]["total"] for a in attempts
        )
        assert panel["ledger"]["accounting_complete"] is (case != "failed")
        assert panel["ledger"]["overshoot_tokens"] == 0
        calls = [json.loads(line) for line in (work / "gh.jsonl").read_text().splitlines()]
        assert all(call["credential_names"] == [] for call in calls)
        posts = [call for call in calls if "POST" in call["args"]]
        emitted = dict(line.split("=", 1) for line in output.read_text().splitlines())
        if case == "failed":
            assert posts == [] and panel["status"] == "failed"
            assert not (directory / "verdict.json").exists()
        else:
            assert len(posts) == 1
            assert posts[0]["stdin"].encode() == (directory / "review-payload.json").read_bytes()
            canonical = load(directory / "verdict.json")
            assert (directory / "verdict.json").read_bytes() == (
                json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode()
            assert isinstance(load(directory / "findings.json"), list)
            assert load(directory / "posting.json")["attempts"] == 1
        if exit_code:
            assert set(emitted) == set(prepared)
            assert not (directory / "result.json").exists()
            if case == "uncertain":
                assert load(directory / "posting.json")["status"] == "unknown"
        else:
            assert emitted["verdict"] == verdict and emitted["head-sha"] == "a" * 40
            assert emitted["run-dir"] == str(directory)
            assert load(directory / "result.json")["scrutare_version"] == VERSION
        # Execute the uploader's exact path/hidden-file contract locally. This is
        # archive readability evidence, not a claim that the hosted uploader ran.
        assert steps[3]["with"]["include-hidden-files"] is True
        archive = tmp_path / "uploaded.zip"
        members = {}
        with zipfile.ZipFile(archive, "x") as bundle:
            for path in sorted(evidence_root.rglob("*")):
                if path.is_file():
                    assert path.stat().st_uid == os.getuid()
                    data = (
                        path.read_bytes()
                    )  # no chmod: private container bytes must be host-readable
                    name = path.relative_to(evidence_root).as_posix()
                    members[name] = hashlib.sha256(data).hexdigest()
                    bundle.writestr(name, data)
        with zipfile.ZipFile(archive) as bundle:
            assert set(bundle.namelist()) == set(members)
            assert all(
                hashlib.sha256(bundle.read(name)).hexdigest() == value
                for name, value in members.items()
            )
            assert any(name.startswith("work/.scrutare/runs/") for name in members)
        assert "cli-stderr.bin" in members and "cli-stdout.bin" in members
        assert all("checkout" not in name and "/home/" not in name for name in members)
        # A successful archive never masks a failed review; any archive failure is
        # fatal because metadata admits no continue-on-error for either step.
        assert not any(step.get("continue-on-error", False) for step in steps)
        assert bool(review.returncode) is (exit_code != 0)
        (tmp_path / "proof.json").write_text(
            json.dumps(
                {
                    "candidate": identity,
                    "case": case,
                    "review_exit": review.returncode,
                    "uploader_archive_exit": 0,
                    "local_review_failed": bool(review.returncode),
                    "archive_sha256": digest(archive),
                    "archive_members": members,
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "config_sha256": hashlib.sha256(config).hexdigest(),
                },
                indent=2,
            )
        )
    finally:
        destination = os.environ.get("SCRUTARE_TEST_EVIDENCE_DIR")
        if destination:
            target = Path(destination) / f"action-container-{case}"
            target.mkdir(parents=True, exist_ok=False)
            for path in tmp_path.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    saved = target / path.relative_to(tmp_path)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, saved)
