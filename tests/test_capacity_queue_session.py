"""Opt-in capacity queueing: argv, capability gating, accounting and the timeout credit."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_nare_flag_capability import advertised, inspect
from test_nare_session import RUN_BODY, contract3_capability, executable, flag_map, module, setup
from test_review_inputs import capture as capture

from scrutare.config import ModelRail, ModelSettings
from scrutare.engine.session_models import NareCapability, NareFlag, NareRuntime, TokenUsage

QUEUE_FLAGS = {
    "--capacity-queue": {"takes_value": False, "choices": None},
    "--capacity-wait-seconds": {"takes_value": True, "choices": None},
    "--capacity-refusals": {"takes_value": True, "choices": None},
    # Adaptive capacity is advertised by the pinned release and never sent.
    "--capacity-adaptive": {"takes_value": False, "choices": None},
    "--capacity-ceiling": {"takes_value": True, "choices": None},
    "--capacity-shared-dir": {"takes_value": True, "choices": None},
}
# The contract entry as nare 2026.11.100 prints it.
ENTRY = {
    "flags": {"enable": "--capacity-queue", "wait_seconds": "--capacity-wait-seconds",
              "refusals": "--capacity-refusals"},
    "env": {"enable": "NARE_CAPACITY_QUEUE", "wait_seconds": "NARE_CAPACITY_WAIT_SECONDS",
            "refusals": "NARE_CAPACITY_REFUSALS"},
    "default": {"enable": False, "wait_seconds": 1800.0, "refusals": 50},
    "per": "model_request", "providers": ["openai"], "event": "progress",
    "event_detail": "capacity", "result_field": "capacity_refusals",
    "session_field": "capacity_refusals",
}
QUEUE = ("--capacity-queue", "--capacity-wait-seconds", "--capacity-refusals")


def refusal(wait, attempt=1):
    """One queued refusal as nare writes it, before it sleeps for the announced wait."""
    record = {"type": "progress", "text": "capacity refusal (HTTP 429, max_parallel_requests); "
              f"queued {attempt} for {wait:g}s, then resent unchanged",
              "detail": {"capacity": {"attempt": attempt, "wait": wait, "waited": wait,
                                      "refusals": attempt, "refusals_limit": 50, "status": 429,
                                      "reason": "max_parallel_requests"}},
              "timestamp": "now"}
    return f"print(json.dumps({record!r}), flush=True)\n"


def capability(*, without=(), extra=None, **fields):
    fields.setdefault("capacity_queue", ("openai",))
    return contract3_capability(without=without, extra={**QUEUE_FLAGS, **(extra or {})},
                                **fields)


def queue_executable(tmp_path, *, extra="", refusals=0, saved=None):
    reported = "" if refusals is None else f'"capacity_refusals": {refusals}, '
    kept = reported if saved is None else f'"capacity_refusals": {saved}, '
    body = (RUN_BODY.replace("INPUT", "4").replace("STATUS", "'done'")
            .replace("STOP", "'end_turn'").replace("OUTPUT", "{'findings': []}")
            .replace("EXTRA", extra).replace("EXIT", "0")
            .replace('"contract": 1', '"contract": 3').replace('"2026.10.0"', '"2026.11.100"')
            .replace('a = parser.parse_args()',
                     'for flag in ("max-cutoffs", "request-retries", "max-corrections",\n'
                     '             "compaction", "compaction-trigger", "compaction-target",\n'
                     '             "capacity-wait-seconds", "capacity-refusals"):\n'
                     '    parser.add_argument("--" + flag)\n'
                     'for flag in ("stream", "capacity-queue"):\n'
                     '    parser.add_argument("--" + flag, action="store_true")\n'
                     'a = parser.parse_args()')
            .replace('result = {"type": "result",', 'result = {' + reported
                     + '"capacity_wait_seconds": 0.0, "capacity_limit": None, '
                     '"capacity_slot_wait_seconds": 0.0, "type": "result",')
            .replace('saved = {"id": "stable-id",', 'saved = {' + kept + '"id": "stable-id",'))
    return executable(tmp_path, version="2026.11.100", contract=3, body=body)


def run(capture, tmp_path, rail, found=None, *, timeout=5, **script):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=attempt,
        runtime=NareRuntime(queue_executable(tmp_path, **script), 7, timeout),
        capability=capability(version="2026.11.100") if found is None else found))
    return outcome, attempt, ledger


def load(path):
    return json.loads(path.read_bytes())


def flag_values(argv, flag):
    return [argv[index + 1] for index, item in enumerate(argv) if item == flag]


def test_a_rail_that_does_not_ask_sends_what_it_sent_before_the_queue_existed(
        capture, tmp_path):
    # The same rail against the previous pin's flags and against the new pin's.
    rail = ModelRail("openai", "https://gateway.invalid/v1", "m")
    before, after = contract3_capability(), capability(version="2026.11.100")
    assert module()._rail_argv(rail, before) == module()._rail_argv(rail, after)
    assert module()._rail_options(rail) == []
    outcome, attempt, _ = run(capture, tmp_path, rail)
    assert outcome.status == "complete" and outcome.accounting_complete
    invocation, result = load(attempt / "invocation.json"), load(attempt / "result.json")
    assert not any(item.startswith("--capacity") for item in invocation["argv"])
    assert invocation["rail"] == {"provider": "openai", "model": "m",
                                  "base_url": "https://gateway.invalid/v1"}
    assert "capacity_queue" not in result


@pytest.mark.parametrize(("fields", "seconds", "refusals"), [
    ({}, "1800", "50"),
    ({"capacity_wait_seconds": 7.5}, "7.5", "50"),
    ({"capacity_refusals": 3}, "1800", "3"),
    ({"capacity_wait_seconds": 900, "capacity_refusals": 12}, "900", "12"),
])
def test_a_queued_rail_passes_the_switch_and_both_limits_explicitly(
        capture, tmp_path, fields, seconds, refusals):
    rail = ModelRail("openai", None, "m", capacity_queue=True, **fields)
    outcome, attempt, _ = run(capture, tmp_path, rail)
    assert outcome.status == "complete" and outcome.accounting_complete
    invocation, result = load(attempt / "invocation.json"), load(attempt / "result.json")
    argv = invocation["argv"]
    assert argv.count("--capacity-queue") == 1
    assert flag_values(argv, "--capacity-wait-seconds") == [seconds]
    assert flag_values(argv, "--capacity-refusals") == [refusals]
    # The retry policy is untouched: a queued refusal is not a resend.
    assert flag_values(argv, "--request-retries") == ["0"]
    assert invocation["rail"] == rail.record() == {
        "provider": "openai", "model": "m", "base_url": None, "capacity_queue": True, **fields}
    assert result["capacity_queue"] == {
        "enabled": True, "wait_seconds": float(seconds), "refusals_limit": int(refusals),
        "queued": 0, "waited_seconds": 0.0}
    assert load(attempt / "cwd/observed.json")["argv"] == argv[1:]


def test_queued_refusals_add_no_usage_and_are_recorded(capture, tmp_path):
    rail = ModelRail("openai", None, "m", capacity_queue=True)
    outcome, attempt, ledger = run(capture, tmp_path, rail, refusals=2,
                                   extra=refusal(0.25) + refusal(0.5, 2))
    assert outcome.status == "complete" and outcome.accounting_complete
    # Two refused requests were never dispatched: one cost event, counted once.
    assert outcome.usage == TokenUsage(4, 2, 3, 1) == ledger.usage
    assert load(attempt / "result.json")["capacity_queue"] == {
        "enabled": True, "wait_seconds": 1800.0, "refusals_limit": 50,
        "queued": 2, "waited_seconds": 0.75}


@pytest.mark.parametrize(("events", "reported", "saved", "seen"), [
    (refusal(0.1), 0, None, 1), ("", 1, None, 0), (refusal(0.1), None, None, 1),
    (refusal(0.1), 1, 0, 1)])
def test_queue_evidence_that_disagrees_with_the_result_is_a_protocol_failure(
        capture, tmp_path, events, reported, saved, seen):
    rail = ModelRail("openai", None, "m", capacity_queue=True)
    outcome, attempt, _ = run(capture, tmp_path, rail, extra=events, refusals=reported,
                              saved=saved)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not outcome.accounting_complete
    # The retained stream is still counted for the record.
    assert load(attempt / "result.json")["capacity_queue"]["queued"] == seen


@pytest.mark.parametrize("rail", [ModelRail("openai", None, "m"),
                                  ModelRail("openai", None, "m", capacity_queue=False)])
def test_a_queued_refusal_on_a_session_that_did_not_ask_is_a_protocol_failure(
        capture, tmp_path, rail):
    # nare was not told to queue, so the record is not this session's.
    outcome, attempt, _ = run(capture, tmp_path, rail, extra=refusal(0.1), refusals=1)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not any(item.startswith("--capacity")
                   for item in load(attempt / "invocation.json")["argv"])


@pytest.mark.parametrize("record", [
    "[]", '"queued"', '{"attempt": 1}', '{"wait": -1}', '{"wait": "2"}', '{"wait": True}',
    '{"wait": float("inf")}'])
def test_a_malformed_queue_record_is_a_protocol_failure(capture, tmp_path, record):
    event = ('print(json.dumps({"type": "progress", "text": "", "detail": {"capacity": '
             + record + '}, "timestamp": "now"}), flush=True)\n')
    rail = ModelRail("openai", None, "m", capacity_queue=True)
    outcome, _, ledger = run(capture, tmp_path, rail, extra=event, refusals=1)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not ledger.accounting_complete


def test_a_limit_record_from_adaptive_capacity_changes_nothing(capture, tmp_path):
    # Scrutare never asks for the adaptive limit; its records carry no accounting.
    event = ('print(json.dumps({"type": "progress", "text": "", "detail": {"capacity_limit": '
             '{"limit": 4, "previous": 8, "reason": "refusal"}}, "timestamp": "now"}), '
             'flush=True)\n')
    rail = ModelRail("openai", None, "m", capacity_queue=True)
    outcome, attempt, _ = run(capture, tmp_path, rail, extra=event)
    assert outcome.status == "complete" and outcome.accounting_complete
    assert load(attempt / "result.json")["capacity_queue"]["queued"] == 0


QUEUED = ModelRail("openai", None, "m", capacity_queue=True)


@pytest.mark.parametrize("found", [
    NareCapability("2026.10.12", 2),
    contract3_capability(),
    capability(capacity_queue=()),
    capability(capacity_queue=("anthropic",)),
    capability(without=("--capacity-queue",)),
    capability(without=("--capacity-wait-seconds",)),
    capability(without=("--capacity-refusals",)),
    capability(extra={"--capacity-queue": {"takes_value": True, "choices": None}}),
    capability(extra={"--capacity-refusals": {"takes_value": False, "choices": None}}),
    # Only a streamed session writes the queue record before it waits.
    capability(without=("--stream",)),
])
def test_an_unadvertised_queue_is_refused_for_the_wave_and_the_session(
        capture, tmp_path, found):
    with pytest.raises(module().SessionRuntimeError,
                       match="^capability: nare .* cannot send capacity_queue"):
        module().require_rail_options(ModelSettings(QUEUED), found)
    outcome, attempt, _ = run(capture, tmp_path, QUEUED, found)
    assert (outcome.status, outcome.reason) == ("failed", "invocation")
    assert not (attempt / "stdout.jsonl").exists()


def test_a_queue_turned_off_needs_nothing_from_the_runtime(capture, tmp_path):
    rail = ModelRail("openai", None, "m", capacity_queue=False)
    # A runtime that lists none of the queue flags.
    found = contract3_capability(version="2026.11.100")
    module().require_rail_options(ModelSettings(rail), found)
    outcome, attempt, _ = run(capture, tmp_path, rail, found)
    assert outcome.status == "complete"
    assert load(attempt / "invocation.json")["rail"]["capacity_queue"] is False
    assert load(attempt / "result.json")["capacity_queue"] == {"enabled": False}


def test_inspection_reads_the_rails_that_queue(tmp_path):
    flags = flag_map(extra=QUEUE_FLAGS)
    found = inspect(advertised(tmp_path, flags=flags, capacity_queue=ENTRY))
    assert found.capacity_queue == ("openai",)
    assert found.flag("--capacity-queue") == NareFlag("--capacity-queue", False)
    # Additive fields in the entry are tolerated; a different shape advertises nothing.
    assert inspect(advertised(tmp_path, flags=flags, capacity_queue={
        **ENTRY, "added_later": True})).capacity_queue == ("openai",)
    for entries in (
            {"flags": flag_map(extra=QUEUE_FLAGS, without=("--capacity-queue",)),
             "capacity_queue": ENTRY},
            {"flags": flag_map(extra=QUEUE_FLAGS, without=("--capacity-refusals",)),
             "capacity_queue": ENTRY},
            {"flags": flag_map(), "capacity_queue": ENTRY},
            {"flags": flags, "capacity_queue": {**ENTRY, "providers": "openai"}},
            {"flags": flags, "capacity_queue": {**ENTRY, "providers": ["bedrock"]}},
            {"flags": flags, "capacity_queue": {**ENTRY, "event_detail": "other"}},
            {"flags": flags, "capacity_queue": {**ENTRY, "result_field": "other"}},
            {"flags": flags, "capacity_queue": {**ENTRY, "flags": {"enable": "--other"}}},
            {"flags": flags, "capacity_queue": "--capacity-queue"},
            {"flags": flags}):
        assert inspect(advertised(tmp_path, **entries)).capacity_queue == ()
    assert replace(found, capacity_queue=()).capacity_queue == ()
    # Contracts 1 and 2 never queue, whatever they print.
    assert inspect(advertised(tmp_path, version="2026.10.12", contract=2, flags=flags,
                              capacity_queue=ENTRY)).capacity_queue == ()


def waiting(wait, pause):
    """A session that announces one queued wait and then stays quiet for `pause` seconds."""
    return refusal(wait) + f"import time\ntime.sleep({pause})\n"


def test_an_announced_queue_wait_is_credited_against_the_session_timeout(capture, tmp_path):
    # The session is quiet for longer than its timeout, inside a wait nare announced.
    outcome, attempt, ledger = run(capture, tmp_path, QUEUED, timeout=1.5, refusals=1,
                                   extra=waiting(4.0, 2.5))
    assert (outcome.status, outcome.reason) == ("complete", "done")
    assert outcome.accounting_complete and ledger.accounting_complete
    assert load(attempt / "result.json")["capacity_queue"]["waited_seconds"] == 4.0
    # The configured timeout is what the invocation records; the credit is in the result.
    assert load(attempt / "invocation.json")["timeout_seconds"] == 1.5


@pytest.mark.parametrize(("rail", "script"), [
    # The same quiet period with no announced wait is a hang, queue or no queue.
    (QUEUED, {"extra": "import time\ntime.sleep(2.5)\n"}),
    # An announced wait shorter than the quiet period buys only itself.
    (QUEUED, {"extra": waiting(0.2, 2.5), "refusals": 1}),
    # One session is credited at most its rail's bound, whatever nare announces.
    (replace(QUEUED, capacity_wait_seconds=0.2), {"extra": waiting(60.0, 2.5), "refusals": 1}),
    # A session that did not ask for the queue earns nothing from such a record.
    (ModelRail("openai", None, "m"), {"extra": waiting(4.0, 2.5), "refusals": 1}),
])
def test_the_timeout_still_ends_a_session_beyond_its_credit(capture, tmp_path, rail, script):
    outcome, _, ledger = run(capture, tmp_path, rail, timeout=1.5, **script)
    assert (outcome.status, outcome.reason) == ("failed", "timeout")
    assert not outcome.accounting_complete and not ledger.accounting_complete
