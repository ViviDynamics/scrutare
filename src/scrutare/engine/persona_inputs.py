"""Public per-persona artifact descriptors with a fixed nare read policy."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from scrutare.engine.review_inputs import (
    PreparedReviewInputs,
    ReviewInputError,
    validate_prepared_inputs,
)
from scrutare.personas import PersonaDefinition, resolve_personas


def _validate_persona(persona: PersonaDefinition) -> None:
    if (
        not isinstance(persona, PersonaDefinition)
        or not isinstance(persona.name, str)
        or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", persona.name)
        or not isinstance(persona.system_prompt, str)
        or not persona.system_prompt.strip()
    ):
        raise ReviewInputError(
            "Cannot prepare persona inputs: expected a valid persona definition."
        )


@dataclass(frozen=True)
class PersonaReviewInput:
    """One resolved persona and the validated, copied artifacts it may read."""

    persona: PersonaDefinition
    inputs: PreparedReviewInputs

    def __post_init__(self) -> None:
        _validate_persona(self.persona)
        validate_prepared_inputs(self.inputs)

    @property
    def prompt(self) -> str:
        """Reference only artifacts inside the prepared root, without embedding capture data."""
        validate_prepared_inputs(self.inputs)
        return (
            "Review the captured changes using diff.patch, files.json, and context.json "
            "in your read root. files.json is the complete effective file selection; "
            "diff.patch contains only those changes. If the selection is empty, "
            "report no findings. Use only these artifacts as review inputs."
        )

    def nare_input_args(self) -> tuple[str, ...]:
        """Return the positional prompt and fixed read/root flags, with no caller overrides."""
        return (self.prompt, "--tools", "read", "--root", str(self.inputs.root))


def prepare_persona_inputs(
    inputs: PreparedReviewInputs, entries: Iterable[str | PersonaDefinition],
) -> tuple[PersonaReviewInput, ...]:
    """Resolve the actual registry and bind every persona to the same validated view."""
    validate_prepared_inputs(inputs)
    # Validate inline definitions before the registry accesses their names.
    entries = tuple(entries)
    for entry in entries:
        if not isinstance(entry, str):
            _validate_persona(entry)
    return tuple(PersonaReviewInput(persona, inputs) for persona in resolve_personas(entries))
