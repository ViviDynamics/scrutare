"""Opt-in nare rail options: parsing, inheritance, refusal and canonical bytes."""

import math

import pytest
import yaml

from scrutare.config import (
    EFFORT_LEVELS,
    REQUEST_RETRIES_MAX,
    REQUEST_RETRY_SECONDS,
    ConfigError,
    ModelRail,
    parse_config,
)

OPENAI = "models:\n  default: {provider: openai, model: m"
ANTHROPIC = "models:\n  default: {provider: anthropic, model: m"
BARE = {"provider": "openai", "base_url": None, "model": "m"}
FIELDS = ("request_retries", "request_retry_seconds", "cache_breakpoints", "effort")


def test_every_option_is_off_by_default_and_absent_from_the_canonical_record():
    config = parse_config(OPENAI + "}\n  overrides:\n    security: {model: other}\n")
    for rail in (config.models.default, config.models.for_persona("security")):
        assert all(getattr(rail, field) is None for field in FIELDS)
    assert config.to_dict()["models"] == {
        "default": BARE, "overrides": {"security": {**BARE, "model": "other"}}}
    assert ModelRail("openai", None, "m").record() == BARE


@pytest.mark.parametrize("field", FIELDS)
def test_explicit_null_is_the_same_as_absent(field):
    absent = parse_config(OPENAI + "}\n")
    explicit = parse_config(OPENAI + f", {field}: null}}\n")
    assert explicit == absent and explicit.to_dict() == absent.to_dict()


@pytest.mark.parametrize(("setting", "field", "value"), [
    ("request_retries: 0", "request_retries", 0),
    ("request_retries: 3", "request_retries", 3),
    ("request_retries: 2, request_retry_seconds: 45", "request_retry_seconds", 45),
    ("request_retries: 2, request_retry_seconds: 7.5", "request_retry_seconds", 7.5),
    ("cache_breakpoints: true", "cache_breakpoints", True),
    ("cache_breakpoints: false", "cache_breakpoints", False),
    ("effort: xhigh", "effort", "xhigh"),
])
def test_a_set_option_inherits_and_an_override_clears_it(setting, field, value):
    cleared = ("request_retries: null, request_retry_seconds: null"
               if field == "request_retry_seconds" else f"{field}: null")
    config = parse_config(
        OPENAI + f", {setting}}}\n  overrides:\n    security: {{model: other}}\n"
        f"    devops: {{{cleared}}}\n")
    assert getattr(config.models.default, field) == value
    assert getattr(config.models.for_persona("security"), field) == value
    assert getattr(config.models.for_persona("devops"), field) is None
    canonical = config.to_dict()["models"]
    assert canonical["default"][field] == value
    assert canonical["overrides"]["security"][field] == value
    # A cleared override stays explicit, so the record parses back to the same settings.
    assert canonical["overrides"]["devops"][field] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config
    # Options no rail sets stay out of every record.
    others = [name for name in FIELDS if name != field
              and getattr(config.models.default, name) is None]
    assert not any(name in canonical["default"] for name in others)


def test_an_override_can_opt_in_alone():
    config = parse_config(OPENAI + "}\n  overrides:\n    security: {effort: low}\n")
    assert config.models.default.effort is None
    assert config.models.for_persona("security").effort == "low"
    assert config.to_dict()["models"]["default"]["effort"] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


