"""Opt-in nare capacity queueing: capability, argv, shared directory, accounting, protocol
checks and the timeout credit."""

import asyncio
import json
import os
import re
import shutil
import stat

import pytest
from test_capacity_config import ADAPTIVE, FIXED, OPENAI
from test_capacity_config import config as parsed
from test_nare_flag_capability import advertised, inspect
from test_nare_session import RUN_BODY, contract3_capability, executable, flag_map, module
from test_review_inputs import capture as capture
from test_structured_output_wave import configure, wave

from scrutare.config import BudgetSettings, CapacitySettings, ModelRail
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

VERSION = "2026.11.100"
# The capacity options nare 2026.11.100 lists under `flags`.
CAPACITY_FLAGS = {
    **{name: {"takes_value": False, "choices": None}
       for name in ("--capacity-queue", "--capacity-adaptive")},
    **{name: {"takes_value": True, "choices": None}
       for name in ("--capacity-wait-seconds", "--capacity-refusals", "--capacity-ceiling",
                    "--capacity-floor", "--capacity-quiet-seconds", "--capacity-shared-dir")},
}
# The two contract entries as nare 2026.11.100 prints them.
QUEUE_ENTRY = {
    "flags": {"enable": "--capacity-queue", "wait_seconds": "--capacity-wait-seconds",
              "refusals": "--capacity-refusals"},
    "env": {"enable": "NARE_CAPACITY_QUEUE", "wait_seconds": "NARE_CAPACITY_WAIT_SECONDS",
            "refusals": "NARE_CAPACITY_REFUSALS"},
    "default": {"enable": False, "wait_seconds": 1800.0, "refusals": 50},
    "per": "model_request", "providers": ["openai"], "event": "progress",
    "event_detail": "capacity", "result_field": "capacity_refusals",
    "session_field": "capacity_refusals",
}
ADAPTIVE_ENTRY = {
    "flags": {"enable": "--capacity-adaptive", "ceiling": "--capacity-ceiling",
              "floor": "--capacity-floor", "quiet_seconds": "--capacity-quiet-seconds",
              "shared_dir": "--capacity-shared-dir"},
    "env": {"enable": "NARE_CAPACITY_ADAPTIVE"},
    "default": {"enable": False, "ceiling": 8, "floor": 1, "quiet_seconds": 60.0,
                "shared_dir": None},
    "modes": {"off": "neither flag", "fixed": "--capacity-queue alone (the queue, no limit)",
              "adaptive": "--capacity-queue with --capacity-adaptive"},
    "per": "model_request", "providers": ["openai"], "event": "progress",
    "event_detail": "capacity_limit", "result_field": "capacity_limit",
    "session_field": "capacity_limit", "slot_wait_result_field": "capacity_slot_wait_seconds",
}


def capable(*modes, without=(), extra=None):
    return contract3_capability(without=without, extra={**CAPACITY_FLAGS, **(extra or {})},
                                version=VERSION, capacity=modes or ("fixed", "adaptive"))


CAPABLE = capable()


def emit(detail):
    record = {"type": "progress", "text": "capacity", "detail": detail, "timestamp": "now"}
    return f"print(json.dumps({record!r}), flush=True)\n"


def refusal(wait, attempt=1):
    """One queued refusal as nare writes it, before it sleeps for the announced wait."""
    return emit({"capacity": {"attempt": attempt, "wait": wait, "waited": wait,
                              "refusals": attempt, "refusals_limit": 50, "status": 429,
                              "reason": "max_parallel_requests"}})


def cut(limit, previous):
    return emit({"capacity_limit": {"limit": limit, "previous": previous, "reason": "refusal"}})


def slot(limit, waited):
    """A wait for a request slot, which nare reports only once it has ended."""
    return emit({"capacity_limit": {"limit": limit, "reason": "slot", "waited": waited}})


