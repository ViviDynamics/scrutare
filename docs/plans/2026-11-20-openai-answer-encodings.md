# Answer encodings on openai rails

## Goal and design

Let an openai rail choose how nare encodes the constrained answer turn (#131,
part of #137). nare 2026.11.107 (nare #313) adds `--structured-output
json_object` and `--structured-output tool_call` on its openai rail beside
`json_schema`. Many self-hosted backends handle a forced tool call better than
`response_format` json_schema, or support only one of them, and the GLM
schedules never tried another encoding.

The rules of [the 2026.11.107 pin](2026-11-20-nare-2026-11-107.md) stay: the
subprocess boundary, capabilities read from `nare contract`, and nothing that
changes what a default-configuration review sends or how its verdict is derived.
No live model call, credential, holdout content or deployment is part of this
work. Every proof runs offline, against fake contracts and the installed
2026.11.107 console.

### What nare offers

`nare contract` at 2026.11.107 prints, under `structured_output`, the modes
(`json_schema`, `json_object`, `tool_call`), the providers, and a new
`provider_modes` map: `anthropic` and `bedrock` list `json_schema`, `openai`
lists all three. The `--structured-output` flag lists the same three choices.
nare refuses `json_object` and `tool_call` on the anthropic rail at startup.

On a constrained turn (the structural correction, or every turn of a tool-free
run), the openai rail sends:

- `json_schema`: `response_format` json_schema, strict, as before;
- `json_object`: `response_format: {"type": "json_object"}`, syntax only;
- `tool_call`: only the forced `nare_final_answer` function with the schema as
  its parameters, `tool_choice` naming it and `parallel_tool_calls: false`.

Each constrained turn's `progress` record names the mode. nare validates every
answer against `--schema` in every mode.

### Design

- Configuration: `structured_output` accepts `json_object` and `tool_call`
  beside `json_schema`. The two new values require `provider: openai` and are
  refused at load on an anthropic rail, with the same "inherited from
  models.default; set it to null to clear it" wording when an anthropic override
  inherits one. Inheritance and `null` clearing are unchanged.
- Capability: inspection reads `provider_modes` into
  `NareCapability.structured_output_provider_modes`, and
  `structured_output_for(provider)` answers which modes a rail may send. A mode
  is offered on a provider only when the entry lists the mode and the provider,
  `provider_modes` lists the mode for that provider, and the advertised flag lists
  it among its choices. An entry without `provider_modes` (every release before
  2026.11.107) offers `json_schema` alone on its listed providers, exactly as
  before. A malformed `provider_modes` offers nothing, so only a review that asks
  depends on it.
- Refusal: `require_rail_options` refuses a rail whose encoding the runtime does
  not offer for its provider before any session launches (`capability: nare V
  cannot send structured output tool_call; ...`). The session layer refuses an
  openai-only encoding on any other provider as a second guard.
- Provenance and replay: the encoding is the rail's `structured_output` value,
  so `config.json`, `fanout.json`, `invocation.json` (rail and argv) and
  `result.json` (`{"mode", "constrained_turns"}`) already record it. Replay
  accepts any of the three modes in a rail record, and the existing checks then
  require argv, result and constrained-turn records to name the configured
  encoding (`structured_output_unrecorded`, `structured_output_result_disagrees`).
- The evaluation snapshot's `nare_structured_output`, written only when a rail
  opts in, lists every mode the runtime advertises, so on 2026.11.107 it now
  lists all three.

## Task 1: Configuration

Files: `src/scrutare/config.py`, `tests/test_structured_output_config.py`.

- [x] Tests: each new mode parses on openai, inherits and clears with `null`; an
  anthropic default or override refuses each at load; an anthropic override
  inheriting one names the inheritance and can clear it or choose `json_schema`.
- [x] Accept the modes and refuse them off openai.

## Task 2: Capability and refusal

Files: `src/scrutare/engine/session_models.py`,
`src/scrutare/engine/nare_session.py`, `tests/test_structured_output_encodings.py`,
`tests/test_structured_output_session.py`.

- [x] Tests against fake contracts: the 2026.11.107 entry gives all three modes
  on openai and `json_schema` on anthropic; each mode needs the entry, the
  provider, `provider_modes` and the flag choice; a malformed `provider_modes`
  offers nothing.
- [x] Tests: a 2026.11.100-shaped runtime refuses a review (default rail or
  override) that asks for either encoding, while a default review and a
  `json_schema` review pass, and a default rail sends the same argv on both
  runtimes.
- [x] Tests: one session in each encoding sends and records it; a constrained-turn
  record in another encoding is a protocol failure; a `json_schema`-only runtime
  never launches it.
- [x] Read `provider_modes` and gate each rail on its own provider.

## Task 3: Replay

Files: `src/scrutare/replay/audit.py`, `tests/test_replay_structured_output.py`.

- [x] Tests: a run in each encoding replays clean; a session that sent another
  encoding, an argv naming another encoding, markers or a result in another
  encoding are invalid; an anthropic rail recording `tool_call` is invalid config.
- [x] Accept the three modes in a recorded rail.

## Task 4: Installed console

Files: `tests/test_installed_structured_output.py`,
`tests/test_installed_capabilities.py`.

- [x] The installed 2026.11.107 console advertises all three modes on openai and
  `json_schema` on anthropic.
- [x] Through the real openai transport and the offline HTTP harness, a review in
  each new encoding sends `--structured-output MODE`, and the correction request
  carries `response_format` json_object or the forced `nare_final_answer`
  function; the streamed answer call decodes into the same verdict, and replay
  is clean.
- [x] Each encoding's argv equals the unconstrained argv plus its one option pair.

## Task 5: Documentation

- [x] `docs/config.md`, `docs/session-fanout.md`, `docs/replay.md`.

## Screening the encodings (follow-up, not in this change)

The screens run against live models and belong to the #92 follow-up work. Each
uses the development corpus, never the holdout, for `onprem/glm-5.3-flash` and
each #92 candidate. One schedule per model runs the same cases under each of
`json_schema`, `json_object` and `tool_call`, changing only the rail's
`structured_output`, so the recorded argv differs only in that pair (Task 4
proves this). For each model and encoding the report gives the session-cause
table the GLM packages use (complete, schema violation, protocol, provider,
timeout and the other failure causes), plus constrained turns per session from
`result.json`. The encoding the self-hosted rail profile (#133) ships is the one
with the fewest schema and protocol failures at no loss of findings quality;
ties keep `json_schema`.

## What stays unchanged

- A rail that sets no `structured_output` sends the same argv and request bytes,
  and records the same artifacts, as before.
- A `json_schema` rail sends the same argv and request as before.
- The anthropic rail offers `json_schema` only.
- Runtimes before 2026.11.107 are inspected as before; asking them for a new
  encoding is refused before any session launches.
