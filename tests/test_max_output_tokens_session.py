"""One nare session on a rail with a per-request output token cap, and the wave manifest."""

import asyncio
import json

import pytest
from test_nare_cli_integration import FINDING
from test_nare_contract_capability import contract2
from test_nare_session import module, running_executable, setup
from test_review_inputs import capture as capture
from test_structured_output_session import marker
from test_structured_output_wave import configure, wave

from scrutare.config import ModelRail
from scrutare.engine.session_models import NareCapability, NareRuntime, TokenUsage

RUNTIME = NareCapability("2026.10.12", 2, ("json_schema",))
CAPPED = ModelRail("openai", None, "m", max_output_tokens=12000)
OFF = ModelRail("openai", None, "m")


def capped(tmp_path, **kwargs):
    path = contract2(running_executable(tmp_path, **kwargs))
    path.write_text(path.read_text().replace(
        'a = parser.parse_args()',
        'parser.add_argument("--max-tokens")\n'
        'parser.add_argument("--structured-output")\na = parser.parse_args()'))
    return path


def launch(capture, path, rail):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=attempt,
        runtime=NareRuntime(path, 7, 5), capability=RUNTIME))
    return outcome, ledger, attempt


def recorded(attempt, name):
    return json.loads((attempt / name).read_bytes())


def cut_off(tmp_path, *, status="error", exit_code=1, output=None):
    return capped(tmp_path, status=status, stop="max_tokens", exit_code=exit_code,
                  output=output)


def test_a_capped_rail_passes_the_cap_on_every_request_and_records_it(capture, tmp_path):
    outcome, _, attempt = launch(capture, capped(tmp_path), CAPPED)
    assert outcome.status == "complete"
    argv = recorded(attempt / "cwd", "observed.json")["argv"]
    assert argv[argv.index("--max-tokens") + 1] == "12000"
    assert argv.count("--max-tokens") == 1
    invocation = recorded(attempt, "invocation.json")
    assert invocation["argv"][1:] == argv
    assert invocation["argv"][-2:] == ["--max-tokens", "12000"]
    assert invocation["rail"] == {"provider": "openai", "model": "m", "base_url": None,
                                  "max_output_tokens": 12000}
    assert recorded(attempt, "result.json")["max_output_tokens"] == {
        "limit": 12000, "reached": False}


def test_both_opt_ins_are_passed_and_recorded_together(capture, tmp_path):
    rail = ModelRail("openai", None, "m", "json_schema", 900)
    path = capped(tmp_path, extra=marker())
    outcome, _, attempt = launch(capture, path, rail)
    assert outcome.status == "complete"
    invocation = recorded(attempt, "invocation.json")
    assert invocation["argv"][-4:] == ["--structured-output", "json_schema",
                                       "--max-tokens", "900"]
    assert invocation["rail"]["structured_output"] == "json_schema"
    assert invocation["rail"]["max_output_tokens"] == 900
    result = recorded(attempt, "result.json")
    assert result["structured_output"]["constrained_turns"] == 1
    assert result["max_output_tokens"] == {"limit": 900, "reached": False}


def test_off_is_unchanged(capture, tmp_path):
    outcome, _, attempt = launch(capture, capped(tmp_path), OFF)
    assert outcome.status == "complete"
    assert "--max-tokens" not in recorded(attempt / "cwd", "observed.json")["argv"]
    invocation = recorded(attempt, "invocation.json")
    assert "--max-tokens" not in invocation["argv"]
    assert invocation["rail"] == {"provider": "openai", "model": "m", "base_url": None}
    assert "max_output_tokens" not in recorded(attempt, "result.json")


def test_a_request_cut_off_at_the_cap_fails_the_session_as_the_cap(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, cut_off(tmp_path), CAPPED)
    assert (outcome.status, outcome.reason) == ("failed", "output_cap")
    assert (outcome.nare_status, outcome.stop_reason) == ("error", "max_tokens")
    # The cut-off response reported its usage, so it stays in the shared ledger in full.
    assert outcome.accounting_complete
    assert outcome.usage == TokenUsage(4, 2, 3, 1)
    assert ledger.usage == outcome.usage
    assert ledger.snapshot()["active_reservations"] == []
    result = recorded(attempt, "result.json")
    assert (result["status"], result["reason"]) == ("failed", "output_cap")
    assert result["max_output_tokens"] == {"limit": 12000, "reached": True}


