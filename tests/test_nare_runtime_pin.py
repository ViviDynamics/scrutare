"""Keep every installed runtime on the selected public release and exact commit."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
RELEASE = "2026.10.32"
COMMIT = "19b4e983e2f54e735649aeb6691ffbc46cf4460a"
PREVIOUS = ("2026.10.12", "96672e1e6d23920fc6cdeebfcad7250c8c36cc5e")


def install_steps(workflow, job):
    steps = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"][job]
    return [step["run"] for step in steps["steps"] if "nare_checkout=" in step.get("run", "")]


def test_ci_installs_selected_nare_release_and_verifies_its_commit():
    [install] = install_steps("ci.yml", "checks")
    assert f"git clone --depth 1 --branch {RELEASE} " in install
    assert COMMIT in install
    assert "--python 3.14 --locked --no-dev" in install
    assert "SCRUTARE_TEST_NARE_EXECUTABLE=" in install


def test_release_workflow_installs_the_same_release():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert text.count(f"git clone --depth 1 --branch {RELEASE} ") == 2
    assert text.count(COMMIT) == 2
    assert not any(value in text for value in PREVIOUS)


def test_action_image_installs_the_same_release():
    text = (ROOT / "Dockerfile").read_text()
    assert f"git clone --depth 1 --branch {RELEASE} " in text
    assert COMMIT in text
    assert f'test "$(/opt/nare/bin/nare --version)" = "{RELEASE}"' in text
    assert f'assert version == "{RELEASE}"' in (ROOT / "scripts/smoke-container.sh").read_text()


def test_documented_installation_verifies_selected_release_commit():
    for name in ("docs/session-fanout.md", "docs/releases.md"):
        guide = (ROOT / name).read_text()
        assert f"nare-{RELEASE}" in guide or f"nare/tree/{RELEASE}" in guide
        assert COMMIT in guide
        assert PREVIOUS[1] not in guide
        assert f"--branch {PREVIOUS[0]} " not in guide
    assert "899e1eb3851dcce4a4ab907f76384c49f4aca2fa" not in (
        ROOT / "docs/session-fanout.md").read_text()
