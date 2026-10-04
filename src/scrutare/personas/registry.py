"""Load packaged built-ins and resolve configured data without running sessions."""

from collections.abc import Iterable
from functools import cache
from importlib.resources import files

import yaml

from scrutare.personas.definition import PersonaDefinition
from scrutare.personas.names import BUILTIN_PERSONA_NAMES


@cache
def _builtin_personas() -> tuple[PersonaDefinition, ...]:
    data = yaml.safe_load(
        files("scrutare.personas").joinpath("builtins.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(data, list):
        raise RuntimeError("Packaged personas must be a list of definitions")
    entries = []
    for entry in data:
        if not isinstance(entry, dict) or set(entry) != {"name", "system_prompt"}:
            raise RuntimeError("Packaged personas must contain only name and system_prompt")
        name, prompt = entry["name"], entry["system_prompt"]
        if not isinstance(name, str) or not isinstance(prompt, str) or not prompt.strip():
            raise RuntimeError("Packaged personas must have string names and nonempty prompts")
        entries.append(PersonaDefinition(name, prompt))
    if len(entries) != len(BUILTIN_PERSONA_NAMES) or {p.name for p in entries} != set(
        BUILTIN_PERSONA_NAMES
    ):
        raise RuntimeError("Packaged personas must match the allowed built-in names exactly")
    return tuple(entries)


def load_persona(name: str) -> PersonaDefinition:
    """Return one immutable built-in definition, or raise ValueError for an unknown name."""
    if name not in BUILTIN_PERSONA_NAMES:
        raise ValueError(
            f"Unknown persona name; expected one of {', '.join(BUILTIN_PERSONA_NAMES)}"
        )
    return next(persona for persona in _builtin_personas() if persona.name == name)


def resolve_personas(entries: Iterable[str | PersonaDefinition]) -> tuple[PersonaDefinition, ...]:
    """Resolve names and preserve inline definitions in order, rejecting duplicate names."""
    resolved = []
    seen = set[str]()
    for entry in entries:
        persona = load_persona(entry) if isinstance(entry, str) else entry
        if persona.name in seen:
            raise ValueError("Duplicate persona name")
        seen.add(persona.name)
        resolved.append(persona)
    return tuple(resolved)
