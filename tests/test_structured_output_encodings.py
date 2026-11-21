"""The json_object and tool_call answer encodings, gated per provider on `nare contract`."""

import pytest
from test_nare_flag_capability import advertised, inspect
from test_nare_session import contract3_capability as capability
from test_nare_session import flag_map, module

from scrutare.config import ModelRail, ModelSettings
from scrutare.engine.session_models import NareCapability

MODES = ["json_schema", "json_object", "tool_call"]
# The entry nare 2026.11.107 prints, without the fields Scrutare does not read.
ENCODINGS = {"flag": "--structured-output", "modes": MODES, "requires": "--schema",
             "providers": ["anthropic", "bedrock", "openai"],
             "provider_modes": {"anthropic": ["json_schema"], "bedrock": ["json_schema"],
                                "openai": MODES},
             "event": "progress", "event_detail": "structured_output"}
# The entry nare 2026.11.100 printed: one mode, no per-provider list.
BEFORE = {"flag": "--structured-output", "modes": ["json_schema"], "requires": "--schema",
          "providers": ["anthropic", "bedrock", "openai"]}
ALL_CHOICES = {"--structured-output": {"takes_value": True, "choices": MODES}}


def current(tmp_path, entry=ENCODINGS, choices=ALL_CHOICES):
    return inspect(advertised(tmp_path, version="2026.11.107",
                              flags=flag_map(extra=choices), structured_output=entry))


def test_the_advertised_modes_are_read_for_each_provider(tmp_path):
    found = current(tmp_path)
    assert found.structured_output == tuple(MODES)
    assert found.structured_output_providers == ("anthropic", "openai")
    assert found.structured_output_for("openai") == tuple(MODES)
    assert found.structured_output_for("anthropic") == ("json_schema",)
    assert found.structured_output_for("fake") == ()


@pytest.mark.parametrize(("entry", "choices", "openai", "anthropic"), [
    # Modes without a per-provider list offer json_schema only, as before 2026.11.107.
    ({key: value for key, value in ENCODINGS.items() if key != "provider_modes"},
     ALL_CHOICES, ("json_schema",), ("json_schema",)),
    # The flag must list a mode among its choices.
    (ENCODINGS, {"--structured-output": {"takes_value": True,
                                         "choices": ["json_schema", "tool_call"]}},
     ("json_schema", "tool_call"), ("json_schema",)),
    (ENCODINGS, {}, ("json_schema",), ("json_schema",)),
    # A mode the entry does not list is not offered, whatever provider_modes says.
    ({**ENCODINGS, "modes": ["json_schema", "json_object"]}, ALL_CHOICES,
     ("json_schema", "json_object"), ("json_schema",)),
    # A provider the entry does not list offers nothing.
    ({**ENCODINGS, "providers": ["openai"]}, ALL_CHOICES, tuple(MODES), ()),
    # An unknown mode is ignored.
    ({**ENCODINGS, "modes": [*MODES, "grammar"],
      "provider_modes": {"openai": [*MODES, "grammar"]}}, ALL_CHOICES, tuple(MODES), ()),
    # An unfamiliar per-provider list advertises nothing.
    ({**ENCODINGS, "provider_modes": ["openai"]}, ALL_CHOICES, (), ()),
    ({**ENCODINGS, "provider_modes": {"openai": "tool_call"}}, ALL_CHOICES, (), ()),
    ({**ENCODINGS, "provider_modes": {}}, ALL_CHOICES, (), ()),
])
def test_each_mode_needs_the_entry_the_provider_and_the_flag(
        tmp_path, entry, choices, openai, anthropic):
    found = current(tmp_path, entry, choices)
    assert found.structured_output_for("openai") == openai
    assert found.structured_output_for("anthropic") == anthropic
    assert found.structured_output == tuple(
        mode for mode in MODES if mode in openai or mode in anthropic)


def rails(mode):
    return ModelSettings(ModelRail("openai", "https://litellm.example/v1", "m", mode))


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_an_older_runtime_refuses_a_review_that_asks_for_an_encoding(tmp_path, mode):
    found = inspect(advertised(tmp_path, version="2026.11.100", flags=flag_map(),
                               structured_output=BEFORE))
    with pytest.raises(module().SessionRuntimeError, match=(
            f"^capability: nare 2026.11.100 cannot send structured output {mode}; ")):
        module().require_rail_options(rails(mode), found)
    # A default review, and one asking for json_schema, are unaffected.
    module().require_rail_options(rails(None), found)
    module().require_rail_options(rails("json_schema"), found)


@pytest.mark.parametrize("mode", ["json_object", "tool_call"])
def test_an_override_asking_for_an_encoding_is_refused_on_an_older_runtime(tmp_path, mode):
    found = inspect(advertised(tmp_path, version="2026.11.100", flags=flag_map(),
                               structured_output=BEFORE))
    models = ModelSettings(ModelRail("openai", None, "m"),
                           (("security", ModelRail("openai", None, "m", mode)),))
    with pytest.raises(module().SessionRuntimeError, match=f"structured output {mode}"):
        module().require_rail_options(models, found)


@pytest.mark.parametrize("mode", MODES)
def test_the_current_runtime_sends_each_encoding_on_openai(tmp_path, mode):
    found = current(tmp_path)
    module().require_rail_options(rails(mode), found)
    rail = ModelRail("openai", None, "m", mode)
    sent = module()._rail_options(rail)
    assert sent[sent.index("--structured-output") + 1] == mode
    assert sent.count("--structured-output") == 1


def test_a_default_rail_sends_the_same_argv_on_either_runtime(tmp_path):
    rail = ModelRail("openai", None, "m")
    before = inspect(advertised(tmp_path, version="2026.11.100", flags=flag_map(),
                                structured_output=BEFORE))
    after = current(tmp_path)
    assert module()._rail_argv(rail, before) == module()._rail_argv(rail, after)
    assert "--structured-output" not in module()._rail_options(rail)


def test_a_programmatic_capability_without_provider_modes_offers_json_schema_only():
    found = capability(structured_output=("json_schema", "tool_call"),
                       structured_output_providers=("openai",),
                       extra=ALL_CHOICES)
    assert found.structured_output_for("openai") == ("json_schema",)
    with pytest.raises(module().SessionRuntimeError, match="structured output tool_call"):
        module().require_rail_options(rails("tool_call"), found)


@pytest.mark.parametrize("value", [
    "openai", (("openai",),), (("openai", ("grammar",)),),
    (("openai", ("json_schema",)), ("openai", ("json_schema",))), (("", ("json_schema",)),),
])
def test_capabilities_refuse_a_malformed_per_provider_list(value):
    with pytest.raises(ValueError, match="structured_output_provider_modes"):
        NareCapability("2026.11.107", 3, ("json_schema",),
                       structured_output_provider_modes=value)


def test_the_session_layer_refuses_an_openai_only_encoding_on_anthropic():
    rail = object.__new__(ModelRail)
    for name, value in (("provider", "anthropic"), ("base_url", None), ("model", "m"),
                        ("structured_output", "tool_call"), ("max_output_tokens", None),
                        ("proxy_num_retries", None), ("request_retries", None),
                        ("request_retry_seconds", None), ("cache_breakpoints", None),
                        ("effort", None)):
        object.__setattr__(rail, name, value)
    with pytest.raises(module().SessionRuntimeError, match="^rail.structured_output: "):
        module()._rail(rail)