def native(tmp_path, *, events="", refusals=0, wait=0.0, limit=None, slot_wait=0.0,
           counters=True, saved=None, status="done", exit_code=0):
    """A contract 3, 2026.11.100-shaped scripted nare that accepts the capacity flags.

    ``events`` is what it prints before its result; the counters are what its result line
    and saved session report. ``saved`` overrides the saved session's own counters.
    """
    stored = {"capacity_refusals": refusals, "capacity_wait": wait, "capacity_limit": limit,
              "capacity_slot_wait": slot_wait, **(saved or {})}
    reported = (f'"capacity_refusals": {refusals}, "capacity_wait_seconds": {wait}, '
                f'"capacity_limit": {limit}, "capacity_slot_wait_seconds": {slot_wait}, '
                if counters else "")
    kept = "".join(f'"{name}": {value}, ' for name, value in stored.items()) if counters else ""
    done = status == "done"
    body = (RUN_BODY.replace("INPUT", "4").replace("STATUS", repr(status))
            .replace("STOP", repr("end_turn" if done else None))
            .replace("OUTPUT", "{'findings': []}" if done else "None")
            .replace("EXTRA", events).replace("EXIT", str(exit_code))
            .replace('"contract": 1', '"contract": 3').replace('"2026.10.0"', f'"{VERSION}"')
            .replace('a = parser.parse_args()',
                     'for flag in ("max-cutoffs", "request-retries", "max-corrections",\n'
                     '             "compaction", "compaction-trigger", "compaction-target",\n'
                     '             "capacity-wait-seconds", "capacity-refusals",\n'
                     '             "capacity-ceiling", "capacity-floor",\n'
                     '             "capacity-quiet-seconds", "capacity-shared-dir"):\n'
                     '    parser.add_argument("--" + flag)\n'
                     'for flag in ("stream", "capacity-queue", "capacity-adaptive"):\n'
                     '    parser.add_argument("--" + flag, action="store_true")\n'
                     'a = parser.parse_args()')
            .replace('result = {"type": "result",', 'result = {' + reported + '"type": "result",')
            .replace('saved = {"id": "stable-id",', 'saved = {' + kept + '"id": "stable-id",'))
    return executable(tmp_path, version=VERSION, contract=3, body=body)


def queued(tmp_path, **changes):
    """A session with one queued refusal, as the queue alone produces it."""
    return native(tmp_path, **{"events": refusal(2.5), "refusals": 1, "wait": 2.5, **changes})


def limited(tmp_path, **changes):
    """A session with one queued refusal, a cut of the limit and a wait for a slot."""
    return native(tmp_path, **{"events": refusal(2.5) + cut(4, 8) + slot(4, 0.25),
                               "refusals": 1, "wait": 2.5, "limit": 4, "slot_wait": 0.25,
                               **changes})


def prepared(capture, capacity=None, *, limit=40):
    loaded = configure(capture, {**OPENAI, **({"capacity": capacity} if capacity else {})},
                       ("security",))
    inputs = prepare_review_inputs(capture, loaded)
    descriptor = prepare_persona_inputs(inputs, (PersonaDefinition("security", "Review."),))[0]
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


def load(path):
    return json.loads(path.read_bytes())


def capacity_flags(argv):
    """The capacity flags of one argv, in order, each value flag with its value."""
    index = next((i for i, item in enumerate(argv) if item.startswith("--capacity")), len(argv))
    found = []
    while index < len(argv) and argv[index].startswith("--capacity"):
        switch = argv[index] in ("--capacity-queue", "--capacity-adaptive")
        found.extend(argv[index:index + (1 if switch else 2)])
        index += 1 if switch else 2
    return found


# Capability


