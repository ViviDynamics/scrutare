"""Release automation against actual git histories and built package identity."""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/auto-release.py"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def history(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Release Test")
    git(tmp_path, "config", "user.email", "release@example.test")
    source = tmp_path / "src/scrutare/__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text('"""Package."""\n\n__version__ = "2026.10.2"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "published")
    git(tmp_path, "tag", "2026.10.2")
    git(tmp_path, "commit", "--allow-empty", "-m", "new feature")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def plan(root, month="2026.10"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "plan", "--month", month],
        cwd=root, capture_output=True, text=True,
    )


def test_green_commit_without_version_bump_gets_next_patch(history):
    result = plan(history)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "version=2026.10.3\ntag_exists=false\n"


def test_month_rollover_starts_at_zero(history):
    assert plan(history, "2026.11").stdout == "version=2026.11.0\ntag_exists=false\n"


def test_first_release_can_use_untagged_authored_version(history):
    git(history, "tag", "-d", "2026.10.2")
    assert plan(history).stdout == "version=2026.10.2\ntag_exists=false\n"


def test_rerun_reuses_annotated_release_tag_on_exact_commit(history):
    git(history, "tag", "-a", "2026.10.3", "-m", "release")
    git(history, "tag", "unrelated-tag")
    assert plan(history).stdout == "version=2026.10.3\ntag_exists=true\n"


def test_unrelated_tag_does_not_suppress_release(history):
    git(history, "tag", "unrelated-tag")
    assert plan(history).stdout == "version=2026.10.3\ntag_exists=false\n"


def test_stray_release_tag_cannot_reset_counter_but_its_name_is_reserved(history):
    git(history, "checkout", "-b", "unmerged", "HEAD~1")
    git(history, "commit", "--allow-empty", "-m", "unmerged")
    git(history, "tag", "2026.10.3")
    git(history, "tag", "2026.10.999")
    git(history, "checkout", "main")
    assert plan(history).stdout == "version=2026.10.4\ntag_exists=false\n"


def test_noncanonical_tags_are_not_releases(history):
    for name in ("v2026.10.9", "2026.10.03", "2026.10.3.1", "2026.13.0"):
        git(history, "tag", name)
    assert plan(history).stdout == "version=2026.10.3\ntag_exists=false\n"


def test_commit_outside_main_history_cannot_be_released(history):
    git(history, "checkout", "-b", "unmerged")
    git(history, "commit", "--allow-empty", "-m", "unmerged")
    result = plan(history)
    assert result.returncode != 0
    assert "main history" in result.stderr
    assert not result.stdout


def test_stamp_changes_only_release_checkout_source_and_not_git_history(history):
    original_head = git(history, "rev-parse", "HEAD")
    original = (history / "src/scrutare/__init__.py").read_text()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stamp", "2026.10.3"],
        cwd=history, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (history / "src/scrutare/__init__.py").read_text() == original.replace(
        '"2026.10.2"', '"2026.10.3"',
    )
    assert git(history, "rev-parse", "HEAD") == original_head
    assert git(history, "status", "--porcelain") == "M src/scrutare/__init__.py"


def test_stamp_updates_only_project_version_in_lockfile(history):
    lock = history / "uv.lock"
    original = ('[[package]]\nname = "scrutare"\nversion = "2026.10.2"\n'
                'source = { editable = "." }\n\n[[package]]\nname = "pyyaml"\nversion = "6.0.3"\n')
    lock.write_text(original)
    subprocess.run([sys.executable, str(SCRIPT), "stamp", "2026.10.3"], cwd=history, check=True)
    assert lock.read_text() == original.replace('version = "2026.10.2"', 'version = "2026.10.3"')


def test_stamp_accepts_actual_uv_dynamic_project_lock_without_version(history):
    lock = history / "uv.lock"
    original = (ROOT / "uv.lock").read_text()
    lock.write_text(original)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stamp", "2026.10.3"],
        cwd=history, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert lock.read_text() == original


@pytest.mark.parametrize("version", ["v2026.10.3", "2026.10.03", "2026.13.0", "2026.10.3\n"])
def test_stamp_rejects_noncanonical_version_without_modification(history, version):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "stamp", version],
        cwd=history, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not git(history, "status", "--porcelain")


def test_stamped_source_matches_wheel_release_validator(history):
    scripts = history / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/check-release.py", scripts / "check-release.py")
    subprocess.run([sys.executable, str(SCRIPT), "stamp", "2026.10.3"], cwd=history, check=True)
    # Use the real packaging validator with the stamped source and matching metadata.
    wheel = history / "scrutare-2026.10.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "scrutare-2026.10.3.dist-info/METADATA", "Name: scrutare\nVersion: 2026.10.3\n",
        )
    subprocess.run(
        [sys.executable, str(scripts / "check-release.py"), "2026.10.3", str(wheel)],
        cwd=history, check=True,
    )


def test_auto_tag_only_accepts_green_same_repository_main_pushes():
    path = ROOT / ".github/workflows/auto-tag.yml"
    assert path.exists(), "automatic tagging workflow missing"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["workflow_run"] == {"workflows": ["CI"], "types": ["completed"]}
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {}
    assert workflow["concurrency"] == {"group": "auto-tag", "cancel-in-progress": False}
    job = workflow["jobs"]["tag"]
    guard = job["if"]
    for requirement in (
        "conclusion == 'success'", "event == 'push'", "head_branch == 'main'",
        "head_repository.full_name == github.repository",
    ):
        assert requirement in guard
    assert job["permissions"] == {"contents": "write", "actions": "write"}
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha || github.sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert 'refs/heads/main' in commands
    assert '--commit "$sha"' in commands
    assert 'gh workflow run release.yml --ref "$version"' in commands
    assert 'git push origin "refs/tags/$version"' in commands
    assert 'gh run list' in commands
    assert 'cancelled' in commands and 'superseded' in commands
    assert 'git commit' not in commands


@pytest.mark.parametrize("existing,appears,dispatch_status,expected_status", [
    (True, True, 0, 0), (False, True, 0, 0),
    (False, False, 0, 1), (False, True, 9, 9),
])
def test_dispatch_skips_existing_runs_recovers_missing_runs_and_exposes_failures(
    history, existing, appears, dispatch_status, expected_status,
):
    workflow = yaml.safe_load((ROOT / ".github/workflows/auto-tag.yml").read_text())
    command = workflow["jobs"]["tag"]["steps"][-1]["run"]
    binary = history / "bin"
    binary.mkdir()
    calls = history / "gh-calls.jsonl"
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
with open(os.environ["CALLS"], "a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["workflow", "run"]:
    sys.exit(int(os.environ["DISPATCH_STATUS"]))
if sys.argv[1:3] == ["run", "list"]:
    print(os.environ["APPEARS"] if "--commit" in sys.argv else os.environ["EXISTING"])
else:
    sys.exit(99)
''')
    gh.chmod(0o755)
    sleep = binary / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", command], cwd=history, text=True, capture_output=True,
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
             "version": "2026.10.3", "GITHUB_STEP_SUMMARY": str(history / "summary"),
             "CALLS": str(calls), "EXISTING": str(existing).lower(),
             "APPEARS": str(appears).lower(), "DISPATCH_STATUS": str(dispatch_status)},
    )
    assert result.returncode == expected_status, result.stderr
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    dispatches = [args for args in invocations if args[:2] == ["workflow", "run"]]
    assert dispatches == ([] if existing else [
        ["workflow", "run", "release.yml", "--ref", "2026.10.3"],
    ])
    if not existing and dispatch_status == 0:
        assert any("--commit" in args and git(history, "rev-parse", "HEAD") in args
                   for args in invocations)


@pytest.mark.parametrize("present,published_version", [
    (False, "2026.10.2"), (True, "2026.10.2"), (True, "2026.10.9"),
])
def test_recovery_uses_published_wheel_bytes_and_rejects_wrong_release(
    history, present, published_version,
):
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    command = next(step["run"] for step in workflow["jobs"]["publish"]["steps"]
                   if "gh release download" in step.get("run", ""))
    scripts = history / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/check-release.py", scripts / "check-release.py")
    target = history / "dist/scrutare-2026.10.2-py3-none-any.whl"
    target.parent.mkdir()
    asset = history / "published/scrutare-2026.10.2-py3-none-any.whl"
    asset.parent.mkdir()
    for path, version, marker in ((target, "2026.10.2", "rebuilt"),
                                  (asset, published_version, "published")):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"scrutare-{version}.dist-info/METADATA",
                             f"Name: scrutare\nVersion: {version}\n")
            archive.writestr("marker", marker)
    rebuilt = target.read_bytes()
    binary = history / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + '''import os, pathlib, shutil, sys
if sys.argv[1:3] == ["release", "view"]:
    if os.environ["PRESENT"] == "false":
        sys.exit(1)
    if "--json" in sys.argv:
        print(pathlib.Path(os.environ["ASSET"]).name)
elif sys.argv[1:3] == ["release", "download"]:
    directory = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
    shutil.copyfile(os.environ["ASSET"], directory / pathlib.Path(os.environ["ASSET"]).name)
else:
    sys.exit(99)
''')
    gh.chmod(0o755)
    uv = binary / "uv"
    uv.write_text(f"#!{sys.executable}\n" + '''import subprocess, sys
sys.exit(subprocess.run([sys.executable, *sys.argv[sys.argv.index("python") + 1:]]).returncode)
''')
    uv.chmod(0o755)
    temporary = history / "temporary"
    temporary.mkdir()
    result = subprocess.run(
        ["bash", "-c", command], cwd=history, text=True, capture_output=True,
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
             "RELEASE_TAG": "2026.10.2", "WHEEL": str(target),
             "RUNNER_TEMP": str(temporary), "PRESENT": str(present).lower(),
             "ASSET": str(asset)},
    )
    assert (result.returncode == 0) == (not present or published_version == "2026.10.2")
    assert target.read_bytes() == (asset.read_bytes() if present else rebuilt)
    assert not list(temporary.iterdir()), "recovery download directory must be removed"
