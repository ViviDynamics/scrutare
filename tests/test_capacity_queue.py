"""Opt-in nare capacity queueing: configuration, capability, argv and accounting."""

import asyncio
import json
import os
import re
import stat

import pytest
from test_nare_contract_capability import advertised, inspect
from test_nare_session import module, running_executable
from test_review_inputs import capture as capture
from test_structured_output_wave import configure, wave

from scrutare.config import ConfigError, ModelRail, parse_config
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.persona_inputs import prepare_persona_inputs
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_artifacts import create_attempt_directory
from scrutare.engine.session_models import (
    CapacityUsage,
    NareCapability,
    NareRuntime,
    TokenUsage,
)
from scrutare.personas.definition import PersonaDefinition

OPENAI = {"default": {"provider": "openai", "model": "m"}}
FIXED = {"mode": "fixed", "wait_seconds": 1800, "refusals": 50}
ADAPTIVE = {**FIXED, "mode": "adaptive", "ceiling": 8, "floor": 1, "quiet_seconds": 60}
CAPABLE = NareCapability("2026.11.99", 3, capacity=("fixed", "adaptive"))
QUEUE_ENTRY = {"flags": {"enable": "--capacity-queue", "wait_seconds": "--capacity-wait-seconds",
                         "refusals": "--capacity-refusals"}, "providers": ["openai"]}
ADAPTIVE_ENTRY = {"flags": {"enable": "--capacity-adaptive", "ceiling": "--capacity-ceiling",
                            "floor": "--capacity-floor",
                            "quiet_seconds": "--capacity-quiet-seconds",
                            "shared_dir": "--capacity-shared-dir"}, "providers": ["openai"]}


def config(capacity=None, models=OPENAI):
    document = {"models": {**models, **({"capacity": capacity} if capacity is not None else {})}}
    return parse_config(json.dumps(document))


# Configuration


def test_absent_and_off_capacity_keep_canonical_bytes():
    plain = config()
    for off in ({}, {"mode": "off"}):
        assert config(off) == plain
        assert config(off).to_dict() == plain.to_dict()
    assert "capacity" not in plain.to_dict()["models"]
    # YAML 1.1 reads an unquoted `off` as false; it still means off.
    unquoted = parse_config("models:\n  default: {provider: openai, model: m}\n"
                            "  capacity: {mode: off}\n")
    assert unquoted == plain


@pytest.mark.parametrize("capacity", [FIXED, ADAPTIVE])
def test_enabled_modes_record_every_value_and_round_trip(capacity):
    loaded = config(capacity)
    assert loaded.to_dict()["models"]["capacity"] == capacity
    assert parse_config(json.dumps(loaded.to_dict())) == loaded


@pytest.mark.parametrize("capacity,message", [
    ({"mode": "on"}, "mode: expected one of off, fixed, adaptive"),
    ({"mode": "fixed", "refusals": 50}, "wait_seconds: expected a positive finite number"),
    ({"mode": "fixed", "wait_seconds": 1800}, "refusals: expected a positive integer"),
    ({**FIXED, "wait_seconds": 0}, "wait_seconds: expected a positive finite number"),
    ({**FIXED, "wait_seconds": True}, "wait_seconds: expected a positive finite number"),
    ({**FIXED, "refusals": 1.5}, "refusals: expected a positive integer"),
    ({**FIXED, "ceiling": 8}, "ceiling: not used by mode fixed"),
    ({"mode": "off", "refusals": 50}, "refusals: not used by mode off"),
    ({**ADAPTIVE, "floor": 9}, "floor: must not exceed ceiling"),
    ({**ADAPTIVE, "quiet_seconds": None}, "quiet_seconds: expected a positive finite number"),
    ({**ADAPTIVE, "shared_dir": "/tmp"}, "shared_dir: unknown field"),
])
def test_invalid_capacity_refuses(capacity, message):
    with pytest.raises(ConfigError, match="^" + re.escape("models.capacity." + message)):
        config(capacity)


