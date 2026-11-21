"""scripts/bump-nare.py against fake nare releases and a local git remote, offline."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from nare_pin import COMMIT, RELEASE, read_pin

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/bump-nare.py"
SPEC = importlib.util.spec_from_file_location("bump_nare", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bump_nare = importlib.util.module_from_spec(SPEC)
sys.modules["bump_nare"] = bump_nare
SPEC.loader.exec_module(bump_nare)

YEAR, MONTH, PATCH = (int(part) for part in RELEASE.split("."))
NEXT = f"{YEAR}.{MONTH}.{PATCH + 1}"
LATEST = f"{YEAR}.{MONTH}.{PATCH + 2}"
DRAFT = f"{YEAR}.{MONTH}.{PATCH + 3}"
TRACKED = [bump_nare.PIN_FILE, bump_nare.PIN_TEST, *bump_nare.DOCUMENTS]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def remote(tmp_path):
    """A nare-like remote: the pinned tag, an annotated next tag and a latest tag."""
    path = tmp_path / "nare.git"
    git(tmp_path, "init", "--quiet", "-b", "main", str(path))
    git(path, "config", "user.name", "Bump Test")
    git(path, "config", "user.email", "bump@example.test")
    commits = {}
    for tag in (RELEASE, NEXT, LATEST, DRAFT):
        git(path, "commit", "--quiet", "--allow-empty", "-m", tag)
        commits[tag] = git(path, "rev-parse", "HEAD")
        if tag == NEXT:
            git(path, "tag", "-a", tag, "-m", f"nare {tag}")
        else:
            git(path, "tag", tag)
    return path, commits


@pytest.fixture
def tree(tmp_path, remote):
    """The real pin, documents and pin test, with the pinned commit the fake remote's."""
    _, commits = remote
    root = tmp_path / "scrutare"
    for path in TRACKED:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / path).read_text().replace(COMMIT, commits[RELEASE])
        target.write_text(text)
    return root


@pytest.fixture
def releases(tmp_path):
    data = [
        {"tag_name": DRAFT, "body": "unpublished", "draft": True, "prerelease": False},
        {"tag_name": LATEST, "body": "Latest change (#12)\n\nThanks @someone.\n\nSHA-256:\n\n"
         "    0000  nare.whl\n", "draft": False, "prerelease": False},
        {"tag_name": f"{YEAR}.{MONTH}.{PATCH + 4}", "body": "release candidate notes",
         "draft": False, "prerelease": True},
        {"tag_name": NEXT, "body": "Next change (ViviDynamics/nare#11)", "draft": False,
         "prerelease": False},
        {"tag_name": RELEASE, "body": "The pinned release", "draft": False,
         "prerelease": False},
        {"tag_name": "v1", "body": "not a release tag", "draft": False, "prerelease": False},
    ]
    path = tmp_path / "releases.json"
    path.write_text(json.dumps(data))
    return path


def snapshot(root):
    return {path: (root / path).read_bytes() for path in TRACKED}


def run(tree, releases, *args, remote=None):
    command = [sys.executable, str(SCRIPT), "--root", str(tree), "--releases", str(releases),
               "--outputs", str(tree / "outputs"), *args]
    if remote is not None:
        command += ["--remote", str(remote[0])]
    return subprocess.run(command, capture_output=True, text=True)


def outputs(tree):
    path = tree / "outputs"
    lines = path.read_text().splitlines() if path.exists() else []
    return dict(line.split("=", 1) for line in lines)


def test_status_names_the_latest_published_release(tree, releases):
    result = run(tree, releases, "status")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"current={RELEASE}\nlatest={LATEST}\nchanged=true\n"
    assert outputs(tree) == {"current": RELEASE, "latest": LATEST, "changed": "true"}


def test_status_is_unchanged_when_the_pin_is_latest(tree, tmp_path):
    current = tmp_path / "current.json"
    current.write_text(json.dumps([{"tag_name": RELEASE, "body": ""}]))
    result = run(tree, current, "status")
    assert result.stdout.endswith("changed=false\n")


def test_bump_is_a_noop_when_the_pin_is_current(tree, releases, remote):
    before = snapshot(tree)
    result = run(tree, releases, "bump", RELEASE, remote=remote)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"nare pin is already {RELEASE}; nothing to do.\n"
    assert snapshot(tree) == before
    assert outputs(tree) == {"changed": "false", "release": RELEASE}


