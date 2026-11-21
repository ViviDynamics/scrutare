"""One nare session on a rail that opts in to schema-constrained answers."""

import asyncio
import json

import pytest
from test_nare_contract_capability import contract2
from test_nare_session import module, running_executable, setup
from test_review_inputs import capture as capture

from scrutare.config import ModelRail
from scrutare.engine.session_models import NareCapability, NareRuntime

CAPABLE = NareCapability("2026.10.12", 2, ("json_schema",))
ON = ModelRail("openai", None, "m", "json_schema")
OFF = ModelRail("openai", None, "m")


def marker(mode="json_schema", withheld=True):
    detail = {"structured_output": {"mode": mode, "tools_withheld": withheld}}
    return ("print(json.dumps({'type': 'progress', 'text': 'schema-constrained answer turn', "
            f"'detail': {detail!r}, 'timestamp': 'now'}}), flush=True)\n")


def structured(tmp_path, *, markers=1, extra="", **kwargs):
    path = contract2(running_executable(tmp_path, extra=extra + marker() * markers, **kwargs))
    path.write_text(path.read_text().replace(
        'a = parser.parse_args()',
        'parser.add_argument("--structured-output")\na = parser.parse_args()'))
    return path


def launch(capture, path, rail, capability=CAPABLE):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=attempt,
        runtime=NareRuntime(path, 7, 5), capability=capability))
    return outcome, attempt


def recorded(attempt, name):
    return json.loads((attempt / name).read_bytes())


def test_an_enabled_rail_requests_the_constraint_and_records_it(capture, tmp_path):
    outcome, attempt = launch(capture, structured(tmp_path), ON)
    assert outcome.status == "complete"
    argv = recorded(attempt / "cwd", "observed.json")["argv"]
    assert argv[-2:] == ["--structured-output", "json_schema"]
    invocation = recorded(attempt, "invocation.json")
    assert invocation["argv"][-2:] == ["--structured-output", "json_schema"]
    assert invocation["rail"] == {"provider": "openai", "model": "m", "base_url": None,
                                  "structured_output": "json_schema"}
    assert recorded(attempt, "result.json")["structured_output"] == {
        "mode": "json_schema", "constrained_turns": 1}


def test_a_valid_first_answer_records_zero_constrained_turns(capture, tmp_path):
    outcome, attempt = launch(capture, structured(tmp_path, markers=0), ON)
    assert outcome.status == "complete"
    assert recorded(attempt, "result.json")["structured_output"] == {
        "mode": "json_schema", "constrained_turns": 0}


def test_a_failed_constrained_session_still_records_its_constrained_turns(capture, tmp_path):
    path = structured(tmp_path, status="error", stop="schema_violation", exit_code=1)
    outcome, attempt = launch(capture, path, ON)
    assert outcome.status == "failed" and outcome.reason == "provider"
    assert recorded(attempt, "result.json")["structured_output"] == {
        "mode": "json_schema", "constrained_turns": 1}


def test_off_is_unchanged_on_a_capable_runtime(capture, tmp_path):
    outcome, attempt = launch(capture, structured(tmp_path, markers=0), OFF)
    assert outcome.status == "complete"
    assert "--structured-output" not in recorded(attempt / "cwd", "observed.json")["argv"]
    invocation = recorded(attempt, "invocation.json")
    assert "--structured-output" not in invocation["argv"]
    assert invocation["rail"] == {"provider": "openai", "model": "m", "base_url": None}
    assert "structured_output" not in recorded(attempt, "result.json")


def test_a_constrained_turn_on_an_unconstrained_session_is_a_protocol_failure(capture, tmp_path):
    outcome, attempt = launch(capture, structured(tmp_path), OFF)
    assert outcome.status == "failed" and outcome.reason == "protocol"
    assert "structured_output" not in recorded(attempt, "result.json")


@pytest.mark.parametrize("extra", [marker(mode="grammar"), marker(withheld="yes")])
def test_a_malformed_constrained_turn_record_is_a_protocol_failure(capture, tmp_path, extra):
    outcome, _ = launch(capture, structured(tmp_path, markers=0, extra=extra), ON)
    assert outcome.status == "failed" and outcome.reason == "protocol"


def test_a_runtime_without_the_capability_never_launches(capture, tmp_path):
    outcome, attempt = launch(capture, structured(tmp_path), ON,
                              capability=NareCapability("2026.10.12", 2))
    assert outcome.status == "failed" and outcome.reason == "invocation"
    assert outcome.accounting_complete
    assert not (attempt / "cwd" / "observed.json").exists()


def test_additive_fields_in_a_constrained_turn_record_are_ignored(capture, tmp_path):
    extra = ("print(json.dumps({'type': 'progress', 'text': 'x', 'timestamp': 'now', 'detail': "
             "{'structured_output': {'mode': 'json_schema', 'tools_withheld': True, "
             "'future': 1}}}), flush=True)\n")
    outcome, attempt = launch(capture, structured(tmp_path, markers=0, extra=extra), ON)
    assert outcome.status == "complete"
    assert recorded(attempt, "result.json")["structured_output"]["constrained_turns"] == 1


ENCODING_CAPABLE = NareCapability(
    "2026.10.12", 2, ("json_schema", "json_object", "tool_call"),
    structured_output_provider_modes=(("openai", ("json_schema", "json_object", "tool_call")),))


def encoded(tmp_path, mode, *, markers=1, marked=None):
    return structured(tmp_path, markers=0, extra=marker(mode=marked or mode) * markers)


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_an_openai_encoding_is_sent_and_recorded_like_json_schema(capture, tmp_path, mode):
    rail = ModelRail("openai", None, "m", mode)
    outcome, attempt = launch(capture, encoded(tmp_path, mode), rail, ENCODING_CAPABLE)
    assert outcome.status == "complete"
    assert recorded(attempt / "cwd", "observed.json")["argv"][-2:] == [
        "--structured-output", mode]
    invocation = recorded(attempt, "invocation.json")
    assert invocation["argv"][-2:] == ["--structured-output", mode]
    assert invocation["rail"]["structured_output"] == mode
    assert recorded(attempt, "result.json")["structured_output"] == {
        "mode": mode, "constrained_turns": 1}


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_a_constrained_turn_in_another_encoding_is_a_protocol_failure(capture, tmp_path, mode):
    rail = ModelRail("openai", None, "m", mode)
    outcome, _ = launch(capture, encoded(tmp_path, mode, marked="json_schema"), rail,
                        ENCODING_CAPABLE)
    assert outcome.status == "failed" and outcome.reason == "protocol"


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_a_json_schema_only_runtime_never_launches_an_encoding(capture, tmp_path, mode):
    outcome, attempt = launch(capture, encoded(tmp_path, mode),
                              ModelRail("openai", None, "m", mode))
    assert outcome.status == "failed" and outcome.reason == "invocation"
    assert not (attempt / "cwd" / "observed.json").exists()