@pytest.mark.parametrize("entries,flags,modes", [
    ({}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": QUEUE_ENTRY}, CAPACITY_FLAGS, ("fixed",)),
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY}, CAPACITY_FLAGS,
     ("fixed", "adaptive")),
    # Additive fields in an entry are tolerated.
    ({"capacity_queue": {**QUEUE_ENTRY, "added_later": True},
      "capacity_adaptive": {**ADAPTIVE_ENTRY, "added_later": True}}, CAPACITY_FLAGS,
     ("fixed", "adaptive")),
    ({"capacity_queue": {**QUEUE_ENTRY, "providers": ["anthropic"]},
      "capacity_adaptive": ADAPTIVE_ENTRY}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": {**QUEUE_ENTRY, "providers": "openai"}}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": {**QUEUE_ENTRY, "flags": {}}}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": {**QUEUE_ENTRY, "event_detail": "other"}}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": {**QUEUE_ENTRY, "result_field": "other"}}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": "--capacity-queue"}, CAPACITY_FLAGS, ()),
    # The adaptive limit needs the queue.
    ({"capacity_adaptive": ADAPTIVE_ENTRY}, CAPACITY_FLAGS, ()),
    ({"capacity_queue": QUEUE_ENTRY,
      "capacity_adaptive": {**ADAPTIVE_ENTRY, "event_detail": "other"}}, CAPACITY_FLAGS,
     ("fixed",)),
    ({"capacity_queue": QUEUE_ENTRY,
      "capacity_adaptive": {**ADAPTIVE_ENTRY, "flags": {"enable": "--capacity-adaptive"}}},
     CAPACITY_FLAGS, ("fixed",)),
    # An entry is not enough: the flag list must carry each option in the shape it is sent.
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY}, {}, ()),
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY},
     {name: record for name, record in CAPACITY_FLAGS.items() if name != "--capacity-refusals"},
     ()),
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY},
     {name: record for name, record in CAPACITY_FLAGS.items()
      if name != "--capacity-shared-dir"}, ("fixed",)),
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY},
     {**CAPACITY_FLAGS, "--capacity-queue": {"takes_value": True, "choices": None}}, ()),
    ({"capacity_queue": QUEUE_ENTRY, "capacity_adaptive": ADAPTIVE_ENTRY},
     {**CAPACITY_FLAGS, "--capacity-ceiling": {"takes_value": False, "choices": None}},
     ("fixed",)),
])
def test_inspection_reads_the_capacity_modes_the_runtime_offers(tmp_path, entries, flags, modes):
    found = inspect(advertised(tmp_path, version=VERSION, flags=flag_map(extra=flags),
                               **entries))
    assert found.capacity == modes


def test_contracts_1_and_2_offer_no_capacity_mode(tmp_path):
    found = inspect(advertised(tmp_path, version="2026.10.12", contract=2,
                               flags=flag_map(extra=CAPACITY_FLAGS), capacity_queue=QUEUE_ENTRY,
                               capacity_adaptive=ADAPTIVE_ENTRY))
    assert found.capacity == () and NareCapability("2026.10.12", 2).capacity == ()
    with pytest.raises(ValueError, match="capacity"):
        NareCapability(VERSION, 3, capacity=("turbo",))


@pytest.mark.parametrize("capacity,capability,message", [
    (FIXED, contract3_capability(version="2026.11.94"),
     "in mode fixed; use a nare release that advertises it (2026.11.95 or newer)"),
    (ADAPTIVE, capable("fixed"),
     "in mode adaptive; use a nare release that advertises it (2026.11.96 or newer)"),
    (FIXED, NareCapability("2026.10.12", 2), "in mode fixed"),
    # Only a streamed session writes a queue record before it waits.
    (FIXED, capable(without=("--stream",)), "in mode fixed without --stream"),
    (ADAPTIVE, capable(without=("--stream",)), "in mode adaptive without --stream"),
])
def test_incapable_wave_refuses_before_any_launch(capture, monkeypatch, capacity, capability,
                                                  message):
    loaded = configure(capture, {**OPENAI, "capacity": capacity})
    assert loaded.models.capacity.mode == capacity["mode"]
    with pytest.raises(module().SessionRuntimeError,
                       match="cannot queue capacity refusals " + re.escape(message)):
        wave(capture, monkeypatch, loaded, capability)
    assert not (capture / "fanout.json").exists()
    assert not (capture / "capacity").exists()


def test_a_wave_without_the_block_needs_nothing_from_the_runtime(capture, monkeypatch):
    loaded = configure(capture, OPENAI)
    launched, _ = wave(capture, monkeypatch, loaded, NareCapability("2026.10.12", 2))
    assert len(launched) == 1
    assert "capacity" not in load(capture / "fanout.json")["result"]


def test_capable_wave_records_the_block_in_prepared_configuration(capture, monkeypatch):
    loaded = configure(capture, {**OPENAI, "capacity": ADAPTIVE})
    wave(capture, monkeypatch, loaded, CAPABLE)
    assert load(capture / "config.json")["models"]["capacity"] == ADAPTIVE


# Native invocation