def test_capacity_needs_an_openai_rail():
    with pytest.raises(ConfigError, match="models.capacity.mode: requires provider openai"):
        config(FIXED, {"default": {"provider": "anthropic", "model": "m"}})
    mixed = config(FIXED, {"default": {"provider": "anthropic", "model": "m"},
                           "overrides": {"security": {"provider": "openai"}}})
    assert mixed.models.capacity.mode == "fixed"


# Capability


@pytest.mark.parametrize("entries,modes", [
    ({}, ()),
    ({"capacity_queue": QUEUE_ENTRY}, ("fixed",)),
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY}, ("fixed", "adaptive")),
    ({"capacity_queue": {**QUEUE_ENTRY, "providers": ["anthropic"]},
      "capacity_adaptive": ADAPTIVE_ENTRY}, ()),
    ({"capacity_queue": {**QUEUE_ENTRY, "flags": {}}}, ()),
    ({"capacity_adaptive": ADAPTIVE_ENTRY}, ()),
])
def test_runtime_reads_exact_advertised_capacity_modes(tmp_path, entries, modes):
    path = advertised(tmp_path, version="2026.11.99", contract=3)
    path.write_text(path.read_text().replace(
        '"nare": \'2026.11.99\'}', f'"nare": \'2026.11.99\', **{entries!r}}}'))
    assert inspect(path).capacity == modes


@pytest.mark.parametrize("capacity,capability,minimum", [
    (FIXED, NareCapability("2026.11.80", 3), "2026.11.95"),
    (ADAPTIVE, NareCapability("2026.11.95", 3, capacity=("fixed",)), "2026.11.96"),
])
def test_incapable_wave_refuses_before_any_launch(capture, monkeypatch, capacity, capability,
                                                  minimum):
    loaded = configure(capture, {**OPENAI, "capacity": capacity})
    assert loaded.models.capacity.mode == capacity["mode"]
    with pytest.raises(module().SessionRuntimeError,
                       match=f"cannot queue capacity refusals.*{re.escape(minimum)} or newer"):
        wave(capture, monkeypatch, loaded, capability)
    assert not (capture / "fanout.json").exists()


def test_capable_wave_records_the_block_in_prepared_configuration(capture, monkeypatch):
    loaded = configure(capture, {**OPENAI, "capacity": ADAPTIVE})
    wave(capture, monkeypatch, loaded, CAPABLE)
    prepared = json.loads((capture / "config.json").read_text())
    assert prepared["models"]["capacity"] == ADAPTIVE


# Native invocation

RESULT = '"contract": 1, "nare": "2026.10.0", "output":'
SAVED = '"contract": 1, "nare": "2026.10.0"}'
REFUSAL = ('print(json.dumps({"type": "progress", "text": "capacity refusal", "detail": '
           '{"capacity": {"attempt": 1, "wait": 2.5, "waited": 2.5, "refusals": 1, '
           '"refusals_limit": 50, "status": 429, "reason": "max_parallel_requests"}}, '
           '"timestamp": "now"}), flush=True)\n'
           'print(json.dumps({"type": "progress", "text": "slot", "detail": {"capacity_limit": '
           '{"limit": 4, "reason": "slot", "waited": 0.25}}, "timestamp": "now"}), flush=True)\n')


def native(tmp_path, *, counters=True, extra="", status="done", exit_code=0):
    """A contract-3 2026.11.99-shaped scripted nare that accepts the capacity flags."""
    path = running_executable(tmp_path, extra=REFUSAL + extra, status=status, exit_code=exit_code,
                              stop="end_turn" if status == "done" else None,
                              output=None if status == "done" else "NONE")
    reported = (', "capacity_refusals": 1, "capacity_wait_seconds": 2.5, "capacity_limit": 4, '
                '"capacity_slot_wait_seconds": 0.25' if counters else "")
    stored = (', "capacity_refusals": 1, "capacity_wait": 2.5, "capacity_limit": 4, '
              '"capacity_slot_wait": 0.25' if counters else "")
    body = path.read_text().replace("'NONE'", "None")
    assert body.count(RESULT) == 1 and body.count(SAVED) == 1
    body = (body.replace(RESULT, '"contract": 3, "nare": "2026.11.99"' + reported + ', "output":')
            .replace(SAVED, '"contract": 3, "nare": "2026.11.99"' + stored + "}")
            .replace("a = parser.parse_args()",
                     'for flag in ("max-cutoffs", "request-retries", "max-corrections",\n'
                     '             "capacity-wait-seconds", "capacity-refusals",\n'
                     '             "capacity-ceiling", "capacity-floor",\n'
                     '             "capacity-quiet-seconds", "capacity-shared-dir"):\n'
                     '    parser.add_argument("--" + flag)\n'
                     'for flag in ("stream", "capacity-queue", "capacity-adaptive"):\n'
                     '    parser.add_argument("--" + flag, action="store_true")\n'
                     'a = parser.parse_args()'))
    path.write_text(body)
    return path