def test_a_moved_tag_on_the_current_pin_is_refused(tree, releases, remote):
    path, commits = remote
    git(path, "tag", "--force", RELEASE, commits[NEXT])
    result = run(tree, releases, "bump", RELEASE, remote=remote)
    assert result.returncode == 1
    assert f"tag {RELEASE} no longer names the pinned commit" in result.stderr


def test_dry_run_reports_the_range_and_writes_nothing(tree, releases, remote):
    before = snapshot(tree)
    result = run(tree, releases, "bump", "latest", "--dry-run", remote=remote)
    assert result.returncode == 0, result.stderr
    assert snapshot(tree) == before
    _, commits = remote
    out = result.stdout
    assert f"from {RELEASE} (`{commits[RELEASE]}`) to {LATEST} (`{commits[LATEST]}`)" in out
    # Newest first, only the published range: not the pinned release, a draft or an rc.
    assert out.index(f"### [{LATEST}]") < out.index(f"### [{NEXT}]")
    assert "The pinned release" not in out and "unpublished" not in out
    assert "candidate" not in out
    # Checksums drop; nare's references stay nare's, and no account is notified.
    assert "SHA-256" not in out and "0000  nare.whl" not in out
    assert "Latest change (ViviDynamics/nare#12)" in out
    assert "ViviDynamics/nare#11" in out and "nare#nare" not in out
    assert "@someone" not in out and "@​someone" in out
    assert "Not collected" in out
    assert f"\n-NARE_RELEASE={RELEASE}\n" in out and f"\n+NARE_RELEASE={LATEST}\n" in out
    assert f"\n+NARE_COMMIT={commits[LATEST]}\n" in out
    assert outputs(tree) == {"changed": "true", "previous": RELEASE, "release": LATEST,
                             "commit": commits[LATEST], "contract_changed": "false"}


def test_bump_rewrites_the_pin_documents_and_previous_pin(tree, releases, remote):
    _, commits = remote
    report = tree / "body.md"
    result = run(tree, releases, "bump", NEXT, "--report", str(report), remote=remote)
    assert result.returncode == 0, result.stderr
    # The annotated tag is peeled to its commit.
    assert read_pin(tree / "nare.pin") == {"NARE_RELEASE": NEXT, "NARE_COMMIT": commits[NEXT]}
    assert (tree / "nare.pin").read_text().startswith("# The nare release")
    for path in bump_nare.DOCUMENTS:
        text = (tree / path).read_text()
        assert NEXT in text and RELEASE not in text and commits[RELEASE] not in text, path
    assert commits[NEXT] in (tree / "docs/releases.md").read_text()
    # Versions that are not the pin, such as the contract 3 release, stay.
    assert "2026.11.33" in (tree / "docs/session-fanout.md").read_text()
    test = (tree / bump_nare.PIN_TEST).read_text()
    assert f'PREVIOUS = ("{RELEASE}", "{commits[RELEASE]}")\n' in test
    assert report.read_text() == result.stdout
    assert f"### [{NEXT}]" in result.stdout and f"### [{LATEST}]" not in result.stdout
    # The next bump starts from the new pin.
    again = run(tree, releases, "bump", NEXT, remote=remote)
    assert again.stdout == f"nare pin is already {NEXT}; nothing to do.\n"


@pytest.mark.parametrize(("target", "message"), [
    ("2026.11.1", "is older than the pinned"),
    (DRAFT, "is not a published ViviDynamics/nare release"),
    ("1.0", "is not a canonical YYYY.M.PATCH release"),
])
def test_bump_refuses_a_target_that_is_not_a_newer_published_release(
        tree, releases, remote, target, message):
    before = snapshot(tree)
    result = run(tree, releases, "bump", target, remote=remote)
    assert result.returncode == 1 and message in result.stderr
    assert snapshot(tree) == before


def test_bump_refuses_a_release_whose_tag_is_missing(tree, releases, remote):
    path, _ = remote
    git(path, "tag", "-d", LATEST)
    result = run(tree, releases, "bump", LATEST, remote=remote)
    assert result.returncode == 1 and f"tag {LATEST} does not exist" in result.stderr


def test_a_document_that_stops_naming_the_pin_stops_the_bump(tree, releases, remote):
    (tree / "docs/cli.md").write_text("No version here.\n")
    before = snapshot(tree)
    result = run(tree, releases, "bump", LATEST, remote=remote)
    assert result.returncode == 1
    assert f"docs/cli.md does not mention the pinned {RELEASE}" in result.stderr
    assert snapshot(tree) == before


