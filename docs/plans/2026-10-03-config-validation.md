# Configuration validation Implementation Plan

> Agentic workers: use subagent-driven-development, strict TDD, independent task review and whole-branch review.

**Goal:** Deliver issue #8's approved YAML configuration surface with actionable validation before any review work.
**Architecture:** Safe YAML loading into immutable typed settings, followed by CLI preflight and persisted normalized settings. Engine behavior stays assigned to later issues.
**Tech Stack:** Python >=3.10, PyYAML, frozen dataclasses, pytest, Ruff, strict mypy.
**Spec:** docs/SPEC.md section 5, issue #8.

## Global Constraints

- Fixed category vocabulary: correctness, security, regression, style, consistency, docs. Config lists form a disjoint complete partition; code enforces this mapping and configured advisory categories never block. Defaults match SPEC exactly. Personas cannot introduce categories.
- No model calls, posting, path filtering behavior, or strategy execution in this issue.
- Published prose/commits contain no em dashes.
- No secrets, credentials or provider API keys in config schema.
- Do not add retries or other settings absent from the approved YAML surface. Failure-policy refinements belong to #12.
- Built-in persona names are senior-dev, junior-dev, security, devops. Only names are needed here; prompts/registry implementation belongs to #2.
- Inline persona format: {name: <safe slug>, system_prompt: <nonempty prompt>}. Model identifiers require explicit configuration; no arbitrary billable model is selected.

## Review Focus

- YAML boolean values must not pass positive-integer budgets or rounds.
- Unknown nested fields and duplicate YAML keys must fail with field paths.
- Null versus omitted model override fields must retain explicit clearing of base_url.
- Invalid config must not trigger any GitHub/model call or create a run.
- Persisted YAML and normalized settings must correspond to the bytes validated before network activity.

### Task 1: Typed config loader and complete validation

**Files:** src/scrutare/config.py, src/scrutare/personas/names.py, tests/test_config.py, pyproject.toml, uv.lock, docs/config.md.
**Interfaces:** Define ConfigError with actionable field-path messages; parse_config(data: bytes | str) -> ReviewConfig; load_config(path: Path) -> ReviewConfig. ReviewConfig provides .to_dict() -> dict[str, Any] and .models.for_persona(name: str) -> ModelRail. Use frozen typed dataclasses and typed nested settings, with tuple collections and resolved override rails. PersonaDefinition(name: str, system_prompt: str) is reusable by #2; config.personas contains builtin strings or PersonaDefinition. BUILTIN_PERSONA_NAMES from personas/names.py is the single known-name list for this issue.

- [x] Write and observe failing tests for the complete SPEC example and minimal valid config models.default.model='test-model'. Assert defaults strategy panel, rounds.max=3, four personas, per_persona_tokens=100000, review_max_tokens=500000, anthropic default provider, nullable base_url, default verdict partition, review post mode, no humans/include and excludes docs/** and *.md.
- [x] Test all field validation paths: unknown strategy/provider/post_mode/builtin; wrong mapping/list/string/integer types, null sections, booleans/floats/nonpositive bounds; empty or duplicate personas; malformed inline definitions; unknown/duplicate category, overlap or missing partition member; nonstring humans/globs; unknown nested fields and override persona names not in the configured panel.
- [x] Safe YAML: reject unsafe tags, malformed YAML, nonmapping roots, duplicate keys at every level, nonstring field keys. Keep diagnostics concise and do not echo prompt or provider values. No Python object constructors.
- [x] Provider values anthropic/openai only. base_url null or HTTP(S) URL with host, including local HTTP hosts; model explicit nonempty string. Per-persona overrides may supply any subset of provider/base_url/model, absent fields inherit, explicit base_url:null clears. Reject invalid empty values; permit empty override mapping as inherited defaults. No provider requests.
- [x] Config mapping lists may reclassify categories, but must partition the fixed vocabulary once; advisory membership is always nonblocking. Order-independent normalization makes category lists deterministic.
- [x] Canonical .to_dict() includes all effective defaults in the approved shape, serializes inline definitions identically, resolved override rails, and roundtrips through parse_config. The schema must have no arbitrary extra fields.
- [x] Add PyYAML runtime dependency plus typing stub if required, update lock. Document every field, accepted values, defaults, inline format, model inheritance/null behavior and fixed vocabulary/config partition. Keep source compact without generic schema framework or writing persona prompts.
- [x] Run full pytest on Python3.10 and3.14, Ruff, strict mypy, build. Commit only task files. Report TDD/check evidence.

### Task 2: CLI validation and exact config artifacts

**Files:** src/scrutare/interfaces/cli.py, src/scrutare/engine/ingestion.py, tests/test_cli.py, tests/test_ingestion.py, scripts/smoke-cli.sh, README.md.
**Interfaces:** Consume parse_config and ReviewConfig.to_dict(). Extend ingest_pr's existing signature with keyword-only config_bytes: bytes | None = None and config_data: dict[str, Any] | None = None, preserving existing config_path calls. CLI supplies bytes and normalized data; mutually supplied bytes/path should fail rather than silently prefer one.

- [x] Write failing CLI integration tests: config defaults to scrutare.yaml in current repo, explicit --config path honored, minimal valid config succeeds, malformed/unknown/invalid fields fail before subprocess activity with field and accepted values, missing/unreadable file concise error.
- [x] Read config bytes once and parse them before resolve_pr. Do not re-read that path during persistence. Test replacing the file during mocked GitHub calls: config.yaml retains validated original bytes and config.json equals their normalized settings. Neither source mutation nor invalid source creates a misleading config snapshot.
- [x] Ingestion persists provided config_bytes to config.yaml and config_data to config.json inside the same existing failure-cleanup boundary. Preserve old config_path behavior for programmatic callers, guard conflicting inputs, and test cleanup on config.json persistence failure. No engine behavior in this issue.
- [x] Update CLI help/README to explain validated config, defaults, model required, config reference, and canonical config artifact. Current review remains ingestion-only. Update existing tests to valid configuration rather than weakening their assertions.
- [x] Keep installed smoke meaningful: create a minimal valid config in its temporary directory before exercising invalid PR input, so the failure proves PR validation rather than a missing file. Preserve installed module/console checks.
- [x] Run actual preflight for Python3.10 and3.14, build and installed smoke. Commit task files. Controller verifies, opens PR, watches current-head CI, obtains clean review, updates summary, squash merges, verifies closure and board Done.