def prepared(capture, capacity=None, *, limit=40):
    loaded = configure(capture, {**OPENAI, **({"capacity": capacity} if capacity else {})},
                       ("security",))
    inputs = prepare_review_inputs(capture, loaded)
    descriptor = prepare_persona_inputs(inputs, (PersonaDefinition("security", "Review."),))[0]
    from scrutare.config import BudgetSettings
    ledger = ReviewBudgetLedger(("security",), BudgetSettings(limit, limit))
    lease = ledger.admit("security", "attempt-0001")
    attempt = create_attempt_directory(capture, "security", prepared_root=inputs.root)
    return descriptor, ledger, lease, attempt


def launch(capture, path, capacity=None, *, rail=None, capability=CAPABLE, timeout=5.0):
    descriptor, ledger, lease, attempt = prepared(capture, capacity)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, rail or ModelRail("openai", None, "m"), lease, ledger=ledger,
        artifact_directory=attempt, runtime=NareRuntime(path, 7, timeout),
        capability=capability))
    return outcome, ledger, attempt


def recovery(argv):
    index = argv.index("--max-corrections")
    return argv[index + 2:]


def test_off_argv_and_records_are_unchanged(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, native(tmp_path))
    assert outcome.status == "complete" and outcome.capacity is None
    argv = json.loads((attempt / "invocation.json").read_text())["argv"]
    assert not [item for item in argv if "capacity" in item]
    # The recovery controls are followed directly by what followed them before.
    assert recovery(argv)[:2] == ["--jsonl", "--yes"]
    result = json.loads((attempt / "result.json").read_text())
    assert "capacity" not in result
    assert not (capture / "capacity").exists()
    rail = ModelRail("openai", None, "m")
    assert (module()._rail_argv(rail, CAPABLE)
            == module()._rail_argv(rail, CAPABLE, module().capacity_argv(
                rail, config().models.capacity, capture)))


def test_fixed_mode_queues_without_a_shared_directory(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, native(tmp_path), FIXED)
    assert outcome.status == "complete"
    argv = json.loads((attempt / "invocation.json").read_text())["argv"]
    assert recovery(argv)[:5] == ["--capacity-queue", "--capacity-wait-seconds", "1800",
                                  "--capacity-refusals", "50"]
    assert "--capacity-adaptive" not in argv and "--capacity-shared-dir" not in argv
    assert not (capture / "capacity").exists()
    observed = json.loads((attempt / "cwd/observed.json").read_text())["argv"]
    assert "--capacity-queue" in observed


def test_adaptive_mode_shares_one_private_run_directory(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, native(tmp_path), ADAPTIVE)
    assert outcome.status == "complete"
    argv = json.loads((attempt / "invocation.json").read_text())["argv"]
    assert recovery(argv)[:14] == [
        "--capacity-queue", "--capacity-wait-seconds", "1800", "--capacity-refusals", "50",
        "--capacity-adaptive", "--capacity-ceiling", "8", "--capacity-floor", "1",
        "--capacity-quiet-seconds", "60", "--capacity-shared-dir", str(capture / "capacity")]
    info = os.lstat(capture / "capacity")
    assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700