@pytest.mark.parametrize(("setting", "message"), [
    ("request_retries: -1", "request_retries: expected a nonnegative integer or null"),
    ("request_retries: true", "request_retries: expected a nonnegative integer or null"),
    ("request_retries: 1.5", "request_retries: expected a nonnegative integer or null"),
    ('request_retries: "2"', "request_retries: expected a nonnegative integer or null"),
    ("request_retries: 2, request_retry_seconds: 0",
     "request_retry_seconds: expected a positive finite number or null"),
    ("request_retries: 2, request_retry_seconds: -3",
     "request_retry_seconds: expected a positive finite number or null"),
    ("request_retries: 2, request_retry_seconds: true",
     "request_retry_seconds: expected a positive finite number or null"),
    ("request_retries: 2, request_retry_seconds: .inf",
     "request_retry_seconds: expected a positive finite number or null"),
    ("request_retry_seconds: 30", "request_retry_seconds: requires request_retries above 0"),
    ("request_retries: 0, request_retry_seconds: 30",
     "request_retry_seconds: requires request_retries above 0"),
    ("cache_breakpoints: 1", "cache_breakpoints: expected a boolean or null"),
    ('cache_breakpoints: "yes"', "cache_breakpoints: expected a boolean or null"),
    ("effort: extreme", "effort: expected one of low, medium, high, xhigh, max or null"),
    ("effort: 3", "effort: expected one of low, medium, high, xhigh, max or null"),
    ("retries: 2", "models.default"),
])
def test_invalid_values_fail_at_configuration(setting, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(OPENAI + f", {setting}}}\n")


def test_cache_breakpoints_are_an_openai_rail_option():
    with pytest.raises(ConfigError, match="cache_breakpoints: requires provider openai"):
        parse_config(ANTHROPIC + ", cache_breakpoints: true}\n")
    with pytest.raises(ConfigError, match="overrides.security.cache_breakpoints: requires "
                                          "provider openai"):
        parse_config(OPENAI + ", cache_breakpoints: true}\n  overrides:\n"
                     "    security: {provider: anthropic}\n")
    with pytest.raises(ConfigError, match="cache_breakpoints: requires provider openai"):
        ModelRail("anthropic", None, "m", cache_breakpoints=True)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_retries_effort_and_structured_output_work_on_both_rails(provider):
    config = parse_config(
        f"models:\n  default: {{provider: {provider}, model: m, request_retries: 2, "
        "effort: high, structured_output: json_schema}\n")
    rail = config.models.default
    assert (rail.request_retries, rail.effort, rail.structured_output) == (
        2, "high", "json_schema")
    assert config.to_dict()["models"]["default"] == {
        "provider": provider, "base_url": None, "model": "m",
        "structured_output": "json_schema", "request_retries": 2, "effort": "high"}


def test_programmatic_rails_validate_like_configuration():
    assert ModelRail("anthropic", None, "m", "json_schema").structured_output == "json_schema"
    for fields, message in (
            ({"request_retries": -1}, "request_retries"),
            ({"request_retry_seconds": 5}, "requires request_retries above 0"),
            ({"request_retries": 1, "request_retry_seconds": math.nan}, "positive finite"),
            ({"effort": "extreme"}, "effort"),
            ({"cache_breakpoints": "true"}, "cache_breakpoints")):
        with pytest.raises(ConfigError, match=message):
            ModelRail("openai", None, "m", **fields)


def test_the_documented_defaults():
    assert EFFORT_LEVELS == ("low", "medium", "high", "xhigh", "max")
    assert REQUEST_RETRY_SECONDS == 60


@pytest.mark.parametrize(("off", "value"), [("0", 0), ("null", None)])
def test_an_override_that_turns_resends_off_drops_the_inherited_wait_bound(off, value):
    config = parse_config(
        OPENAI + ", request_retries: 2, request_retry_seconds: 45}\n  overrides:\n"
        f"    security: {{request_retries: {off}}}\n    devops: {{model: other}}\n")
    rail = config.models.for_persona("security")
    assert (rail.request_retries, rail.request_retry_seconds) == (value, None)
    # A rail that still resends keeps the bound it inherited.
    assert config.models.for_persona("devops").request_retry_seconds == 45
    canonical = config.to_dict()["models"]["overrides"]["security"]
    assert (canonical["request_retries"], canonical["request_retry_seconds"]) == (value, None)
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_a_wait_bound_set_on_an_override_without_resends_is_still_refused():
    with pytest.raises(ConfigError, match="overrides.security.request_retry_seconds: requires "
                                          "request_retries above 0$"):
        parse_config(OPENAI + ", request_retries: 2, request_retry_seconds: 45}\n  overrides:\n"
                     "    security: {request_retries: 0, request_retry_seconds: 30}\n")


HINT = r" \(inherited from models\.default; set it to null to clear it\)$"


@pytest.mark.parametrize("field", ["cache_breakpoints: true", "cache_breakpoints: false",
                                   "proxy_num_retries: 0"])
def test_an_inherited_openai_only_option_says_how_to_clear_it(field):
    name = field.split(":")[0]
    with pytest.raises(ConfigError, match=f"overrides.security.{name}: requires provider "
                                          "openai" + HINT):
        parse_config(OPENAI + f", {field}}}\n  overrides:\n"
                     "    security: {provider: anthropic}\n")
    # A value the override sets itself is its own mistake, with no hint.
    with pytest.raises(ConfigError, match=f"overrides.security.{name}: requires provider "
                                          "openai$"):
        parse_config(OPENAI + "}\n  overrides:\n"
                     f"    security: {{provider: anthropic, {field}}}\n")
    cleared = parse_config(OPENAI + f", {field}}}\n  overrides:\n"
                           f"    security: {{provider: anthropic, {name}: null}}\n")
    assert getattr(cleared.models.for_persona("security"), name) is None


def test_request_retries_are_capped():
    assert REQUEST_RETRIES_MAX == 10
    assert parse_config(OPENAI + ", request_retries: 10}\n").models.default.request_retries == 10
    message = "request_retries: expected at most 10 resends of one request$"
    with pytest.raises(ConfigError, match="models.default." + message):
        parse_config(OPENAI + ", request_retries: 11}\n")
    with pytest.raises(ConfigError, match="models.overrides.security." + message):
        parse_config(OPENAI + "}\n  overrides:\n    security: {request_retries: 1000000}\n")
    with pytest.raises(ConfigError, match=message):
        ModelRail("openai", None, "m", request_retries=11)