def contract(version, nare, **entries):
    return {"contract": version, "nare": nare, "flags": {
        "--stream": {"takes_value": False, "choices": None},
        "--structured-output": {"takes_value": True, "choices": ["json_schema"]},
    }, "compaction": {"default": {"mode": "elide", "trigger": 0.8}}, **entries}


def write_contracts(tmp_path, old, new):
    paths = tmp_path / "old.json", tmp_path / "new.json"
    for path, value in zip(paths, (old, new)):
        path.write_text(json.dumps(value))
    return ["--old-contract", str(paths[0]), "--new-contract", str(paths[1])]


def test_an_additive_contract_change_is_listed_without_the_label(
        tmp_path, tree, releases, remote):
    old = contract(3, RELEASE, gone={"flag": "--gone"})
    new = contract(3, LATEST, check_command={"default_timeout": 30.0})
    new["flags"]["--check-command"] = {"takes_value": True, "choices": None}
    new["flags"]["--structured-output"]["choices"] = ["json_schema", "json_object"]
    new["compaction"]["default"]["trigger"] = 0.7
    result = run(tree, releases, "bump", LATEST, "--dry-run",
                 *write_contracts(tmp_path, old, new), remote=remote)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "- Flags added: `--check-command`" in out
    assert ('  - `--structured-output`: `{"takes_value": true, "choices": ["json_schema"]}` to '
            '`{"takes_value": true, "choices": ["json_schema", "json_object"]}`') in out
    assert "- Entries added: `check_command`" in out and "- Entries removed: `gone`" in out
    assert "  - `compaction.default.trigger`: `0.8` to `0.7`" in out
    assert "  - `check_command.default_timeout`: `null` to `30.0`" in out
    assert "Contract version" not in out and "nare-contract-change" not in out
    assert outputs(tree)["contract_changed"] == "false"


def test_a_contract_version_change_is_called_out_and_labeled(tmp_path, tree, releases, remote):
    args = write_contracts(tmp_path, contract(3, RELEASE), contract(4, LATEST))
    result = run(tree, releases, "bump", LATEST, "--dry-run", *args, remote=remote)
    assert result.returncode == 0, result.stderr
    first, _ = result.stdout.split("## `nare contract` changes")
    assert "machine contract version from `3` to `4`" in first
    assert "Labeled `nare-contract-change`" in first
    assert "- **Contract version: `3` to `4`.**" in result.stdout
    assert outputs(tree)["contract_changed"] == "true"


def test_identical_contracts_report_no_change(tmp_path, tree, releases, remote):
    args = write_contracts(tmp_path, contract(3, RELEASE), contract(3, LATEST))
    result = run(tree, releases, "bump", LATEST, "--dry-run", *args, remote=remote)
    assert "None. Flags, defaults and every other entry are unchanged." in result.stdout


def test_one_contract_alone_is_refused(tmp_path, tree, releases, remote):
    path = tmp_path / "old.json"
    path.write_text("{}")
    result = run(tree, releases, "bump", LATEST, "--old-contract", str(path), remote=remote)
    assert result.returncode == 1 and "pass both" in result.stderr


def test_the_script_and_the_tests_read_the_pin_file_alike():
    pin = bump_nare.parse_pin((ROOT / "nare.pin").read_text())
    assert (pin.release, pin.commit) == (RELEASE, COMMIT)
    with pytest.raises(bump_nare.BumpError):
        bump_nare.parse_pin("NARE_RELEASE=2026.11.1\n")


def test_the_bump_workflow_opens_one_pull_request_and_never_merges():
    path = ROOT / ".github/workflows/nare-bump.yml"
    text = path.read_text()
    workflow = yaml.safe_load(text)
    triggers = workflow[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"} and triggers["schedule"]
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["bump"]
    assert job["permissions"] == {"contents": "write", "pull-requests": "write",
                                  "actions": "write"}
    assert workflow["env"]["BRANCH"] == "automation/nare-bump"
    assert "secrets." not in text and "gh pr merge" not in text and "--auto" not in text
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "scripts/bump-nare.py" in runs and '--head "$BRANCH"' in runs
    assert "gh workflow run ci.yml --ref \"$BRANCH\"" in runs
    assert "gh workflow run disclosure-guard.yml" in runs
    for name in ("ci.yml", "disclosure-guard.yml"):
        dispatched = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        assert "workflow_dispatch" in dispatched[True], name


def test_strict_types_cover_the_bump_script():
    for path in (".agents/test-commands.md", ".github/workflows/ci.yml",
                 ".github/workflows/release.yml"):
        assert "scripts/auto-release.py scripts/bump-nare.py" in (ROOT / path).read_text()