def test_anthropic_rail_never_queues(capture, tmp_path):
    path = native(tmp_path)
    outcome, ledger, attempt = launch(capture, path, FIXED,
                                      rail=ModelRail("anthropic", None, "m"))
    argv = json.loads((attempt / "invocation.json").read_text())["argv"]
    assert not [item for item in argv if "capacity" in item]
    assert outcome.capacity is None


def test_linked_shared_directory_is_refused(capture, tmp_path):
    (tmp_path / "elsewhere").mkdir(mode=0o700)
    (capture / "capacity").symlink_to(tmp_path / "elsewhere")
    outcome, ledger, attempt = launch(capture, native(tmp_path), ADAPTIVE)
    assert outcome.status == "failed" and outcome.reason == "artifacts"
    assert not (attempt / "cwd/observed.json").exists()
    # Never invoked, so nothing queued and no counters are claimed.
    assert outcome.capacity is None
    assert "capacity" not in json.loads((attempt / "result.json").read_text())


def test_incapable_session_never_starts(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, native(tmp_path), ADAPTIVE,
                                      capability=NareCapability("2026.11.99", 3,
                                                                capacity=("fixed",)))
    assert outcome.status == "failed"
    assert not (attempt / "cwd/observed.json").exists()


# Accounting


def test_refusals_are_recorded_apart_from_usage_and_budget(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, native(tmp_path), FIXED)
    assert outcome.status == "complete" and outcome.accounting_complete
    assert outcome.capacity == CapacityUsage(1, 2.5, 4, 0.25, "result")
    assert outcome.usage == TokenUsage(4, 2, 3, 1) and ledger.usage == outcome.usage
    result = json.loads((attempt / "result.json").read_text())
    assert result["capacity"] == {"refusals": 1, "wait_seconds": 2.5, "limit": 4,
                                  "slot_wait_seconds": 0.25, "source": "result"}
    assert result["usage"]["total"] == 10


def test_refusals_past_nare_limits_are_an_ordinary_failure(capture, tmp_path):
    outcome, ledger, attempt = launch(
        capture, native(tmp_path, status="error", exit_code=1), FIXED)
    assert outcome.status == "failed" and outcome.reason == "provider"
    assert outcome.capacity is not None and outcome.capacity.refusals == 1


def test_a_session_without_a_result_counts_refusal_events(capture, tmp_path):
    path = native(tmp_path, extra="import time\ntime.sleep(30)\n")
    outcome, ledger, attempt = launch(capture, path, FIXED, timeout=1.5)
    assert outcome.status == "failed" and outcome.reason == "timeout"
    assert outcome.capacity == CapacityUsage(1, 2.5, 4, 0.25, "events")


def test_inconsistent_saved_counters_fail_the_session(capture, tmp_path):
    path = native(tmp_path)
    path.write_text(path.read_text().replace('"capacity_refusals": 1, "capacity_wait"',
                                             '"capacity_refusals": 2, "capacity_wait"'))
    outcome, ledger, attempt = launch(capture, path, FIXED)
    assert outcome.status == "failed" and outcome.reason == "protocol"
    assert outcome.capacity is not None and outcome.capacity.source == "events"


def test_wave_totals_capacity_apart_from_usage():
    from pathlib import Path

    from scrutare.engine.session_models import FanOutResult, SessionOutcome

    def outcome(name, capacity):
        return SessionOutcome(name, "complete", "done", (), True, TokenUsage(1), True, 5, 0,
                              None, Path("/run") / name, 5, capacity=capacity)

    plain = FanOutResult((outcome("a", None),), False, False, TokenUsage(1), False, 0)
    assert "capacity" not in plain.to_dict()
    queued = FanOutResult((outcome("a", CapacityUsage(2, 1.25, 4, 0.5)),
                           outcome("b", CapacityUsage(1, 0.5, None, 0.0, "events")),
                           outcome("c", None)),
                          False, False, TokenUsage(3), False, 0)
    assert queued.to_dict()["capacity"] == {"sessions": 2, "refusals": 3,
                                            "wait_seconds": 1.75, "slot_wait_seconds": 0.5}
    assert queued.to_dict()["usage"] == TokenUsage(3).to_dict()
