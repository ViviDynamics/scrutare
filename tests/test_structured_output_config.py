"""Opt-in schema-constrained output on model rails: parsing, refusal and canonical bytes."""

import pytest
import yaml

from scrutare.config import ConfigError, ModelRail, parse_config

OPENAI = "models:\n  default: {provider: openai, base_url: http://localhost:8000/v1, model: m"


def test_structured_output_is_off_by_default_and_absent_from_every_rail_record():
    config = parse_config(
        OPENAI + "}\n  overrides:\n    security: {model: other}\n")
    assert config.models.default.structured_output is None
    assert config.models.for_persona("security").structured_output is None
    canonical = config.to_dict()["models"]
    assert canonical == {
        "default": {"provider": "openai", "base_url": "http://localhost:8000/v1", "model": "m"},
        "overrides": {"security": {"provider": "openai", "base_url": "http://localhost:8000/v1",
                                   "model": "other"}},
    }
    assert ModelRail("openai", None, "m").record() == {
        "provider": "openai", "base_url": None, "model": "m"}


def test_explicit_null_is_the_same_as_absent():
    absent = parse_config(OPENAI + "}\n")
    explicit = parse_config(OPENAI + ", structured_output: null}\n")
    assert explicit == absent
    assert explicit.to_dict() == absent.to_dict()


def test_an_openai_rail_opts_in_and_overrides_inherit_or_clear_it():
    config = parse_config(
        OPENAI + ", structured_output: json_schema}\n"
        "  overrides:\n    security: {model: other}\n    devops: {structured_output: null}\n")
    assert config.models.default.structured_output == "json_schema"
    assert config.models.for_persona("security").structured_output == "json_schema"
    assert config.models.for_persona("devops").structured_output is None
    assert config.models.for_persona("junior-dev").structured_output == "json_schema"
    canonical = config.to_dict()["models"]
    assert canonical["default"]["structured_output"] == "json_schema"
    assert canonical["overrides"]["security"]["structured_output"] == "json_schema"
    # A cleared override stays explicit, so the record re-parses to the same settings.
    assert canonical["overrides"]["devops"]["structured_output"] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_an_override_can_opt_in_alone():
    config = parse_config(
        OPENAI + "}\n  overrides:\n    security: {structured_output: json_schema}\n")
    assert config.models.default.structured_output is None
    assert config.models.for_persona("security").structured_output == "json_schema"
    canonical = config.to_dict()["models"]
    assert canonical["default"]["structured_output"] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


@pytest.mark.parametrize(("text", "path"), [
    ("models: {default: {model: m, structured_output: json_schema}}\n",
     "models.default.structured_output: requires provider openai"),
    (OPENAI + ", structured_output: json_schema}\n"
     "  overrides:\n    senior-dev: {provider: anthropic}\n",
     "models.overrides.senior-dev.structured_output: requires provider openai"),
    (OPENAI + ", structured_output: grammar}\n",
     "models.default.structured_output: expected one of json_schema"),
    (OPENAI + ", structured_output: true}\n",
     "models.default.structured_output: expected one of json_schema"),
])
def test_unsupported_backends_and_modes_fail_at_configuration(text, path):
    with pytest.raises(ConfigError, match=path):
        parse_config(text)


def test_programmatic_rails_refuse_an_unsupported_backend():
    with pytest.raises(ConfigError, match="structured_output: requires provider openai"):
        ModelRail("anthropic", None, "m", "json_schema")
    with pytest.raises(ConfigError, match="structured_output: expected one of json_schema"):
        ModelRail("openai", None, "m", "grammar")  # type: ignore[arg-type]
