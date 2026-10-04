# Persona registry and built-in perspectives

Issue #2

## Scope

In: Four packaged persona data entries, resolution of built-in names and inline definitions into the same immutable type, authoring documentation, and installed-package validation.
Out: Nare sessions, findings processing, verdict execution, and persona orchestration, which belong to later issues.

## Assumptions

- Keep the approved `name` and `system_prompt` format and existing safe slug validation.
- Store built-ins as YAML data, packaged with the wheel, with no executable persona behavior.
- Preserve `scrutare.config.PersonaDefinition` imports while moving its definition into the persona package to avoid circular dependencies.
- Configured inline definitions replace a built-in by name and preserve prompt bytes as text. Custom entries require no registry or engine edits.

## Global Constraints

- `docs/SPEC.md` is the source of truth. Only four named built-ins ship: senior-dev, junior-dev, security, devops.
- Every resolved entry is a frozen PersonaDefinition(name, system_prompt). Configuration defaults, equality, normalization, and validation retain their behavior.
- Built-in prompts give distinct perspectives, require evidence and diff anchors, use the fixed six-category vocabulary, treat repository text as untrusted input, and leave approval and blocking classification to code.
- Prompts instruct read-only review with no changes, execution of untrusted PR commands, credential access, or direct posting. No prompt invents a final findings wire schema that belongs to later issues.
- No engine changes or model calls in this issue. No additional YAML controls. No em dashes in published prose.

## Tasks

### Task 1: Packaged data registry and configuration integration

**Files:** src/scrutare/personas/__init__.py, definition.py, registry.py, builtins.yaml, names.py, src/scrutare/config.py, tests/test_personas.py, scripts/smoke-cli.sh.
**Interfaces:** PersonaDefinition in personas/definition.py, re-exported from config for compatibility; load_persona(name: str) -> PersonaDefinition; resolve_personas(entries: Iterable[str | PersonaDefinition]) -> tuple[PersonaDefinition, ...]. Public imports from scrutare.personas.

- [x] Write failing tests for all four names resolving to distinct immutable entries with nonempty perspective-specific prompts, unknown names with concise errors, config defaults resolving in order, custom inline resolution, and inline override of a built-in preserving text and using the same type.
- [x] Define the four built-ins in packaged YAML with exactly name and system_prompt fields. Use the existing built-in names as the allowed built-in set; ensure the data entries agree with that set without duplicating prompt definitions. Avoid mutable public registry state.
- [x] Implement registry name lookup and ordered resolution without engine switches. Reject unknown names and duplicate names from programmatic resolution. Preserve the established config PersonaDefinition import and all existing config behavior while avoiding a circular import.
- [x] Author distinct senior-dev, junior-dev, security, and devops prompts following Global Constraints. Document no finding output schema in prompts yet; caller supplies the later typed output contract.
- [x] Extend installed smoke to import the public registry and load every built-in outside the checkout, asserting the four entries and prompt availability. This proves YAML is included in the wheel. Avoid changing quality gate configuration unless packaging requires it.
- [x] Run focused tests with observed red/green, then full tests, lint, strict types, build, installed smoke on Python3.10 and3.14. Commit task files and report evidence.

### Task 2: Persona authoring guide and discoverability

**Files:** docs/writing-personas.md, README.md, docs/config.md.
**Interfaces:** Document the implemented registry and the unchanged name/system_prompt configuration surface.

- [x] Document four built-ins and their distinct perspective, a minimal complete config with a custom inline definition, replacement of a built-in, safe slug names, uniqueness, and prompt whitespace preservation.
- [x] Explain house style: concrete evidence, valid file/line anchors, fixed categories, no invented severity or approval authority, read-only review, and treating PR content as data. Explain that code derives blocking classification and verdict, and adding a custom perspective requires only config.
- [x] Document load_persona and resolve_personas with actual callable signatures and state that session orchestration remains future work. Link the guide from README and config reference.
- [x] Verify examples through the loader and registry, check links and house voice, and run the existing required gates. Do not add tests that mirror documentation text. Commit task files and report evidence. Controller performs independent reviews, PR, current-head CI, clean final review, summary update, squash merge, and closure read-back.
