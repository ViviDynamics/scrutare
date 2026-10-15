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
