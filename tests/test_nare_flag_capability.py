"""Contract 3 capabilities are read from the advertised `nare contract` data."""

import asyncio
import json

import pytest
from test_nare_session import (
    contract3_capability as capability,
)
from test_nare_session import executable, flag_map, module, running_executable, setup
from test_review_inputs import capture as capture

from scrutare.config import ModelRail, ModelSettings
from scrutare.engine.session_models import NareCapability, NareFlag, NareRuntime

STRUCTURED = {"flag": "--structured-output", "modes": ["json_schema"], "requires": "--schema",
              "providers": ["anthropic", "bedrock", "openai"]}
PROXY = {"flag": "--proxy-num-retries", "providers": ["openai"], "request_field": "num_retries",
         "default": None}



def advertised(tmp_path, *, version="2026.11.94", contract=3, **entries):
    path = executable(tmp_path, version=version, contract=contract)
    path.write_text(path.read_text().replace(
        f'"nare": {version!r}}}',
        f'"nare": {version!r}, **{entries!r}}}'))
    return path


def inspect(path):
    return asyncio.run(module().inspect_nare_runtime(NareRuntime(path)))


def test_contract_3_inspection_reads_every_advertised_flag(tmp_path):
    found = inspect(advertised(tmp_path, flags=flag_map(), structured_output=STRUCTURED,
                               proxy_num_retries=PROXY))
    assert found == capability(structured_output=("json_schema",), proxy_num_retries=True,
                               structured_output_providers=("anthropic", "openai"))
    assert found.advertises("--stream") and not found.advertises("--effort")
    assert found.advertises("--compaction", "elide")
    assert not found.advertises("--compaction", "other")
    assert found.flag("--stream") == NareFlag("--stream", False)
    # A switch takes no value, so no value of it is advertised.
    assert not found.advertises("--stream", "yes")


@pytest.mark.parametrize("flags", [
    None, [], "flags", {}, {"--stream": True}, {"--stream": {"takes_value": "no"}},
    {**flag_map(), "--stream": {"takes_value": False, "choices": "any"}},
    {**flag_map(), "stream": {"takes_value": False, "choices": None}},
    flag_map(without=("--request-retries",)), flag_map(without=("--max-cutoffs",)),
    flag_map(without=("--schema",)),
    {**flag_map(), "--jsonl": {"takes_value": True, "choices": None}},
])
def test_contract_3_without_usable_flags_fails_inspection(tmp_path, flags):
    entries = {} if flags is None else {"flags": flags}
    with pytest.raises(module().SessionRuntimeError, match="^contract: expected advertised"):
        inspect(advertised(tmp_path, **entries))


def test_contracts_1_and_2_never_carry_flags(tmp_path):
    found = inspect(advertised(tmp_path, version="2026.10.12", contract=2, flags=flag_map()))
    assert found == NareCapability("2026.10.12", 2)
    assert found.flags is None and not found.advertises("--stream")


@pytest.mark.parametrize(("entry", "modes", "providers"), [
    (STRUCTURED, ("json_schema",), ("anthropic", "openai")),
    ({**STRUCTURED, "providers": ["openai"]}, ("json_schema",), ("openai",)),
    ({**STRUCTURED, "providers": ["bedrock"]}, (), ()),
    ({**STRUCTURED, "modes": ["grammar"]}, (), ()),
    ({**STRUCTURED, "flag": "--other"}, (), ()),
    ("json_schema", (), ()),
])
def test_contract_3_structured_output_names_its_providers(tmp_path, entry, modes, providers):
    found = inspect(advertised(tmp_path, flags=flag_map(), structured_output=entry))
    assert found.structured_output == modes
    assert (found.structured_output_providers if modes else ()) == providers


def test_contract_3_structured_output_needs_the_flag_and_its_choice(tmp_path):
    for flags in (flag_map(without=("--structured-output",)),
                  flag_map(extra={"--structured-output": {"takes_value": True,
                                                          "choices": ["grammar"]}})):
        found = inspect(advertised(tmp_path, flags=flags, structured_output=STRUCTURED))
        assert found.structured_output == ()


def test_contract_3_proxy_retries_tolerate_additive_entry_fields(tmp_path):
    found = inspect(advertised(tmp_path, flags=flag_map(),
                               proxy_num_retries={**PROXY, "added_later": True}))
    assert found.proxy_num_retries
    for entries in ({"flags": flag_map(without=("--proxy-num-retries",)),
                     "proxy_num_retries": PROXY},
                    {"flags": flag_map(), "proxy_num_retries": {**PROXY, "providers": []}},
                    {"flags": flag_map()}):
        assert not inspect(advertised(tmp_path, **entries)).proxy_num_retries


def rail_argv(rail, found):
    return module()._rail_argv(rail, found)


def test_contract_3_streams_only_when_the_flag_is_advertised():
    openai = ModelRail("openai", None, "m")
    assert "--stream" in rail_argv(openai, capability())
    assert "--stream" not in rail_argv(openai, capability(without=("--stream",)))
    assert "--stream" not in rail_argv(ModelRail("anthropic", None, "m"), capability())


@pytest.mark.parametrize(("version", "contract", "streams"), [
    ("2026.10.3", 1, False), ("2026.10.4", 1, True), ("2026.10.12", 2, True)])
def test_contracts_1_and_2_stream_by_release_number(version, contract, streams):
    found = NareCapability(version, contract)
    assert ("--stream" in rail_argv(ModelRail("openai", None, "m"), found)) is streams


