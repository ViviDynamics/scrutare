# Cap per-request output tokens and re-qualify spark/glm-5.3-flash under a new gate

Issue #82

## Scope
In (this PR, the cap): an opt-in `max_output_tokens` on `models.default` and
`models.overrides.<persona>`, passed to nare as `--max-tokens` on every session of
that rail, recorded in the run's configuration, fan-out manifest, `invocation.json`
and `result.json`, and audited on replay. A session whose request hits the cap
fails as `output_cap`, with its usage in the shared ledger and no findings.

Out: the live smoke, the new gate and the qualification run. They follow in
separate steps and a separate publication PR, like #79 and #80/#81.

## Assumptions
- nare 2026.10.12 (`96672e1`, the pinned release) has `run --max-tokens`. Its OpenAI
  transport sends `max_tokens` only when the flag is given, and maps
  `finish_reason: length` to `max_tokens`. A capped final turn or tool turn ends the
  session with `status: error, stop_reason: max_tokens` (the tool calls in a cut-off
  turn are not run). Every release from 2026.10.0 has the flag, so no capability
  gate is needed.
- The setting applies to any provider. nare forwards it to either transport and
  refuses values its transport cannot honour before any model call.
- The value is a positive integer or null. Overrides inherit it and can clear it
  with `null`, as with `structured_output`.
- Off is byte-identical: no new key in canonical config, rail records,
  `invocation.json` or `result.json`, and argv is unchanged. When any rail opts in,
  every rail records the key, `null` where off, so the record re-parses.
- Attribution: with the cap on, a nare stop reason of `max_tokens` (whatever its
  status) is the `output_cap` failure. Its usage was reported by a completed
  response, so accounting is complete. With the cap off, behaviour is unchanged
  (a backend's own length stop stays a `provider` failure).
- A capped session never contributes findings or corrections, even if an earlier
  tool turn carried a schema-valid document.

## Tasks
- [ ] 1. Configuration: parse, inherit, clear, refuse invalid values, record only
  when used: `tests/test_max_output_tokens_config.py`.
- [ ] 2. Session: `--max-tokens` in argv, rail and result records; a capped session
  fails as `output_cap` with complete accounting and no findings; off unchanged:
  `tests/test_max_output_tokens_session.py`.
- [ ] 3. Fan-out manifest records the cap on every rail when any rail opts in, and
  the off bytes are unchanged: `tests/test_max_output_tokens_session.py`.
- [ ] 4. Replay audits the recorded cap against the configuration and the retained
  stream: `tests/test_replay_max_output_tokens.py`.
- [ ] 5. Installed native nare 2026.10.12 proofs over HTTP SSE: cap on (every
  request carries `max_tokens`), off (none does), a request cut off at the cap, and
  replay of each: `tests/test_installed_max_output_tokens.py`.
- [ ] 6. Documentation: `docs/config.md`, `docs/replay.md`, `docs/session-fanout.md`.
