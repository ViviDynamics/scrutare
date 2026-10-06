"""Release boundaries: reject mismatched artifacts before publication."""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
VERSION = "2026.10.2"


def release_tree(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("check-release.py", "smoke-cli.sh"):
        source = ROOT / "scripts" / name
        if source.exists():
            shutil.copyfile(source, scripts / name)
    package = tmp_path / "src/scrutare"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "2026.10.2"\n')
    return tmp_path


def wheel(root, *, version=VERSION, name=None, metadata=None):
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    path = dist / (name or f"scrutare-{version}-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"scrutare-{version}.dist-info/METADATA",
            metadata or f"Metadata-Version: 2.3\nName: scrutare\nVersion: {version}\n",
        )
    return path


def check(root, tag, path):
    return subprocess.run(
        [sys.executable, str(root / "scripts/check-release.py"), tag, str(path)],
        capture_output=True, text=True, check=False,
    )


def test_matching_tag_source_and_wheel_are_accepted(tmp_path):
    root = release_tree(tmp_path)
    result = check(root, VERSION, wheel(root))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("tag", [
    "v2026.10.2", "2026.01.0", "2026.0.0", "2026.13.0", "2026.10.01",
    "2026.10", "2026.10.2rc1", "2026.10.2\n", "2026.10.2/evil",
])
def test_noncanonical_release_tags_are_refused(tmp_path, tag):
    root = release_tree(tmp_path)
    result = check(root, tag, wheel(root))
    assert result.returncode != 0
    assert "release check failed: tag" in result.stderr.lower()


@pytest.mark.parametrize("changed", ["source", "metadata", "filename", "package", "duplicate"])
def test_disagreeing_release_identity_is_refused(tmp_path, changed):
    root = release_tree(tmp_path)
    metadata = f"Name: scrutare\nVersion: {VERSION}\n"
    name = f"scrutare-{VERSION}-py3-none-any.whl"
    if changed == "source":
        (root / "src/scrutare/__init__.py").write_text('__version__ = "2026.10.3"\n')
    elif changed == "metadata":
        metadata = "Name: scrutare\nVersion: 2026.10.3\n"
    elif changed == "filename":
        name = "scrutare-2026.10.3-py3-none-any.whl"
    elif changed == "package":
        metadata = f"Name: other-package\nVersion: {VERSION}\n"
    elif changed == "duplicate":
        metadata += "Version: 2026.10.3\n"
    result = check(root, VERSION, wheel(root, name=name, metadata=metadata))
    assert result.returncode != 0
    assert "release check failed:" in result.stderr.lower()


def test_corrupt_wheel_is_refused_with_diagnostic(tmp_path):
    root = release_tree(tmp_path)
    path = wheel(root)
    path.write_bytes(b"not a zip file")
    result = check(root, VERSION, path)
    assert result.returncode != 0
    assert "release check failed:" in result.stderr.lower()


@pytest.mark.parametrize("count", [0, 2])
def test_installed_smoke_refuses_missing_or_ambiguous_wheels_before_install(tmp_path, count):
    root = release_tree(tmp_path)
    for number in range(count):
        wheel(root, version=f"2026.10.{number}")
    result = subprocess.run(
        ["bash", str(root / "scripts/smoke-cli.sh")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "exactly one wheel" in result.stderr


def test_release_publication_is_tag_only_and_waits_for_both_test_lanes():
    path = ROOT / ".github/workflows/release.yml"
    assert path.is_file(), "tag release workflow is missing"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"push"}
    assert set(triggers["push"]) == {"tags"}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert set(jobs["checks"]["strategy"]["matrix"]["python"]) == {"3.10", "3.14"}
    publish = jobs["publish"]
    assert publish["needs"] == "checks"
    assert publish["permissions"] == {"contents": "write", "packages": "write"}
    steps = publish["steps"]
    runs = [step.get("run", "") for step in steps]
    release = next(i for i, run in enumerate(runs) if "gh release create" in run)
    push = next(i for i, run in enumerate(runs) if "docker push" in run)
    for gate in ("check-release.py", "smoke-cli.sh", "docker build", "smoke-container.sh"):
        assert any(gate in run for run in runs[:min(release, push)])
    assert sum("uv build --wheel" in run for run in runs) == 1
    assert '"$wheel"' in runs[release]
    assert not any("secrets." in run for run in runs)
    checks = "\n".join(step.get("run", "") for step in jobs["checks"]["steps"])
    for gate in ("pytest", "ruff check", "mypy src", "merge-base --is-ancestor"):
        assert gate in checks


def test_container_uses_selected_wheel_direct_nonroot_entrypoint_and_separate_runtime():
    path = ROOT / "Dockerfile"
    assert path.is_file(), "container recipe is missing"
    instructions = path.read_text().splitlines()
    entry = next(line for line in instructions if line.startswith("ENTRYPOINT "))
    assert json.loads(entry.removeprefix("ENTRYPOINT ")) == ["scrutare"]
    users = [line.split()[1] for line in instructions if line.startswith("USER ")]
    assert users[-1] not in {"root", "0", "0:0"}
    assert "WORKDIR /work" in instructions
    copies = [line for line in instructions if line.startswith("COPY ")]
    assert any("${SCRUTARE_WHEEL}" in line for line in copies)
    assert not any(line in {"COPY . .", "COPY . /app"} for line in copies)
    arguments = [line.split()[1].split("=")[0] for line in instructions if line.startswith("ARG ")]
    assert arguments == ["SCRUTARE_WHEEL"]


def test_container_context_is_allowlisted_to_recipe_and_wheels():
    path = ROOT / ".dockerignore"
    assert path.is_file(), "container context protection is missing"
    patterns = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    assert patterns[0] == "**", "deny unknown local files by default"
    assert set(patterns[1:]) <= {
        "!Dockerfile", "!.dockerignore", "!dist/", "dist/**",
        "!dist/scrutare-*-py3-none-any.whl",
    }
    assert "dist/**" in patterns, "opening dist/ must not admit non-wheel files"
    assert patterns.index("!dist/") < patterns.index("dist/**")
    assert patterns.index("dist/**") < patterns.index("!dist/scrutare-*-py3-none-any.whl")
