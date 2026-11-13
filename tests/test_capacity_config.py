"""Opt-in `models.capacity`: modes, required values, bounds and canonical bytes."""

import json
import math
import re

import pytest

from scrutare.config import (
    CAPACITY_BOUNDS,
    CAPACITY_MODES,
    CapacitySettings,
    ConfigError,
    ModelRail,
    ModelSettings,
    parse_config,
)

OPENAI = {"default": {"provider": "openai", "model": "m"}}
FIXED = {"mode": "fixed", "wait_seconds": 1800, "refusals": 50}
ADAPTIVE = {**FIXED, "mode": "adaptive", "ceiling": 8, "floor": 1, "quiet_seconds": 60}


def config(capacity=None, models=OPENAI):
    document = {"models": {**models, **({"capacity": capacity} if capacity is not None else {})}}
    return parse_config(json.dumps(document))


def test_absent_and_off_capacity_keep_canonical_bytes():
    plain = config()
    for off in ({}, {"mode": "off"}):
        assert config(off) == plain
        assert config(off).to_dict() == plain.to_dict()
    assert "capacity" not in plain.to_dict()["models"]
    assert plain.models.capacity == CapacitySettings() and plain.models.capacity.mode == "off"
    # YAML 1.1 reads an unquoted `off` as false; it still means off.
    unquoted = parse_config("models:\n  default: {provider: openai, model: m}\n"
                            "  capacity: {mode: off}\n")
    assert unquoted == plain
    # Settings built without the block are the settings they were.
    assert ModelSettings(ModelRail("openai", None, "m")).record() == plain.models.record()


@pytest.mark.parametrize("capacity", [FIXED, ADAPTIVE, {**FIXED, "wait_seconds": 7.5},
                                      {**ADAPTIVE, "quiet_seconds": 0.5, "floor": 8}])
def test_enabled_modes_record_every_value_and_round_trip(capacity):
    loaded = config(capacity)
    assert loaded.to_dict()["models"]["capacity"] == capacity
    assert parse_config(json.dumps(loaded.to_dict())) == loaded
    assert loaded.models.capacity.mode == capacity["mode"]


@pytest.mark.parametrize("capacity,message", [
    ({"mode": "on"}, "mode: expected one of off, fixed, adaptive"),
    ({"mode": "fixed", "refusals": 50}, "wait_seconds: expected a positive finite number"),
    ({"mode": "fixed", "wait_seconds": 1800}, "refusals: expected a positive integer"),
    ({**FIXED, "wait_seconds": 0}, "wait_seconds: expected a positive finite number"),
    ({**FIXED, "wait_seconds": True}, "wait_seconds: expected a positive finite number"),
    ({**FIXED, "wait_seconds": "30"}, "wait_seconds: expected a positive finite number"),
    ({**FIXED, "refusals": 1.5}, "refusals: expected a positive integer"),
    ({**FIXED, "refusals": 0}, "refusals: expected a positive integer"),
    ({**FIXED, "refusals": True}, "refusals: expected a positive integer"),
    ({**FIXED, "ceiling": 8}, "ceiling: not used by mode fixed"),
    ({**FIXED, "quiet_seconds": 60}, "quiet_seconds: not used by mode fixed"),
    ({"mode": "off", "refusals": 50}, "refusals: not used by mode off"),
    ({**ADAPTIVE, "floor": 9}, "floor: must not exceed ceiling"),
    ({**ADAPTIVE, "floor": 0}, "floor: expected a positive integer"),
    ({**ADAPTIVE, "ceiling": None}, "ceiling: expected a positive integer"),
    ({**ADAPTIVE, "quiet_seconds": None}, "quiet_seconds: expected a positive finite number"),
    ({**ADAPTIVE, "shared_dir": "/tmp"}, "shared_dir: unknown field"),
    # Upper bounds: the wait also bounds how far queueing can move a session's deadline.
    ({**FIXED, "wait_seconds": 3600.5}, "wait_seconds: expected at most 3600 seconds"),
    ({**FIXED, "refusals": 1001}, "refusals: expected at most 1000 queued refusals"),
    ({**ADAPTIVE, "ceiling": 65}, "ceiling: expected at most 64 requests in flight"),
    ({**ADAPTIVE, "quiet_seconds": 3601}, "quiet_seconds: expected at most 3600 seconds"),
])
def test_invalid_capacity_refuses(capacity, message):
    with pytest.raises(ConfigError, match="^" + re.escape("models.capacity." + message)):
        config(capacity)


def test_the_bounds_are_accepted_exactly():
    assert CAPACITY_MODES == ("off", "fixed", "adaptive")
    assert CAPACITY_BOUNDS == {"wait_seconds": 3600, "refusals": 1000, "ceiling": 64,
                               "quiet_seconds": 3600}
    loaded = config({"mode": "adaptive", "wait_seconds": 3600, "refusals": 1000, "ceiling": 64,
                     "floor": 64, "quiet_seconds": 3600})
    assert loaded.models.capacity.ceiling == 64


def test_capacity_needs_an_openai_rail():
    with pytest.raises(ConfigError, match="models.capacity.mode: requires provider openai"):
        config(FIXED, {"default": {"provider": "anthropic", "model": "m"}})
    mixed = config(FIXED, {"default": {"provider": "anthropic", "model": "m"},
                           "overrides": {"security": {"provider": "openai"}}})
    assert mixed.models.capacity.mode == "fixed"


def test_the_block_is_review_wide_and_not_a_rail_field():
    # One surface: a rail cannot carry its own queue settings.
    for field in ("capacity", "capacity_queue", "capacity_wait_seconds", "capacity_refusals"):
        with pytest.raises(ConfigError, match=f"models.default.{field}: unknown field"):
            parse_config(json.dumps({"models": {"default": {
                "provider": "openai", "model": "m", field: True}}}))
    loaded = config(FIXED, {**OPENAI, "overrides": {"security": {"model": "other"}}})
    assert "capacity" not in loaded.to_dict()["models"]["default"]
    assert "capacity" not in loaded.to_dict()["models"]["overrides"]["security"]


def test_programmatic_settings_validate_like_configuration():
    assert CapacitySettings("fixed", 30, 5).record() == {
        "mode": "fixed", "wait_seconds": 30, "refusals": 5}
    for arguments, message in (
            (("fixed",), "wait_seconds"), (("fixed", math.inf, 5), "positive finite"),
            (("fixed", 30, 5, 8), "not used by mode fixed"),
            (("adaptive", 30, 5, 4, 5, 60), "must not exceed ceiling"),
            (("fixed", 4000, 5), "at most 3600 seconds")):
        with pytest.raises(ConfigError, match=message):
            CapacitySettings(*arguments)
