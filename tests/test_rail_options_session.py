"""Opt-in rail options reach nare only when asked for and advertised, and are recorded."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_nare_session import RUN_BODY, contract3_capability, executable, module, setup
from test_review_inputs import capture as capture

from scrutare.config import ModelRail, ModelSettings
from scrutare.engine.session_models import NareCapability, NareFlag, NareRuntime, TokenUsage

EXTRA_FLAGS = {
    "--request-retry-seconds": {"takes_value": True, "choices": None},
    "--effort": {"takes_value": True, "choices": ["low", "medium", "high", "xhigh", "max"]},
}
RETRY = ('print(json.dumps({"type": "progress", "text": "model request failed (status); retry", '
         '"detail": {"retry": {"attempt": 1, "retries": 2, "reason": "status", "status": 503, '
         '"wait": 2.0, "error": "HTTPStatusError"}}, "timestamp": "now"}), flush=True)\n')


def capability(*, without=(), extra=None, **fields):
    fields.setdefault("cache_breakpoints", ("openai",))
    return contract3_capability(without=without, extra={**EXTRA_FLAGS, **(extra or {})},
                                **fields)


def option_executable(tmp_path, *, extra="", retries=None, effort="a.effort"):
    result_retries = "" if retries is None else f'"retries": {retries}, '
    body = (RUN_BODY.replace("INPUT", "4").replace("STATUS", "'done'")
            .replace("STOP", "'end_turn'").replace("OUTPUT", "{'findings': []}")
            .replace("EXTRA", extra).replace("EXIT", "0")
            .replace('"contract": 1', '"contract": 3').replace('"2026.10.0"', '"2026.11.94"')
            .replace('a = parser.parse_args()',
                     'for flag in ("max-cutoffs", "request-retries", "max-corrections",\n'
                     '             "compaction", "compaction-trigger", "compaction-target",\n'
                     '             "request-retry-seconds", "effort", "structured-output"):\n'
                     '    parser.add_argument("--" + flag)\n'
                     'for flag in ("stream", "cache-breakpoints"):\n'
                     '    parser.add_argument("--" + flag, action="store_true")\n'
                     'a = parser.parse_args()')
            .replace('result = {"type": "result",', 'result = {' + result_retries
                     + '"type": "result",')
            .replace('saved = {"id": "stable-id",', 'saved = {' + result_retries
                     + f'"effort": {effort}, "id": "stable-id",'))
    return executable(tmp_path, version="2026.11.94", contract=3, body=body)


def run(capture, tmp_path, rail, found=None, **script):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=attempt,
        runtime=NareRuntime(option_executable(tmp_path, **script), 7, 5),
        capability=capability() if found is None else found))
    return outcome, attempt, ledger


def load(path):
    return json.loads(path.read_bytes())


def flag_values(argv, flag):
    return [argv[index + 1] for index, item in enumerate(argv) if item == flag]


def test_unset_options_keep_the_pinned_invocation_and_records(capture, tmp_path):
    outcome, attempt, _ = run(capture, tmp_path, ModelRail("openai", None, "m"))
    assert outcome.status == "complete"
    invocation, result = load(attempt / "invocation.json"), load(attempt / "result.json")
    assert flag_values(invocation["argv"], "--request-retries") == ["0"]
    assert not {"--request-retry-seconds", "--cache-breakpoints", "--effort"} & set(
        invocation["argv"])
    assert invocation["rail"] == {"provider": "openai", "model": "m", "base_url": None}
    assert not {"request_retries", "cache_breakpoints", "effort"} & set(result)


@pytest.mark.parametrize(("fields", "retries", "seconds"), [
    ({"request_retries": 2}, "2", ["60"]),
    ({"request_retries": 2, "request_retry_seconds": 7.5}, "2", ["7.5"]),
    ({"request_retries": 5, "request_retry_seconds": 120}, "5", ["120"]),
    ({"request_retries": 0}, "0", []),
])
def test_request_retries_replace_the_pinned_zero(capture, tmp_path, fields, retries, seconds):
    rail = ModelRail("openai", None, "m", **fields)
    outcome, attempt, _ = run(capture, tmp_path, rail, retries=0)
    assert outcome.status == "complete" and outcome.accounting_complete
    invocation, result = load(attempt / "invocation.json"), load(attempt / "result.json")
    argv = invocation["argv"]
    assert flag_values(argv, "--request-retries") == [retries]
    assert flag_values(argv, "--request-retry-seconds") == seconds
    assert invocation["rail"] == rail.record() == {
        "provider": "openai", "model": "m", "base_url": None, **fields}
    assert result["request_retries"] == {"limit": fields["request_retries"], "retried": 0}
    assert load(attempt / "cwd/observed.json")["argv"] == argv[1:]


def test_a_resent_request_is_counted_once_and_its_retries_recorded(capture, tmp_path):
    rail = ModelRail("openai", None, "m", request_retries=2)
    outcome, attempt, ledger = run(capture, tmp_path, rail, extra=RETRY + RETRY, retries=2)
    assert outcome.status == "complete" and outcome.accounting_complete
    # Two resends reported no usage of their own: one cost event, counted once.
    assert outcome.usage == TokenUsage(4, 2, 3, 1) == ledger.usage
    assert load(attempt / "result.json")["request_retries"] == {"limit": 2, "retried": 2}


@pytest.mark.parametrize(("events", "reported", "seen"), [
    (RETRY, 0, 1), ("", 1, 0), (RETRY, None, 1)])
def test_retry_evidence_that_disagrees_with_the_result_is_a_protocol_failure(
        capture, tmp_path, events, reported, seen):
    rail = ModelRail("openai", None, "m", request_retries=2)
    outcome, attempt, _ = run(capture, tmp_path, rail, extra=events, retries=reported)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not outcome.accounting_complete
    # The retained stream is still counted for the record.
    assert load(attempt / "result.json")["request_retries"] == {"limit": 2, "retried": seen}


@pytest.mark.parametrize("rail", [ModelRail("openai", None, "m"),
                                  ModelRail("openai", None, "m", request_retries=0)])
def test_a_retry_on_a_session_that_asked_for_none_is_a_protocol_failure(
        capture, tmp_path, rail):
    outcome, _, _ = run(capture, tmp_path, rail, extra=RETRY, retries=1)
    assert (outcome.status, outcome.reason) == ("failed", "protocol")


@pytest.mark.parametrize(("rail", "found"), [
    (ModelRail("openai", None, "m", request_retries=1), NareCapability("2026.10.12", 2)),
    (ModelRail("openai", None, "m", request_retries=1),
     capability(without=("--request-retry-seconds",))),
    (ModelRail("openai", None, "m", cache_breakpoints=True),
     capability(without=("--cache-breakpoints",))),
    (ModelRail("openai", None, "m", cache_breakpoints=True), capability(cache_breakpoints=())),
    (ModelRail("openai", None, "m", cache_breakpoints=True),
     capability(extra={"--cache-breakpoints": {"takes_value": True, "choices": None}})),
    (ModelRail("openai", None, "m", effort="high"), capability(without=("--effort",))),
    (ModelRail("openai", None, "m", effort="max"),
     capability(extra={"--effort": {"takes_value": True, "choices": ["low", "high"]}})),
    (ModelRail("anthropic", None, "m", effort="high"), NareCapability("2026.10.12", 2)),
    (ModelRail("anthropic", None, "m", structured_output="json_schema"),
     capability(structured_output=("json_schema",))),
    (ModelRail("anthropic", None, "m", structured_output="json_schema"),
     NareCapability("2026.10.12", 2, ("json_schema",))),
])
def test_an_unadvertised_option_is_refused_for_the_wave_and_the_session(
        capture, tmp_path, rail, found):
    with pytest.raises(module().SessionRuntimeError, match="^capability: nare .* cannot send"):
        module().require_rail_options(ModelSettings(rail), found)
    outcome, attempt, _ = run(capture, tmp_path, rail, found)
    assert (outcome.status, outcome.reason) == ("failed", "invocation")
    assert not (attempt / "stdout.jsonl").exists()


def test_a_zero_retry_setting_needs_no_retry_wait_flag(capture, tmp_path):
    rail = ModelRail("openai", None, "m", request_retries=0)
    found = capability(without=("--request-retry-seconds",))
    module().require_rail_options(ModelSettings(rail), found)
    outcome, _, _ = run(capture, tmp_path, rail, found, retries=0)
    assert outcome.status == "complete"


@pytest.mark.parametrize(("enabled", "count"), [(True, 1), (False, 0)])
def test_cache_breakpoints_are_a_switch_sent_only_when_true(capture, tmp_path, enabled, count):
    rail = ModelRail("openai", None, "m", cache_breakpoints=enabled)
    # Turning it off needs nothing from the runtime.
    found = capability() if enabled else capability(without=("--cache-breakpoints",),
                                                    cache_breakpoints=())
    outcome, attempt, _ = run(capture, tmp_path, rail, found)
    assert outcome.status == "complete"
    invocation = load(attempt / "invocation.json")
    assert invocation["argv"].count("--cache-breakpoints") == count
    assert invocation["rail"]["cache_breakpoints"] is enabled
    assert load(attempt / "result.json")["cache_breakpoints"] == {"enabled": enabled}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_effort_is_sent_and_recorded_on_either_rail(capture, tmp_path, provider):
    rail = ModelRail(provider, None, "m", effort="xhigh")
    outcome, attempt, _ = run(capture, tmp_path, rail)
    assert outcome.status == "complete"
    invocation = load(attempt / "invocation.json")
    assert flag_values(invocation["argv"], "--effort") == ["xhigh"]
    assert invocation["rail"]["effort"] == "xhigh"
    assert load(attempt / "result.json")["effort"] == {"level": "xhigh"}


def test_a_session_file_that_records_another_effort_is_a_protocol_failure(capture, tmp_path):
    rail = ModelRail("openai", None, "m", effort="xhigh")
    outcome, _, _ = run(capture, tmp_path, rail, effort="'low'")
    assert (outcome.status, outcome.reason) == ("failed", "protocol")


def test_the_anthropic_rail_asks_for_the_constraint_where_nare_lists_it(capture, tmp_path):
    rail = ModelRail("anthropic", None, "m", structured_output="json_schema")
    found = capability(structured_output=("json_schema",),
                       structured_output_providers=("anthropic", "openai"))
    module().require_rail_options(ModelSettings(rail), found)
    outcome, attempt, _ = run(capture, tmp_path, rail, found)
    assert outcome.status == "complete"
    invocation = load(attempt / "invocation.json")
    assert flag_values(invocation["argv"], "--structured-output") == ["json_schema"]
    assert "--stream" not in invocation["argv"]
    assert load(attempt / "result.json")["structured_output"] == {
        "mode": "json_schema", "constrained_turns": 0}


def test_inspection_reads_the_cache_breakpoint_rails(tmp_path):
    from test_nare_flag_capability import advertised, inspect
    from test_nare_session import flag_map

    entry = {"flag": "--cache-breakpoints", "env": "NARE_CACHE_BREAKPOINTS",
             "providers": ["openai"], "default": False, "marker": {"type": "ephemeral"}}
    found = inspect(advertised(tmp_path, flags=flag_map(), cache_breakpoints=entry))
    assert found.cache_breakpoints == ("openai",)
    assert found.flag("--cache-breakpoints") == NareFlag("--cache-breakpoints", False)
    for entries in ({"flags": flag_map(without=("--cache-breakpoints",)),
                     "cache_breakpoints": entry},
                    {"flags": flag_map(), "cache_breakpoints": {**entry, "flag": "--other"}},
                    {"flags": flag_map(), "cache_breakpoints": {**entry, "providers": "openai"}},
                    {"flags": flag_map()}):
        assert inspect(advertised(tmp_path, **entries)).cache_breakpoints == ()
    assert replace(found, cache_breakpoints=()).cache_breakpoints == ()