def test_contract_3_pins_compaction_only_where_advertised():
    argv = rail_argv(ModelRail("openai", None, "m"), capability())
    for flag, value in (("--compaction", "elide"), ("--compaction-trigger", "0.8"),
                        ("--compaction-target", "0.6"), ("--max-cutoffs", "0"),
                        ("--request-retries", "0"), ("--max-corrections", "1")):
        assert argv[argv.index(flag) + 1] == value and argv.count(flag) == 1
    # A runtime from before the options existed is sent none of them.
    argv = rail_argv(ModelRail("openai", None, "m"), capability(
        without=("--compaction", "--compaction-trigger", "--compaction-target")))
    assert not any(item.startswith("--compaction") for item in argv)
    # The two shares are only meaningful together.
    argv = rail_argv(ModelRail("openai", None, "m"), capability(
        without=("--compaction-target",)))
    assert argv[argv.index("--compaction") + 1] == "elide"
    assert "--compaction-trigger" not in argv and "--compaction-target" not in argv


# Options nare 2026.11.100 lists that no session is sent.
CAPACITY_FLAGS = {
    **{name: {"takes_value": False, "choices": None}
       for name in ("--capacity-queue", "--capacity-adaptive")},
    **{name: {"takes_value": True, "choices": None}
       for name in ("--capacity-wait-seconds", "--capacity-refusals", "--capacity-ceiling",
                    "--capacity-floor", "--capacity-quiet-seconds", "--capacity-shared-dir")},
}


@pytest.mark.parametrize("rail", [
    ModelRail("openai", "https://gateway.invalid/v1", "m"), ModelRail("anthropic", None, "m"),
    ModelRail("openai", None, "m", request_retries=2, effort="high", cache_breakpoints=True)])
def test_a_runtime_that_lists_the_capacity_flags_is_sent_none_of_them(rail):
    # The same rail against the previous pin's flags and against the new pin's.
    before = capability()
    after = capability(extra=CAPACITY_FLAGS, version="2026.11.100")
    assert rail_argv(rail, before) == rail_argv(rail, after)
    sent = rail_argv(rail, after) + module()._rail_options(rail)
    assert not any(item.startswith("--capacity") for item in sent)


def test_legacy_contracts_send_no_compaction_or_recovery_flags():
    argv = rail_argv(ModelRail("openai", None, "m"), NareCapability("2026.10.12", 2))
    assert argv == ["--provider", "openai", "--model=m", "--stream"]


@pytest.mark.parametrize(("rail", "found", "message"), [
    (ModelRail("openai", None, "m", structured_output="json_schema"), capability(),
     "structured output json_schema"),
    (ModelRail("openai", None, "m", proxy_num_retries=0), capability(), "proxy_num_retries"),
    (ModelRail("openai", None, "m", proxy_num_retries=0),
     capability(without=("--proxy-num-retries",), proxy_num_retries=True),
     "proxy_num_retries"),
    (ModelRail("openai", None, "m", structured_output="json_schema"),
     capability(without=("--structured-output",), structured_output=("json_schema",)),
     "structured output json_schema"),
])
def test_an_option_the_runtime_does_not_advertise_is_refused_before_any_session(
        rail, found, message):
    with pytest.raises(module().SessionRuntimeError, match="^capability: nare 2026.11.94 "
                                                          "cannot send " + message):
        module().require_rail_options(ModelSettings(rail), found)
    # The historical name still refuses the same wave.
    with pytest.raises(module().SessionRuntimeError):
        module().require_structured_output(ModelSettings(rail), found)


def test_advertised_options_pass_the_wave_check():
    rail = ModelRail("openai", None, "m", structured_output="json_schema", proxy_num_retries=1,
                     max_output_tokens=10)
    module().require_rail_options(ModelSettings(rail), capability(
        structured_output=("json_schema",), proxy_num_retries=True))


def contract3_executable(tmp_path):
    path = running_executable(tmp_path)
    path.write_text(path.read_text().replace('"contract": 1', '"contract": 3')
                    .replace('"2026.10.0"', '"2026.11.94"')
                    .replace('a = parser.parse_args()',
                             'for flag in ("max-cutoffs", "request-retries", "max-corrections",\n'
                             '             "compaction", "compaction-trigger",\n'
                             '             "compaction-target"):\n'
                             '    parser.add_argument("--" + flag)\n'
                             'parser.add_argument("--stream", action="store_true")\n'
                             'a = parser.parse_args()'))
    return path


def run(capture, tmp_path, found, rail=None):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, rail or ModelRail("openai", None, "m"), lease, ledger=ledger,
        artifact_directory=attempt, runtime=NareRuntime(contract3_executable(tmp_path), 7, 5),
        capability=found))
    return outcome, attempt


def test_a_contract_3_session_needs_the_inspected_flags(capture, tmp_path):
    outcome, attempt = run(capture, tmp_path, NareCapability("2026.11.94", 3))
    assert outcome.status == "failed" and outcome.reason == "invocation"
    assert not (attempt / "stdout.jsonl").exists()


def test_a_session_refuses_an_unadvertised_option_before_launch(capture, tmp_path):
    outcome, attempt = run(capture, tmp_path, capability(),
                           ModelRail("openai", None, "m", proxy_num_retries=0))
    assert outcome.status == "failed" and outcome.reason == "invocation"
    assert not (attempt / "stdout.jsonl").exists()


def test_a_contract_3_session_records_the_gated_argv(capture, tmp_path):
    outcome, attempt = run(capture, tmp_path, capability(without=("--stream",)))
    assert outcome.status == "complete"
    argv = json.loads((attempt / "invocation.json").read_bytes())["argv"]
    assert "--stream" not in argv
    assert argv[argv.index("--compaction") + 1] == "elide"
    assert argv[1:] == json.loads((attempt / "cwd/observed.json").read_bytes())["argv"]
