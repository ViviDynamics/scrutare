"""Opt-in capacity queueing: parsing, inheritance, refusal and canonical bytes."""

import math

import pytest
import yaml

from scrutare.config import (
    CAPACITY_REFUSALS,
    CAPACITY_REFUSALS_MAX,
    CAPACITY_WAIT_SECONDS,
    CAPACITY_WAIT_SECONDS_MAX,
    RAIL_OPTIONS,
    ConfigError,
    ModelRail,
    parse_config,
)

OPENAI = "models:\n  default: {provider: openai, model: m"
ANTHROPIC = "models:\n  default: {provider: anthropic, model: m"
BARE = {"provider": "openai", "base_url": None, "model": "m"}
FIELDS = ("capacity_queue", "capacity_wait_seconds", "capacity_refusals")
HINT = r" \(inherited from models\.default; set it to null to clear it\)$"


def test_the_queue_is_off_by_default_and_absent_from_the_canonical_record():
    config = parse_config(OPENAI + "}\n  overrides:\n    security: {model: other}\n")
    for rail in (config.models.default, config.models.for_persona("security")):
        assert all(getattr(rail, field) is None for field in FIELDS)
    assert config.to_dict()["models"] == {
        "default": BARE, "overrides": {"security": {**BARE, "model": "other"}}}
    assert ModelRail("openai", None, "m").record() == BARE
    # New fields are recorded after every earlier one, so earlier records keep their order.
    assert RAIL_OPTIONS[-3:] == FIELDS


@pytest.mark.parametrize("field", FIELDS)
def test_explicit_null_is_the_same_as_absent(field):
    absent = parse_config(OPENAI + "}\n")
    explicit = parse_config(OPENAI + f", {field}: null}}\n")
    assert explicit == absent and explicit.to_dict() == absent.to_dict()


@pytest.mark.parametrize(("setting", "expected"), [
    ("capacity_queue: true", (True, None, None)),
    ("capacity_queue: false", (False, None, None)),
    ("capacity_queue: true, capacity_wait_seconds: 900", (True, 900, None)),
    ("capacity_queue: true, capacity_wait_seconds: 7.5", (True, 7.5, None)),
    ("capacity_queue: true, capacity_refusals: 3", (True, None, 3)),
    ("capacity_queue: true, capacity_wait_seconds: 3600, capacity_refusals: 1000",
     (True, 3600, 1000)),
])
def test_a_set_queue_inherits_and_round_trips(setting, expected):
    config = parse_config(
        OPENAI + f", {setting}}}\n  overrides:\n    security: {{model: other}}\n")
    for rail in (config.models.default, config.models.for_persona("security")):
        assert tuple(getattr(rail, field) for field in FIELDS) == expected
    canonical = config.to_dict()["models"]
    recorded = {field: value for field, value in zip(FIELDS, expected) if value is not None}
    assert canonical["default"] == {**BARE, **recorded}
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


@pytest.mark.parametrize("off", ["false", "null"])
def test_an_override_that_turns_the_queue_off_drops_the_inherited_limits(off):
    config = parse_config(
        OPENAI + ", capacity_queue: true, capacity_wait_seconds: 900, capacity_refusals: 5}\n"
        f"  overrides:\n    security: {{capacity_queue: {off}}}\n    devops: {{model: other}}\n")
    rail = config.models.for_persona("security")
    assert rail.capacity_queue is (False if off == "false" else None)
    assert (rail.capacity_wait_seconds, rail.capacity_refusals) == (None, None)
    kept = config.models.for_persona("devops")
    assert (kept.capacity_queue, kept.capacity_wait_seconds, kept.capacity_refusals) == (
        True, 900, 5)
    canonical = config.to_dict()["models"]["overrides"]["security"]
    assert (canonical["capacity_wait_seconds"], canonical["capacity_refusals"]) == (None, None)
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


def test_an_override_can_opt_in_alone_and_set_its_own_limits():
    config = parse_config(
        OPENAI + "}\n  overrides:\n"
        "    security: {capacity_queue: true, capacity_refusals: 7}\n")
    assert config.models.default.capacity_queue is None
    rail = config.models.for_persona("security")
    assert (rail.capacity_queue, rail.capacity_refusals) == (True, 7)
    assert config.to_dict()["models"]["default"]["capacity_queue"] is None
    assert parse_config(yaml.safe_dump(config.to_dict())) == config


