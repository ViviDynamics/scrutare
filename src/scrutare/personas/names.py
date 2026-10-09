"""Allowed built-in names shared by configuration and the persona registry."""

DEFAULT_PERSONA_NAMES = ("senior-dev", "junior-dev", "security", "devops")
BUILTIN_PERSONA_NAMES = (*DEFAULT_PERSONA_NAMES, "testing-verification")

SPECIALIST_PERSONA_NAMES = ("performance-concurrency", "data-integrity-migrations")
SELECTABLE_PERSONA_NAMES = (*BUILTIN_PERSONA_NAMES, *SPECIALIST_PERSONA_NAMES)
