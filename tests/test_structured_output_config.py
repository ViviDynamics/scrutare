"""Opt-in schema-constrained output on model rails: parsing, refusal and canonical bytes."""

from typing import Any, cast

import pytest
import yaml

from scrutare.config import ConfigError, ModelRail, parse_config

EXPECTED = "expected one of json_schema, json_object, tool_call or null"
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
    (OPENAI + ", structured_output: grammar}\n",
     "models.default.structured_output: " + EXPECTED),
    (OPENAI + ", structured_output: true}\n",
     "models.default.structured_output: " + EXPECTED),
])
def test_unsupported_modes_fail_at_configuration(text, path):
    with pytest.raises(ConfigError, match=path):
        parse_config(text)


def test_programmatic_rails_refuse_an_unsupported_mode():
    with pytest.raises(ConfigError, match="structured_output: " + EXPECTED):
        ModelRail("openai", None, "m", cast(Any, "grammar"))


def test_the_anthropic_rail_accepts_the_constraint_and_overrides_inherit_it():
    # nare 2026.11.94 constrains answers on the anthropic rail too. Whether the installed
    # runtime can do it is decided at inspection, not at configuration load.
    config = parse_config("models: {default: {model: m, structured_output: json_schema}}\n")
    assert config.models.default.provider == "anthropic"
    assert config.models.default.structured_output == "json_schema"
    assert ModelRail("anthropic", None, "m", "json_schema").structured_output == "json_schema"
    mixed = parse_config(OPENAI + ", structured_output: json_schema}\n"
                         "  overrides:\n    senior-dev: {provider: anthropic}\n")
    override = mixed.models.for_persona("senior-dev")
    assert (override.provider, override.structured_output) == ("anthropic", "json_schema")
    assert parse_config(yaml.safe_dump(mixed.to_dict())) == mixed


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_an_openai_rail_accepts_each_answer_encoding_and_overrides_inherit_or_clear_it(mode):
    # nare 2026.11.107 offers these two encodings on its openai rail only.
    config = parse_config(
        OPENAI + f", structured_output: {mode}}}\n"
        "  overrides:\n    security: {model: other}\n    devops: {structured_output: null}\n"
        "    junior-dev: {structured_output: json_schema}\n")
    assert config.models.default.structured_output == mode
    assert config.models.for_persona("security").structured_output == mode
    assert config.models.for_persona("devops").structured_output is None
    assert config.models.for_persona("junior-dev").structured_output == "json_schema"
    canonical = config.to_dict()["models"]
    assert canonical["default"]["structured_output"] == mode
    assert canonical["overrides"]["security"]["structured_output"] == mode
    assert canonical["overrides"]["devops"]["structured_output"] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config
    assert ModelRail("openai", None, "m", mode).structured_output == mode


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_the_anthropic_rail_refuses_the_openai_only_encodings_at_load(mode):
    with pytest.raises(ConfigError, match=(
            f"^models.default.structured_output: {mode} requires provider openai$")):
        parse_config(f"models: {{default: {{model: m, structured_output: {mode}}}}}\n")
    with pytest.raises(ConfigError, match=(
            f"^models.overrides.security.structured_output: {mode} requires provider openai$")):
        parse_config(OPENAI + "}\n  overrides:\n"
                     f"    security: {{provider: anthropic, structured_output: {mode}}}\n")
    with pytest.raises(ConfigError, match=f"structured_output: {mode} requires provider openai"):
        ModelRail("anthropic", None, "m", cast(Any, mode))


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_an_anthropic_override_cannot_inherit_an_openai_only_encoding(mode):
    text = (OPENAI + f", structured_output: {mode}}}\n"
            "  overrides:\n    senior-dev: {provider: anthropic")
    with pytest.raises(ConfigError, match=(
            f"^models.overrides.senior-dev.structured_output: {mode} requires provider openai "
            r"\(inherited from models.default; set it to null to clear it\)$")):
        parse_config(text + "}\n")
    cleared = parse_config(text + ", structured_output: null}\n")
    assert cleared.models.for_persona("senior-dev").structured_output is None
    assert cleared.models.default.structured_output == mode
    # An anthropic override may still choose the encoding both rails offer.
    chosen = parse_config(text + ", structured_output: json_schema}\n")
    assert chosen.models.for_persona("senior-dev").structured_output == "json_schema"
