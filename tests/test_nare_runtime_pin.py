"""Keep every installed runtime on the release and exact commit in `nare.pin`."""

import re
from pathlib import Path

import yaml
from nare_pin import COMMIT, PIN_FILE, RELEASE, read_pin

ROOT = Path(__file__).parents[1]
# The pin before the current one. scripts/bump-nare.py moves it on every bump.
PREVIOUS = ("2026.11.109", "989636b56ce62375bd67401ab09c970f40c49b5f")
INSTALL = (
    '. ./nare.pin\n'
    'nare_checkout="$RUNNER_TEMP/scrutare-nare-$NARE_RELEASE"\n'
    'git clone --depth 1 --branch "$NARE_RELEASE" https://github.com/ViviDynamics/nare.git'
    ' "$nare_checkout"\n'
    'test "$(git -C "$nare_checkout" rev-parse HEAD)" = "$NARE_COMMIT"\n'
)


def install_steps(workflow, job):
    steps = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"][job]
    return [step["run"] for step in steps["steps"] if "nare_checkout=" in step.get("run", "")]


def test_the_pin_file_is_the_single_well_formed_source():
    assert PIN_FILE == ROOT / "nare.pin"
    assert set(read_pin()) == {"NARE_RELEASE", "NARE_COMMIT"}
    assert re.fullmatch(r"[0-9]{4}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*)", RELEASE)
    assert re.fullmatch(r"[0-9a-f]{40}", COMMIT)
    assert (RELEASE, COMMIT) != PREVIOUS


def test_ci_installs_the_pinned_release_and_verifies_its_commit():
    [install] = install_steps("ci.yml", "checks")
    assert INSTALL in install
    assert "--python 3.14 --locked --no-dev" in install
    assert "SCRUTARE_TEST_NARE_EXECUTABLE=" in install


def test_release_workflow_installs_the_same_release():
    for job in ("checks", "publish"):
        [install] = install_steps("release.yml", job)
        assert INSTALL in install
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert not any(value in text for value in (RELEASE, COMMIT, *PREVIOUS))


def test_workflows_name_no_release_literally():
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        text = workflow.read_text()
        assert RELEASE not in text and COMMIT not in text, workflow.name


def test_action_image_installs_the_same_release():
    text = (ROOT / "Dockerfile").read_text()
    assert "COPY nare.pin /tmp/nare.pin\nRUN . /tmp/nare.pin \\\n" in text
    assert 'git clone --depth 1 --branch "$NARE_RELEASE" ' in text
    assert 'test "$(git -C /tmp/nare rev-parse HEAD)" = "$NARE_COMMIT"' in text
    assert 'test "$(/opt/nare/bin/nare --version)" = "$NARE_RELEASE"' in text
    assert RELEASE not in text and COMMIT not in text
    assert "!nare.pin" in (ROOT / ".dockerignore").read_text().splitlines()
    smoke = (ROOT / "scripts/smoke-container.sh").read_text()
    assert '. "$(dirname "$0")/../nare.pin"' in smoke
    assert '"$image" - "$expected" "$NARE_RELEASE" <<' in smoke
    assert "assert version == sys.argv[2]" in smoke and RELEASE not in smoke


# Tests that run the installed console and check the version it reports. Fixtures that
# record what one release printed (a scripted contract, a captured session) may name it,
# since they stay true after the pin moves.
INSTALLED_TESTS = ("test_installed_*.py", "test_nare_cli_integration.py",
                   "test_action_container.py", "test_rule_a_installed.py")


def test_installed_tests_read_the_expected_release_from_the_pin():
    paths = sorted({path for pattern in INSTALLED_TESTS
                    for path in (ROOT / "tests").glob(pattern)})
    assert len(paths) > len(INSTALLED_TESTS)
    for path in paths:
        assert RELEASE not in path.read_text(), path.name


def test_documented_installation_verifies_selected_release_commit():
    for name in ("docs/session-fanout.md", "docs/releases.md"):
        guide = (ROOT / name).read_text()
        assert f"nare-{RELEASE}" in guide or f"nare/tree/{RELEASE}" in guide
        assert COMMIT in guide
        assert PREVIOUS[1] not in guide
        assert f"--branch {PREVIOUS[0]} " not in guide
    assert "899e1eb3851dcce4a4ab907f76384c49f4aca2fa" not in (
        ROOT / "docs/session-fanout.md").read_text()