def test_off_argv_records_and_wait_path_are_unchanged(capture, tmp_path, monkeypatch):
    credits = []
    wait = module()._wait_capture

    async def recording(*args, **kwargs):
        credits.append(kwargs.get("credit", "absent"))
        return await wait(*args, **kwargs)

    monkeypatch.setattr(module(), "_wait_capture", recording)
    outcome, ledger, attempt = launch(capture, native(tmp_path))
    assert outcome.status == "complete" and outcome.capacity is None
    # The session waited without a deadline credit, as it did before the block existed.
    assert credits == [None]
    argv = load(attempt / "invocation.json")["argv"]
    assert not [item for item in argv if item.startswith("--capacity")]
    result = load(attempt / "result.json")
    assert "capacity" not in result and "capacity" not in outcome.to_dict()
    assert not (capture / "capacity").exists()
    rail = ModelRail("openai", "https://gateway.invalid/v1", "m")
    off = module().capacity_argv(rail, parsed().models.capacity, capture)
    assert off == []
    assert module()._rail_argv(rail, CAPABLE) == module()._rail_argv(rail, CAPABLE, off)
    # The same rail against a runtime that lists none of the capacity flags.
    assert module()._rail_argv(rail, CAPABLE) == module()._rail_argv(
        rail, contract3_capability(version="2026.11.94"))


def test_an_explicit_off_block_leaves_every_artifact_as_without_the_block(capture, tmp_path):
    # Two copies of one capture: one configured with no block, one with `mode: off`.
    other = tmp_path / "explicit-off" / capture.name
    shutil.copytree(capture, other)
    path = native(tmp_path)
    plain, _, first = launch(capture, path)
    explicit, _, second = launch(other, path, {"mode": "off"})
    assert plain.status == explicit.status == "complete"
    assert (capture / "config.json").read_bytes() == (other / "config.json").read_bytes()
    for name in ("invocation.json", "result.json", "stdout.jsonl", "session.json",
                 "findings.schema.json"):
        assert ((first / name).read_bytes().replace(str(capture).encode(), b"<run>")
                == (second / name).read_bytes().replace(str(other).encode(), b"<run>")), name
    assert sorted(item.name for item in first.iterdir()) == sorted(
        item.name for item in second.iterdir())
    assert not (other / "capacity").exists()


def test_fixed_mode_queues_without_a_shared_directory(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, queued(tmp_path), FIXED)
    assert outcome.status == "complete"
    argv = load(attempt / "invocation.json")["argv"]
    assert capacity_flags(argv) == ["--capacity-queue", "--capacity-wait-seconds", "1800",
                                    "--capacity-refusals", "50"]
    assert "--capacity-adaptive" not in argv and "--capacity-shared-dir" not in argv
    # The recovery policy is untouched: a queued refusal is not a resend.
    assert argv[argv.index("--request-retries") + 1] == "0"
    assert not (capture / "capacity").exists()
    assert load(attempt / "cwd/observed.json")["argv"] == argv[1:]


