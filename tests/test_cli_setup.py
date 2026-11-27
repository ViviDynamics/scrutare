"""First-run setup stays local and preserves repository configuration."""

import shlex

import yaml

from scrutare.interfaces.cli import main

URL = "https://github.com/owner/repo/pull/12"


def test_setup_verifies_new_and_existing_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    nare = tmp_path / "nare"
    nare.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    args = ["setup", "--pr", URL]
    assert main([*args, "--provider", "openai", "--model", "selected"]) == 0
    config = tmp_path / "scrutare.yaml"
    assert yaml.safe_load(config.read_bytes())["models"]["default"] == {
        "provider": "openai", "model": "selected",
    }
    command = shlex.split(capsys.readouterr().out.strip())
    assert command[1:] == [
        "-m", "scrutare", "review", "--pr", URL, "--config", str(config),
        "--nare-executable", str(nare),
    ]
    original = config.read_bytes() + b"# user preferences\n"
    config.write_bytes(original)
    assert main(args) == 0
    assert config.read_bytes() == original
    assert not (tmp_path / ".scrutare").exists()


def test_missing_model_or_invalid_existing_config_is_diagnosed_privately(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "scrutare.yaml"
    args = ["setup", "--pr", URL]
    assert main(args) != 0
    assert not config.exists()
    assert "--model" in capsys.readouterr().err
    config.write_bytes(b"credential-shaped-private-value: [")
    assert main(args) != 0
    captured = capsys.readouterr()
    assert not captured.out
    assert "credential-shaped-private-value" not in captured.err
    assert config.read_bytes() == b"credential-shaped-private-value: ["
