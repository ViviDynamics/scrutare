# Session timeout and cut-off recovery

Issue #147, part of #137.

## Goal and design

Two limits decide whether a slow self-hosted model can finish a review, and
both were fixed in code:

1. The review CLI, the MCP server and therefore the App built `NareRuntime`
   with its default `timeout_seconds=600`. At the decode rates the GLM
   campaigns saw (about 15 to 19 output tokens per second), one request at
   `self-hosted@1`'s `max_output_tokens: 20000` takes longer than that. Only
   the evaluator exposed `--timeout-seconds`.
2. Every contract 3 session sends nare `--max-cutoffs 0`, so a reply cut off at
   the output cap or by a repetition loop ends the session at once.

This change makes both configurable. The rules of the earlier plans stay:
capabilities are read from `nare contract`, new behavior is opt-in, and a
configuration that sets neither field sends the same argv and request bytes and
records the same artifacts as before. No live model call, credential, holdout
content or deployment is part of this work.

### Session timeout: `models.session_timeout_seconds`

- A positive finite number of seconds, at most 21600 (the App's own longest job
  deadline, so no configured session can outlive every App deadline), or
  `null`. Unset keeps 600.
- It lives under `models`, beside `capacity` and `preflight`, because it bounds
  each nare session of every rail alike. It is not a rail option: a review's
  sessions share one runtime, and a per-rail timeout would need per-rail
  runtimes for no measured need.
- It is recorded in `config.json` only when set, so every earlier configuration
  keeps its bytes. Each session already records the bound it ran under as
  `invocation.json` `timeout_seconds`.
- The CLI and the MCP server build their runtime from it. The App runs the CLI,
  so it follows. Without the field the runtime is the same `NareRuntime` as
  before, so the recorded `timeout_seconds` stays `600.0`.
- Evaluator precedence: `--timeout-seconds` is now optional. The configuration
  field wins when it is set, the flag applies when it is not, and 600 applies
  when neither is given. A flag that disagrees with a configured value is
  refused before anything runs, so a campaign never records one value in
  `config.json` and run under another.
- Replay binds it: when the captured configuration sets the field, every
  session's `invocation.json` must record that `timeout_seconds`
  (`session_timeout_unrecorded`). A run without the field is not audited, so
  earlier bundles and evaluator runs that used the flag replay as before.
- Capacity queueing still credits announced waits against the same deadline,
  up to `models.capacity.wait_seconds`, and a correction turn still continues
  its session's deadline.

### The App's fit check: refuse, not warn

A configured session timeout must fit inside `SCRUTARE_APP_JOB_TIMEOUT`. The
longest one session may run is its timeout plus the capacity wait credit
(`models.capacity.wait_seconds` when queueing is on). When that is not below
the job deadline, the worker refuses the job before any preflight or review
starts: status `failed`, `delivery: not_sent`, and an error that names both
values.