def test_adaptive_mode_shares_one_private_run_directory(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    assert outcome.status == "complete"
    argv = load(attempt / "invocation.json")["argv"]
    assert capacity_flags(argv) == [
        "--capacity-queue", "--capacity-wait-seconds", "1800", "--capacity-refusals", "50",
        "--capacity-adaptive", "--capacity-ceiling", "8", "--capacity-floor", "1",
        "--capacity-quiet-seconds", "60", "--capacity-shared-dir", str(capture / "capacity")]
    info = os.lstat(capture / "capacity")
    assert stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
    # Beside the sessions and the review inputs, never inside a persona's read root.
    root = argv[argv.index("--root") + 1]
    assert not (capture / "capacity").is_relative_to(root)
    assert (capture / "capacity").parent == (capture / "sessions").parent


def test_fractional_values_are_passed_as_configured(capture, tmp_path):
    capacity = {**ADAPTIVE, "wait_seconds": 7.5, "quiet_seconds": 0.5}
    outcome, _, attempt = launch(capture, limited(tmp_path), capacity)
    assert outcome.status == "complete"
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[argv.index("--capacity-wait-seconds") + 1] == "7.5"
    assert argv[argv.index("--capacity-quiet-seconds") + 1] == "0.5"


def test_anthropic_rail_never_queues(capture, tmp_path):
    mixed = parsed(FIXED, {**OPENAI, "overrides": {"security": {"provider": "anthropic"}}})
    rail = mixed.models.for_persona("security")
    assert rail.provider == "anthropic"
    assert module().capacity_argv(rail, mixed.models.capacity, capture) == []
    assert module().capacity_argv(mixed.models.default, mixed.models.capacity, capture) != []
    outcome, ledger, attempt = launch(capture, native(tmp_path), FIXED,
                                      rail=ModelRail("anthropic", None, "m"))
    argv = load(attempt / "invocation.json")["argv"]
    assert not [item for item in argv if item.startswith("--capacity")]
    assert outcome.status == "complete" and outcome.capacity is None


def test_linked_shared_directory_is_refused(capture, tmp_path):
    (tmp_path / "elsewhere").mkdir(mode=0o700)
    (capture / "capacity").symlink_to(tmp_path / "elsewhere")
    outcome, ledger, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    assert outcome.status == "failed" and outcome.reason == "artifacts"
    assert not (attempt / "cwd/observed.json").exists()
    # Never invoked, so nothing queued and no counters are claimed.
    assert outcome.capacity is None
    assert "capacity" not in load(attempt / "result.json")


def test_a_shared_directory_others_can_enter_is_refused(capture, tmp_path):
    (capture / "capacity").mkdir(mode=0o755)
    outcome, _, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    assert outcome.status == "failed" and outcome.reason == "artifacts"
    assert not (attempt / "cwd/observed.json").exists()


@pytest.mark.parametrize("capacity,capability", [
    (ADAPTIVE, capable("fixed")), (FIXED, contract3_capability(version=VERSION)),
    (FIXED, capable(without=("--stream",)))])
def test_incapable_session_never_starts(capture, tmp_path, capacity, capability):
    outcome, ledger, attempt = launch(capture, limited(tmp_path), capacity,
                                      capability=capability)
    assert (outcome.status, outcome.reason) == ("failed", "invocation")
    assert not (attempt / "cwd/observed.json").exists() and outcome.capacity is None


# Accounting


def test_refusals_are_recorded_apart_from_usage_and_budget(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, queued(tmp_path), FIXED)
    assert outcome.status == "complete" and outcome.accounting_complete
    assert outcome.capacity == CapacityUsage(1, 2.5, None, 0.0, "result")
    # The refused request was never dispatched: one cost event, counted once.
    assert outcome.usage == TokenUsage(4, 2, 3, 1) and ledger.usage == outcome.usage
    result = load(attempt / "result.json")
    assert result["capacity"] == {"refusals": 1, "wait_seconds": 2.5, "limit": None,
                                  "slot_wait_seconds": 0.0, "source": "result"}
    assert result["usage"]["total"] == 10


def test_adaptive_counters_include_the_limit_and_the_slot_wait(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, limited(tmp_path), ADAPTIVE)
    assert outcome.status == "complete" and outcome.accounting_complete
    assert outcome.capacity == CapacityUsage(1, 2.5, 4, 0.25, "result")
    assert outcome.usage == TokenUsage(4, 2, 3, 1) == ledger.usage
    assert load(attempt / "result.json")["capacity"] == {
        "refusals": 1, "wait_seconds": 2.5, "limit": 4, "slot_wait_seconds": 0.25,
        "source": "result"}


def test_a_session_that_queued_nothing_still_records_zeros(capture, tmp_path):
    outcome, _, attempt = launch(capture, native(tmp_path), FIXED)
    assert outcome.status == "complete"
    assert outcome.capacity == CapacityUsage(0, 0.0, None, 0.0, "result")
    assert load(attempt / "result.json")["capacity"]["refusals"] == 0


def test_refusals_past_nare_limits_are_an_ordinary_failure(capture, tmp_path):
    outcome, ledger, attempt = launch(
        capture, queued(tmp_path, status="error", exit_code=1), FIXED)
    assert outcome.status == "failed" and outcome.reason == "provider"
    assert outcome.capacity is not None and outcome.capacity.refusals == 1


def test_a_session_without_a_result_counts_refusal_events(capture, tmp_path):
    # Its one announced wait is credited, and the silence after it is not.
    path = limited(tmp_path, events=refusal(0.25) + cut(4, 8) + slot(4, 0.25)
                   + "import time\ntime.sleep(30)\n")
    outcome, ledger, attempt = launch(capture, path, ADAPTIVE, timeout=1.5)
    assert outcome.status == "failed" and outcome.reason == "timeout"
    assert not outcome.accounting_complete
    assert outcome.capacity == CapacityUsage(1, 0.25, 4, 0.25, "events")
    assert load(attempt / "result.json")["capacity"]["source"] == "events"


def test_inconsistent_saved_counters_fail_the_session(capture, tmp_path):
    path = queued(tmp_path, saved={"capacity_refusals": 2})
    outcome, ledger, attempt = launch(capture, path, FIXED)
    assert outcome.status == "failed" and outcome.reason == "protocol"
    assert outcome.capacity == CapacityUsage(1, 2.5, None, 0.0, "events")


def test_wave_totals_capacity_apart_from_usage():
    from pathlib import Path

    from scrutare.engine.session_models import FanOutResult, SessionOutcome

    def outcome(name, capacity):
        return SessionOutcome(name, "complete", "done", (), True, TokenUsage(1), True, 5, 0,
                              None, Path("/run") / name, 5, capacity=capacity)

    plain = FanOutResult((outcome("a", None),), False, False, TokenUsage(1), False, 0)
    assert "capacity" not in plain.to_dict()
    assert "capacity" not in plain.to_dict()["outcomes"][0]
    queue = FanOutResult((outcome("a", CapacityUsage(2, 1.25, 4, 0.5)),
                          outcome("b", CapacityUsage(1, 0.5, None, 0.0, "events")),
                          outcome("c", None)),
                         False, False, TokenUsage(3), False, 0)
    assert queue.to_dict()["capacity"] == {"sessions": 2, "refusals": 3,
                                           "wait_seconds": 1.75, "slot_wait_seconds": 0.5}
    assert queue.to_dict()["usage"] == TokenUsage(3).to_dict()


# Protocol checks


@pytest.mark.parametrize("changes", [
    # nare counted a refusal no record shows, and the reverse.
    {"events": ""}, {"refusals": 0, "wait": 0.0},
    # The same record with other seconds than nare totalled.
    {"wait": 4.0},
    # A runtime that reports no counters cannot have queued.
    {"counters": False},
])
def test_queue_records_that_disagree_with_the_result_are_a_protocol_failure(
        capture, tmp_path, changes):
    outcome, ledger, attempt = launch(capture, queued(tmp_path, **changes), FIXED)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not outcome.accounting_complete and not ledger.accounting_complete
    # The retained stream is still counted for the record.
    assert outcome.capacity is not None and outcome.capacity.source == "events"
    assert outcome.capacity.refusals == (0 if changes.get("events") == "" else 1)


@pytest.mark.parametrize("capacity,rail,script", [
    # No block: nare was not told to queue, so the record is not this session's.
    (None, None, {"events": refusal(2.5), "refusals": 1, "wait": 2.5}),
    (None, None, {"events": slot(4, 0.25), "limit": 4, "slot_wait": 0.25}),
    (None, None, {"limit": 4}),
    # A rail the block does not apply to.
    (FIXED, ModelRail("anthropic", None, "m"),
     {"events": refusal(2.5), "refusals": 1, "wait": 2.5}),
    # The queue alone: a limit, a limit record or a slot wait was never asked for.
    (FIXED, None, {"events": refusal(2.5) + cut(4, 8) + slot(4, 0.25), "refusals": 1,
                   "wait": 2.5, "limit": 4, "slot_wait": 0.25}),
    (FIXED, None, {"events": refusal(2.5), "refusals": 1, "wait": 2.5, "limit": 4}),
])
def test_capacity_evidence_a_session_did_not_ask_for_is_a_protocol_failure(
        capture, tmp_path, capacity, rail, script):
    outcome, ledger, attempt = launch(capture, native(tmp_path, **script), capacity, rail=rail)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not outcome.accounting_complete
    if capacity is None or rail is not None:
        assert outcome.capacity is None and "capacity" not in load(attempt / "result.json")
        assert not [item for item in load(attempt / "invocation.json")["argv"]
                    if item.startswith("--capacity")]


@pytest.mark.parametrize("record", [
    {"capacity": []}, {"capacity": {"attempt": 1}}, {"capacity": {"wait": -1}},
    {"capacity": {"wait": "2"}}, {"capacity_limit": {"limit": 4, "reason": "slot"}},
    {"capacity_limit": {"limit": 0, "reason": "quiet"}}])
def test_a_malformed_capacity_record_is_a_protocol_failure(capture, tmp_path, record):
    outcome, ledger, _ = launch(capture, limited(tmp_path, events=emit(record)), ADAPTIVE)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not ledger.accounting_complete


# Timeout


def waiting(wait, pause):
    """A session that announces one queued wait and then stays quiet for `pause` seconds."""
    return {"events": refusal(wait) + f"import time\ntime.sleep({pause})\n", "refusals": 1,
            "wait": wait}


def test_an_announced_queue_wait_is_credited_against_the_session_timeout(capture, tmp_path):
    # The session is quiet for longer than its timeout, inside a wait nare announced.
    outcome, ledger, attempt = launch(capture, native(tmp_path, **waiting(4.0, 2.5)), FIXED,
                                      timeout=1.5)
    assert (outcome.status, outcome.reason) == ("complete", "done")
    assert outcome.accounting_complete and ledger.accounting_complete
    assert outcome.capacity == CapacityUsage(1, 4.0, None, 0.0, "result")
    # The configured timeout is what the invocation records.
    assert load(attempt / "invocation.json")["timeout_seconds"] == 1.5


def test_a_completed_slot_wait_is_credited_from_its_record(capture, tmp_path):
    # 1.5 seconds waiting for a slot, reported once it ended, then 1.5 seconds of work.
    script = {"events": "import time\ntime.sleep(1.5)\n" + slot(4, 1.5) + "time.sleep(1.5)\n",
              "limit": 4, "slot_wait": 1.5}
    outcome, ledger, _ = launch(capture, native(tmp_path, **script), ADAPTIVE, timeout=2.0)
    assert (outcome.status, outcome.reason) == ("complete", "done")
    assert outcome.capacity == CapacityUsage(0, 0.0, 4, 1.5, "result")


@pytest.mark.parametrize("capacity,script", [
    # The same quiet period with no record is a hang, whatever the mode.
    (FIXED, {"events": "import time\ntime.sleep(2.5)\n"}),
    (ADAPTIVE, {"events": "import time\ntime.sleep(2.5)\n"}),
    # An announced wait shorter than the quiet period buys only itself.
    (FIXED, waiting(0.2, 2.5)),
    # One session is credited at most the configured wait, whatever nare announces.
    ({**FIXED, "wait_seconds": 0.2}, waiting(60.0, 2.5)),
    # A slot wait that was short does not cover a long silence after it.
    (ADAPTIVE, {"events": slot(4, 0.2) + "import time\ntime.sleep(2.5)\n", "limit": 4,
                "slot_wait": 0.2}),
    # Without the block a record earns nothing.
    (None, waiting(4.0, 2.5)),
])
def test_the_timeout_still_ends_a_session_beyond_its_credit(capture, tmp_path, capacity,
                                                            script):
    outcome, ledger, _ = launch(capture, native(tmp_path, **script), capacity, timeout=1.5)
    assert (outcome.status, outcome.reason) == ("failed", "timeout")
    assert not outcome.accounting_complete and not ledger.accounting_complete


def test_a_slot_wait_in_progress_is_not_credited(capture, tmp_path):
    # nare reports a slot wait only after it ends. A session still waiting at its deadline
    # looks like a hang, and is stopped with what its stream showed so far.
    script = {"events": "import time\ntime.sleep(3.0)\n" + slot(4, 3.0), "limit": 4,
              "slot_wait": 3.0}
    outcome, ledger, attempt = launch(capture, native(tmp_path, **script), ADAPTIVE,
                                      timeout=1.5)
    assert (outcome.status, outcome.reason) == ("failed", "timeout")
    assert not outcome.accounting_complete
    assert outcome.capacity == CapacityUsage(0, 0.0, None, 0.0, "events")
    assert load(attempt / "result.json")["capacity"]["source"] == "events"


def test_queue_and_slot_waits_share_one_bound():
    credit = module()._capacity_credit
    fixed = CapacitySettings("fixed", 10, 5)
    assert credit(fixed, 0.0, 0.0) == 0.0
    assert credit(fixed, 4.0, 0.0) == 4.0
    assert credit(fixed, 4.0, 3.0) == 7.0
    assert credit(fixed, 8.0, 3.0) == 10.0 == credit(fixed, 60.0, 60.0)
    assert credit(CapacitySettings(), 60.0, 60.0) == 0.0
