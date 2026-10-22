"""Opt-in per-request output token cap on model rails: parsing, refusal and canonical bytes."""

from typing import Any, cast

import pytest
import yaml

from scrutare.config import ConfigError, ModelRail, parse_config

OPENAI = "models:\n  default: {provider: openai, base_url: http://localhost:8000/v1, model: m"


def test_the_cap_is_off_by_default_and_absent_from_every_rail_record():
    config = parse_config(OPENAI + "}\n  overrides:\n    security: {model: other}\n")
    assert config.models.default.max_output_tokens is None
    assert config.models.for_persona("security").max_output_tokens is None
    assert not config.models.caps_output
    assert config.to_dict()["models"] == {
        "default": {"provider": "openai", "base_url": "http://localhost:8000/v1", "model": "m"},
        "overrides": {"security": {"provider": "openai", "base_url": "http://localhost:8000/v1",
                                   "model": "other"}},
    }
    assert ModelRail("openai", None, "m").record() == {
        "provider": "openai", "base_url": None, "model": "m"}


def test_explicit_null_is_the_same_as_absent():
    absent = parse_config(OPENAI + "}\n")
    explicit = parse_config(OPENAI + ", max_output_tokens: null}\n")
    assert explicit == absent
    assert explicit.to_dict() == absent.to_dict()


def test_a_rail_opts_in_and_overrides_inherit_or_clear_it():
    config = parse_config(
        OPENAI + ", max_output_tokens: 12000}\n"
        "  overrides:\n    security: {model: other}\n    devops: {max_output_tokens: null}\n"
        "    senior-dev: {max_output_tokens: 9000}\n")
    assert config.models.default.max_output_tokens == 12000
    assert config.models.for_persona("security").max_output_tokens == 12000
    assert config.models.for_persona("devops").max_output_tokens is None
    assert config.models.for_persona("senior-dev").max_output_tokens == 9000
    assert config.models.for_persona("junior-dev").max_output_tokens == 12000
    canonical = config.to_dict()["models"]
    assert canonical["default"]["max_output_tokens"] == 12000
    assert canonical["overrides"]["security"]["max_output_tokens"] == 12000
    # A cleared override stays explicit, so the record re-parses to the same settings.
    assert canonical["overrides"]["devops"]["max_output_tokens"] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_an_override_can_opt_in_alone_and_the_other_opt_in_stays_absent():
    config = parse_config(OPENAI + "}\n  overrides:\n    security: {max_output_tokens: 500}\n")
    assert config.models.default.max_output_tokens is None
    assert config.models.for_persona("security").max_output_tokens == 500
    canonical = config.to_dict()["models"]
    assert canonical["default"]["max_output_tokens"] is None
    assert "structured_output" not in canonical["default"]
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_both_opt_ins_record_together():
    config = parse_config(OPENAI + ", structured_output: json_schema, max_output_tokens: 7}\n")
    assert config.to_dict()["models"]["default"] == {
        "provider": "openai", "base_url": "http://localhost:8000/v1", "model": "m",
        "structured_output": "json_schema", "max_output_tokens": 7}
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_the_cap_applies_to_an_anthropic_rail_too():
    config = parse_config("models: {default: {model: m, max_output_tokens: 4096}}\n")
    assert config.models.default.max_output_tokens == 4096


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", "'12000'", "[1]"])
def test_a_cap_that_is_not_a_positive_integer_fails_at_configuration(value):
    with pytest.raises(ConfigError, match="models.default.max_output_tokens: expected a "
                                          "positive integer or null"):
        parse_config(OPENAI + f", max_output_tokens: {value}}}\n")


def test_an_invalid_override_cap_names_its_path():
    with pytest.raises(ConfigError,
                       match="models.overrides.security.max_output_tokens: expected a positive"):
        parse_config(OPENAI + "}\n  overrides:\n    security: {max_output_tokens: 0}\n")


@pytest.mark.parametrize("value", [0, -3, True, 2.0, "5"])
def test_programmatic_rails_refuse_an_invalid_cap(value):
    with pytest.raises(ConfigError, match="max_output_tokens: expected a positive integer"):
        ModelRail("openai", None, "m", max_output_tokens=cast(Any, value))
