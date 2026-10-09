"""Persona resolution boundaries using packaged data and real configuration."""

from dataclasses import FrozenInstanceError
from importlib.resources import files

import pytest
import yaml

from scrutare.config import PersonaDefinition, parse_config

MINIMAL = "models:\n  default:\n    model: test-model\n"
NAMES = ("senior-dev", "junior-dev", "security", "devops")


def test_builtins_resolve_to_distinct_immutable_definitions():
    from scrutare.personas import load_persona, resolve_personas

    entries = resolve_personas(NAMES)
    assert isinstance(entries, tuple)
    assert tuple(entry.name for entry in entries) == NAMES
    assert all(isinstance(entry, PersonaDefinition) for entry in entries)
    assert all(entry.system_prompt.strip() for entry in entries)
    assert len({entry.system_prompt for entry in entries}) == 4
    for entry in entries:
        with pytest.raises(FrozenInstanceError):
            entry.system_prompt = "changed"
        assert load_persona(entry.name).system_prompt == entry.system_prompt


def test_packaged_data_has_only_inline_definition_fields_and_allowed_names():
    data = yaml.safe_load(files("scrutare.personas").joinpath("builtins.yaml").read_text())
    assert {entry["name"] for entry in data} == set((*NAMES, "testing-verification"))
    assert len(data) == 5
    assert all(set(entry) == {"name", "system_prompt"} for entry in data)


@pytest.mark.parametrize("name", ["unknown", "", "senior_dev"])
def test_unknown_names_have_concise_errors(name):
    from scrutare.personas import load_persona, resolve_personas

    for operation in (lambda: load_persona(name), lambda: resolve_personas([name])):
        with pytest.raises(ValueError, match="[Uu]nknown persona") as error:
            operation()
        assert len(str(error.value)) < 160


def test_config_defaults_resolve_in_configured_order():
    from scrutare.personas import resolve_personas

    config = parse_config(MINIMAL)
    assert tuple(entry.name for entry in resolve_personas(config.personas)) == NAMES


def test_mixed_generator_preserves_order_and_inline_definition():
    from scrutare.personas import resolve_personas

    custom = PersonaDefinition("accessibility", "  Inspect keyboard navigation.\n\n")
    entries = resolve_personas(entry for entry in ("devops", custom, "security"))
    assert tuple(entry.name for entry in entries) == ("devops", "accessibility", "security")
    assert entries[1] is custom
    assert entries[1].system_prompt == "  Inspect keyboard navigation.\n\n"


def test_inline_builtin_override_preserves_text_and_compatible_type():
    from scrutare.personas import PersonaDefinition as PublicDefinition
    from scrutare.personas import load_persona, resolve_personas

    prompt = "  Inspect our authentication boundary.\n\n"
    config = parse_config(
        yaml.safe_dump(
            {
                "models": {"default": {"model": "test-model"}},
                "personas": [{"name": "security", "system_prompt": prompt}, "junior-dev"],
            }
        )
    )
    entries = resolve_personas(config.personas)
    assert PublicDefinition is PersonaDefinition
    assert entries[0] == PersonaDefinition("security", prompt)
    assert entries[0] is config.personas[0]
    assert entries[0].system_prompt == prompt
    assert load_persona("security").system_prompt != prompt
    assert entries[1].name == "junior-dev"


@pytest.mark.parametrize(
    "entries",
    [
        ["security", "security"],
        ["security", PersonaDefinition("security", "Inline security perspective")],
        [PersonaDefinition("security", "Inline security perspective"), "security"],
        [PersonaDefinition("custom", "First"), PersonaDefinition("custom", "Second")],
    ],
)
def test_programmatic_resolution_rejects_duplicate_names(entries):
    from scrutare.personas import resolve_personas

    with pytest.raises(ValueError, match="[Dd]uplicate persona name"):
        resolve_personas(entries)


def test_empty_programmatic_resolution_is_an_empty_tuple():
    from scrutare.personas import resolve_personas

    assert resolve_personas([]) == ()