Refusing is the safe choice. A warning would let the job start, spend model
tokens, and be killed at the deadline as `interrupted` with a review that may
or may not have posted, which is the outcome the deadline work (#135) tried to
make rare. A refusal spends nothing, posts nothing, and tells the repository or
the operator exactly which value to change. The check only applies when the
field is set: a repository that sets nothing keeps today's behavior under any
deadline. The check is a one-session bound. A multi-round strategy runs more
than one session in sequence and needs a deadline sized from its timing
records, as `docs/app.md` already says.

### Cut-off recovery: rail option `max_cutoffs`

What nare does (nare `docs/contract.md`, "Cut-off replies", unchanged
from 2026.11.107 to the pinned 2026.11.109): a reply the provider cut off at the output cap (`stop_reason`
`max_tokens`), or one that repeated one span until nare stopped it
(`repetition`), is corrected while the invocation's budget lasts. A correction
is one user message asking the model to reason briefly, write each record once
and return the complete answer (and saying any tool calls in the cut-off reply
did not run). The cut-off reply counts as a turn against `--max-turns`, its
tokens are charged and reported in its `cost` event, and nare writes a
`progress` event with a `cutoff` detail (`kind`, `output_tokens`, `attempt`,
`limit`, `corrected`). With the budget spent, the run ends `error` with
`stop_reason` `max_tokens` or `repetition`, exactly as `--max-cutoffs 0` ends
at the first cut. The budget is per invocation, so a resumed correction turn
gets its own.

- `max_cutoffs` is an integer from 0 to 3, or `null`, on either provider. It
  inherits to overrides and an override clears it with `null`, like the other
  rail options. Unset sends the pinned `--max-cutoffs 0`. The cap is small
  because each correction is another full reply at the output cap.
- Gate: every contract 3 runtime must advertise `--max-cutoffs` taking a value
  (inspection already requires it). A rail that sets the option against an
  older contract is refused before any session launches.
- Provenance: the rail record in `invocation.json`, `fanout.json` and
  `config.json`, plus the argv value. The correction turn shares the flag with
  its session.
- Replay: `max_cutoffs` joins the audited rail options (the recorded rail must
  equal the configured rail), and the recovery policy check requires
  `--max-cutoffs` to equal the recorded rail's value, or 0 when the rail sets
  none.
- `self-hosted@1` does not change: a profile version never changes. Both
  settings are documented as available and unmeasured, and belong in a future
  `self-hosted@2` once a screen measures them.

## Task 1: Configuration

Files: `src/scrutare/config.py`, `tests/test_session_timeout_config.py`,
`tests/test_rail_options_config.py`.

- [x] Tests: `session_timeout_seconds` parses ints and floats, refuses zero,
  negatives, booleans, non-finite values and values above the cap, records only
  when set, and round-trips.
- [x] Tests: `max_cutoffs` parses 0 to 3 on either provider, inherits, clears,
  refuses out-of-range and non-integer values, and stays out of every record
  when unset.
- [x] Implement both.

## Task 2: Runtime timeout in each interface

Files: `src/scrutare/interfaces/cli.py`, `src/scrutare/interfaces/mcp.py`,
`src/scrutare/evaluation/__main__.py`, `tests/test_session_timeout.py`.

- [x] Tests: the CLI and MCP runtime take the configured timeout and keep 600.0
  without it; the evaluator precedence and the conflict refusal.

## Task 3: Cut-off argv and gate

Files: `src/scrutare/engine/nare_session.py`, `tests/test_rail_options_session.py`.

- [x] Tests: unset and 0 send `--max-cutoffs 0`; 1 to 3 send the value and record
  the rail; an older contract refuses the option for the wave and the session.

## Task 4: Replay

Files: `src/scrutare/replay/audit.py`, `tests/test_replay_rail_options.py`,
`tests/test_replay_session_timeout.py`.

- [x] Tests: a run with `max_cutoffs` replays clean; a changed argv value or a
  recorded rail that differs from the configuration is invalid; a session that
  ran under another timeout than the configured one is invalid; runs without
  either field are not audited.

## Task 5: App fit check

Files: `src/scrutare/interfaces/app_worker.py`, `tests/test_app_session_timeout.py`.

- [x] Tests: a configured timeout (plus capacity credit) at or above the job
  deadline is refused before preflight and the CLI; one that fits runs; an
  unset timeout never refuses.

## Task 6: Installed console

Files: `tests/test_installed_cutoffs.py`.

- [x] Through the real openai transport and the offline HTTP harness, a review
  with `max_cutoffs: 1` whose first reply is cut off at the output cap is
  corrected and completes, nare reports the corrected cut-off, and replay is
  clean; the same script without the option fails at the first cut.
- [x] The argv with `max_cutoffs` equals the default argv but for the one value.

## Task 7: Documentation

- [x] `docs/config.md`, `docs/self-hosted.md`, `docs/app.md`, `docs/replay.md`.

## What stays unchanged

- A configuration that sets neither field sends the same argv and request
  bytes, and records the same artifacts, as before.
- `self-hosted@1` and its tested defaults.
- The evaluator's default of 600 seconds when neither the flag nor the
  configuration sets a timeout.
