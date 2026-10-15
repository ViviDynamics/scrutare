"""Contract 1 and 2 runtimes, and the advertised structured-output capability."""

import asyncio
import json

import pytest
from test_nare_session import executable, module, run, running_executable, setup
from test_review_inputs import capture as capture

from scrutare.engine.session_models import NareCapability, NareRuntime

STRUCTURED = {"flag": "--structured-output", "modes": ["json_schema"], "requires": "--schema",
              "providers": ["openai"], "constrained_turns": ["schema_correction", "tool_free_run"],
              "event": "progress", "event_detail": "structured_output"}


def advertised(tmp_path, *, version, contract, structured=None):
    path = executable(tmp_path, version=version, contract=contract)
    if structured is not None:
        path.write_text(path.read_text().replace(
            f'"nare": {version!r}}}',
            f'"nare": {version!r}, "structured_output": {structured!r}}}'))
    return path


def inspect(path):
    return asyncio.run(module().inspect_nare_runtime(NareRuntime(path)))


@pytest.mark.parametrize(("version", "contract"), [
    ("2026.10.0", 1), ("2026.10.6", 1), ("2026.10.7", 2), ("2026.10.12", 2)])
def test_each_contract_is_accepted_from_the_release_that_speaks_it(tmp_path, version, contract):
    capability = inspect(advertised(tmp_path, version=version, contract=contract))
    assert capability == NareCapability(version, contract)
    assert capability.structured_output == ()


@pytest.mark.parametrize(("version", "contract"), [
    ("2026.10.6", 2), ("2026.10.7", 1), ("2026.10.12", 3), ("2026.10.12", True)])
def test_a_contract_its_release_does_not_speak_is_refused(tmp_path, version, contract):
    with pytest.raises(module().SessionRuntimeError, match="^contract:"):
        inspect(advertised(tmp_path, version=version, contract=contract))


def test_the_advertised_structured_output_modes_are_read(tmp_path):
    capability = inspect(advertised(tmp_path, version="2026.10.12", contract=2,
                                    structured=STRUCTURED))
    assert capability == NareCapability("2026.10.12", 2, ("json_schema",))


@pytest.mark.parametrize("structured", [
    "json_schema", {**STRUCTURED, "modes": "json_schema"},
    {**STRUCTURED, "flag": "--other"}, {**STRUCTURED, "providers": ["anthropic"]}])
def test_a_malformed_or_foreign_structured_output_entry_is_not_a_capability(tmp_path, structured):
    path = advertised(tmp_path, version="2026.10.12", contract=2, structured=structured)
    if isinstance(structured, dict) and isinstance(structured["modes"], list):
        assert inspect(path).structured_output == ()
    else:
        with pytest.raises(module().SessionRuntimeError, match="^contract:"):
            inspect(path)


def streaming(path):
    path.write_text(path.read_text().replace(
        'a = parser.parse_args()',
        'parser.add_argument("--stream", action="store_true")\na = parser.parse_args()'))
    return path


def contract2(path):
    path.write_text(path.read_text().replace('"contract": 1', '"contract": 2')
                    .replace('"2026.10.0"', '"2026.10.12"'))
    return streaming(path)


def test_a_contract_2_session_speaks_the_inspected_contract(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = contract2(running_executable(tmp_path))
    outcome = asyncio.run(module().run_persona_session(
        descriptor, __import__("scrutare.config").config.ModelRail("openai", None, "m"), lease,
        ledger=ledger, artifact_directory=attempt, runtime=NareRuntime(path, 7, 5),
        capability=NareCapability("2026.10.12", 2)))
    assert outcome.status == "complete"
    args = json.loads((attempt / "cwd" / "observed.json").read_bytes())["argv"]
    assert args[args.index("--contract") + 1] == "2"
    invocation = json.loads((attempt / "invocation.json").read_bytes())
    assert invocation["contract"] == 2 and invocation["nare_version"] == "2026.10.12"


def test_contract_1_evidence_under_a_contract_2_capability_fails(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = streaming(running_executable(tmp_path))
    path.write_text(path.read_text().replace('"2026.10.0"', '"2026.10.12"'))
    outcome = asyncio.run(module().run_persona_session(
        descriptor, __import__("scrutare.config").config.ModelRail("openai", None, "m"), lease,
        ledger=ledger, artifact_directory=attempt, runtime=NareRuntime(path, 7, 5),
        capability=NareCapability("2026.10.12", 2)))
    assert outcome.status == "failed" and outcome.reason == "protocol"


def test_contract_1_sessions_are_unchanged(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(tmp_path))
    assert outcome.status == "complete"
    args = json.loads((attempt / "cwd" / "observed.json").read_bytes())["argv"]
    assert args[args.index("--contract") + 1] == "1"
