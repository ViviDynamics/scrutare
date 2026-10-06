"""Release identity and security contract of the public composite Action."""

from pathlib import Path

import yaml

from scrutare import __version__

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/vividynamics/scrutare:2026.10.1"


def metadata():
    path = ROOT / "action.yaml"
    assert path.is_file(), "public composite Action is missing"
    return yaml.safe_load(path.read_text())


def test_candidate_package_and_fixed_runtime_agree():
    assert __version__ == "2026.10.1"
    steps = metadata()["runs"]["steps"]
    assert steps[0]["env"]["SCRUTARE_ACTION_IMAGE"] == IMAGE


def test_action_has_only_config_input_and_validated_success_outputs():
    action = metadata()
    assert set(action["inputs"]) == {"config"}
    assert action["inputs"]["config"]["default"] == "scrutare.yaml"
    assert set(action["outputs"]) == {"verdict", "head-sha", "run-dir"}
    assert action["runs"]["using"] == "composite"
    steps = action["runs"]["steps"]
    assert [step.get("id") for step in steps] == ["prepare", "checkout", "review", "upload"]
    assert all(not step.get("continue-on-error", False) for step in steps)
    for step, command in ((steps[0], "prepare"), (steps[2], "review")):
        assert step["shell"] == "bash"
        assert (
            step["run"]
            == f'python3 "$GITHUB_ACTION_PATH/src/scrutare/interfaces/action.py" {command}'
        )
    assert (
        steps[2]["env"]["SCRUTARE_ACTION_STATE_FILE"] == "${{ steps.prepare.outputs.state-file }}"
    )


def test_checkout_is_pinned_to_trusted_base_without_credentials():
    checkout = metadata()["runs"]["steps"][1]
    assert checkout["uses"] == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    assert checkout["with"] == {
        "repository": "${{ steps.prepare.outputs.repository }}",
        "ref": "${{ steps.prepare.outputs.base-sha }}",
        "path": "${{ steps.prepare.outputs.checkout-path }}",
        "persist-credentials": False,
        "fetch-depth": 1,
        "submodules": False,
        "lfs": False,
    }


def test_uploader_requires_owned_root_and_retains_hidden_private_evidence():
    uploader = metadata()["runs"]["steps"][3]
    assert uploader["uses"] == "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    assert uploader["if"] == "${{ always() && steps.prepare.outputs.evidence-root != '' }}"
    assert uploader["with"] == {
        "name": "${{ steps.prepare.outputs.artifact-name }}",
        "path": "${{ steps.prepare.outputs.evidence-root }}",
        "include-hidden-files": True,
        "if-no-files-found": "error",
        "retention-days": 7,
        "overwrite": False,
    }


def test_pipeline_caller_is_exactly_ten_lines_and_fixed_release():
    path = ROOT / "docs/pipeline.md"
    assert path.is_file(), "pipeline guide is missing"
    blocks = path.read_text().split("```yaml\n")
    caller = blocks[1].split("```", 1)[0]
    assert len(caller.splitlines()) == 10
    assert "ViviDynamics/scrutare@2026.10.1" in caller
    assert "checkout" not in caller
    assert "runs-on: ubuntu-24.04" in caller


def test_hosted_acceptance_is_explicitly_gated_and_uses_released_action():
    path = ROOT / ".github/workflows/action-acceptance.yml"
    assert path.is_file(), "literal hosted acceptance workflow is missing"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"pull_request_target": {"types": ["synchronize"]}}
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    jobs = workflow["jobs"]
    assert set(jobs) == {"success", "failure"}
    for name, job in jobs.items():
        assert f"scrutare-action-acceptance-{name}" in job["if"]
        assert "github.event.pull_request.head.repo.full_name == github.repository" in job["if"]
        assert "github.event.pull_request.head.ref == '14-action-acceptance'" in job["if"]
        assert job["runs-on"] == "ubuntu-24.04"
        (action,) = job["steps"]
        assert action["uses"] == "ViviDynamics/scrutare@2026.10.1"
        assert not action.get("continue-on-error", False)
        assert action["with"]["config"] == f"tests/fixtures/action-acceptance/{name}.yaml"
        assert action["env"]["GH_TOKEN"] == "${{ github.token }}"
        if name == "success":
            assert action["env"]["OPENAI_API_KEY"] == "${{ secrets.OPENAI_API_KEY }}"
        else:
            assert set(action["env"]) == {"GH_TOKEN"}


def test_acceptance_config_focuses_approved_rail_and_invalid_case_stops_early():
    from scrutare.config import parse_config

    root = ROOT / "tests/fixtures/action-acceptance"
    assert (root / "success.yaml").is_file(), "trusted acceptance configuration is missing"
    config = parse_config((root / "success.yaml").read_text())
    assert list(config.personas) == ["senior-dev"]
    assert config.models.default.provider == "openai"
    assert config.models.default.base_url == "https://llm.vividynamics.com/v1"
    assert config.models.default.model == "spark/glm-5.3-flash"
    assert config.budgets.per_persona_tokens == config.budgets.review_max_tokens == 10000
    assert config.github.paths.include == ("tests/fixtures/action-acceptance/target.py",)
    assert config.github.paths.exclude == ()
    assert config.github.post_mode == "comment"
    import pytest

    with pytest.raises(ValueError):
        parse_config((root / "failure.yaml").read_text())
