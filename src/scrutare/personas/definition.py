"""The immutable data format shared by built-in and inline personas."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaDefinition:
    name: str
    system_prompt: str