@pytest.mark.parametrize(("setting", "message"), [
    ("capacity_queue: 1", "capacity_queue: expected a boolean or null"),
    ('capacity_queue: "yes"', "capacity_queue: expected a boolean or null"),
    ("capacity_wait_seconds: 60", "capacity_wait_seconds: requires capacity_queue: true"),
    ("capacity_queue: false, capacity_wait_seconds: 60",
     "capacity_wait_seconds: requires capacity_queue: true"),
    ("capacity_refusals: 5", "capacity_refusals: requires capacity_queue: true"),
    ("capacity_queue: false, capacity_refusals: 5",
     "capacity_refusals: requires capacity_queue: true"),
    ("capacity_queue: true, capacity_wait_seconds: 0",
     "capacity_wait_seconds: expected a positive finite number or null"),
    ("capacity_queue: true, capacity_wait_seconds: -1",
     "capacity_wait_seconds: expected a positive finite number or null"),
    ("capacity_queue: true, capacity_wait_seconds: true",
     "capacity_wait_seconds: expected a positive finite number or null"),
    ("capacity_queue: true, capacity_wait_seconds: .inf",
     "capacity_wait_seconds: expected a positive finite number or null"),
    ("capacity_queue: true, capacity_wait_seconds: 3601",
     "capacity_wait_seconds: expected at most 3600 seconds"),
    ("capacity_queue: true, capacity_refusals: 0",
     "capacity_refusals: expected a positive integer or null"),
    ("capacity_queue: true, capacity_refusals: 2.5",
     "capacity_refusals: expected a positive integer or null"),
    ("capacity_queue: true, capacity_refusals: true",
     "capacity_refusals: expected a positive integer or null"),
    ("capacity_queue: true, capacity_refusals: 1001",
     "capacity_refusals: expected at most 1000 queued refusals"),
    ("capacity_adaptive: true", "capacity_adaptive: unknown field"),
])
def test_invalid_values_fail_at_configuration(setting, message):
    with pytest.raises(ConfigError, match="models.default." + message.split(": ")[0]) as error:
        parse_config(OPENAI + f", {setting}}}\n")
    assert message in str(error.value)


def test_the_queue_is_an_openai_rail_option():
    # nare queues only where a transport declares a capacity marker: the openai rail.
    with pytest.raises(ConfigError, match="capacity_queue: requires provider openai$"):
        parse_config(ANTHROPIC + ", capacity_queue: true}\n")
    with pytest.raises(ConfigError, match="capacity_queue: requires provider openai$"):
        ModelRail("anthropic", None, "m", capacity_queue=True)
    with pytest.raises(ConfigError, match="overrides.security.capacity_queue: requires "
                                          "provider openai" + HINT):
        parse_config(OPENAI + ", capacity_queue: true}\n  overrides:\n"
                     "    security: {provider: anthropic}\n")
    cleared = parse_config(OPENAI + ", capacity_queue: true, capacity_refusals: 5}\n"
                           "  overrides:\n"
                           "    security: {provider: anthropic, capacity_queue: null}\n")
    rail = cleared.models.for_persona("security")
    assert (rail.capacity_queue, rail.capacity_refusals) == (None, None)


def test_programmatic_rails_validate_like_configuration():
    rail = ModelRail("openai", None, "m", capacity_queue=True, capacity_wait_seconds=30,
                     capacity_refusals=2)
    assert rail.record() == {**BARE, "capacity_queue": True, "capacity_wait_seconds": 30,
                             "capacity_refusals": 2}
    for fields, message in (
            ({"capacity_queue": "true"}, "capacity_queue"),
            ({"capacity_wait_seconds": 5}, "requires capacity_queue: true"),
            ({"capacity_refusals": 5}, "requires capacity_queue: true"),
            ({"capacity_queue": True, "capacity_wait_seconds": math.nan}, "positive finite"),
            ({"capacity_queue": True, "capacity_refusals": 0}, "positive integer")):
        with pytest.raises(ConfigError, match=message):
            ModelRail("openai", None, "m", **fields)


def test_the_documented_defaults_and_caps():
    # nare's own defaults at the pinned release, passed explicitly on every queued session.
    assert (CAPACITY_WAIT_SECONDS, CAPACITY_REFUSALS) == (1800, 50)
    assert (CAPACITY_WAIT_SECONDS_MAX, CAPACITY_REFUSALS_MAX) == (3600, 1000)