def test_a_cut_off_session_never_yields_a_partial_answer(capture, tmp_path):
    # nare keeps the last schema-valid document a tool turn carried; it is not an answer.
    outcome, _, attempt = launch(capture, cut_off(tmp_path, output=FINDING), CAPPED)
    assert (outcome.status, outcome.reason) == ("failed", "output_cap")
    assert outcome.findings == () and not outcome.output_available
    result = recorded(attempt, "result.json")
    assert result["findings"] == [] and result["output_available"] is False


def test_a_cut_off_stop_reported_as_done_still_fails_as_the_cap(capture, tmp_path):
    outcome, _, _ = launch(capture, cut_off(tmp_path, status="done", exit_code=0,
                                            output=FINDING), CAPPED)
    assert (outcome.status, outcome.reason) == ("failed", "output_cap")
    assert outcome.findings == ()


def test_without_the_cap_a_backend_length_stop_stays_a_provider_failure(capture, tmp_path):
    outcome, _, attempt = launch(capture, cut_off(tmp_path), OFF)
    assert (outcome.status, outcome.reason) == ("failed", "provider")
    assert not outcome.accounting_complete
    assert "max_output_tokens" not in recorded(attempt, "result.json")


@pytest.mark.parametrize("rail", [ModelRail("openai", None, "m", max_output_tokens=12000),
                                  ModelRail("anthropic", None, "m", max_output_tokens=12000)])
def test_a_rail_with_an_invalid_cap_never_launches(capture, tmp_path, rail):
    object.__setattr__(rail, "max_output_tokens", 0)
    outcome, _, attempt = launch(capture, capped(tmp_path), rail)
    assert (outcome.status, outcome.reason) == ("failed", "invocation")
    assert not (attempt / "cwd" / "observed.json").exists()


CAP = {"default": {"provider": "openai", "model": "m", "max_output_tokens": 12000}}


def test_the_manifest_records_the_cap_on_every_rail_like_the_config(capture, monkeypatch):
    config = configure(capture, {**CAP, "overrides": {"security": {"max_output_tokens": None}}},
                       personas=("senior-dev", "security"))
    launched, _ = wave(capture, monkeypatch, config, RUNTIME)
    assert sorted(rail.max_output_tokens or 0 for rail in launched) == [0, 12000]
    manifest = json.loads((capture / "fanout.json").read_bytes())
    rails = {entry["name"]: entry["rail"] for entry in manifest["personas"]}
    assert rails["senior-dev"]["max_output_tokens"] == 12000
    assert rails["security"]["max_output_tokens"] is None
    assert "structured_output" not in rails["security"]
    assert rails["security"] == config.to_dict()["models"]["overrides"]["security"]


def test_an_uncapped_manifest_rail_is_byte_identical(capture, monkeypatch):
    config = configure(capture, {"default": {"provider": "openai", "model": "m"}})
    wave(capture, monkeypatch, config, RUNTIME)
    manifest = json.loads((capture / "fanout.json").read_bytes())
    assert manifest["personas"][0]["rail"] == {"provider": "openai", "base_url": None,
                                               "model": "m"}


def test_a_cut_off_anchor_correction_yields_no_corrections(capture, tmp_path):
    from test_reanchor_session import correction_setup
    descriptor, ledger, lease, _, attempt = correction_setup(capture)
    path = capped(tmp_path, input_tokens=14, status="error", stop="max_tokens", exit_code=1,
                  output={"corrections": [{"request_id": "r0001", "file": "src/app.py",
                                           "line": 2, "side": "RIGHT"}]})
    path.write_text(path.read_text().replace('"contract": 2', '"contract": 1')
                    .replace('"2026.10.12"', '"2026.10.0"'))
    outcome = asyncio.run(module().run_reanchor_session(
        descriptor, ModelRail("openai", None, "m", max_output_tokens=50), lease, ledger=ledger,
        artifact_directory=attempt, runtime=NareRuntime(path, 7, 5),
        capability=NareCapability("2026.10.0", 1)))
    assert (outcome.status, outcome.reason) == ("failed", "output_cap")
    assert outcome.corrections == () and not outcome.output_available
    assert outcome.accounting_complete and ledger.usage.total == 60 + 14 + 6
    assert recorded(attempt, "result.json")["max_output_tokens"] == {"limit": 50,
                                                                     "reached": True}
