# Nare 2026.11.99 and opt-in capacity queueing

## Goal and design

Run Scrutare against released nare 2026.11.99 (still contract 3) and expose its
capacity-refusal queueing (nare #291) and adaptive in-flight limits (nare #292)
as one opt-in, validated `models.capacity` block. A proxy that refuses a request
for capacity charged nothing, so waiting and resending the identical request is
admission, not a retry. Keep it distinct from Scrutare's recovery policy:
`--request-retries 0` stays, refusals are accounted apart from usage and retries,
and a refusal past nare's limits is an ordinary session failure.

Every value an enabled mode uses is explicit, so no nare default is inherited.
Scrutare's default is `off`, which keeps argv, configuration and artifacts
byte-identical. Adaptive sessions of one review run share one limit through a
private `capacity/` directory in the run directory. No model calls, holdout
inspection, quality promotion, budget or deployment changes are included.

## Task 1: Compatibility and capacity integration

Files: config.py, engine/nare_session.py, engine/session_output.py,
engine/session_models.py, replay/audit.py and their tests.

- [x] Show a 2026.11.99-shaped session (capacity result/session fields and
  progress events) decodes unchanged before any code change.
- [x] Validate `models.capacity` (off, fixed, adaptive; required values; OpenAI
  rail) and record it only when enabled.
- [x] Read advertised capacity modes from `nare contract`; refuse a wave whose
  configured mode the runtime lacks before any session.
- [x] Pass explicit capacity flags after the recovery controls on initial and
  resumed correction invocations of OpenAI rails; create the run's shared
  directory owner-only for adaptive mode.
- [x] Decode and cross-check capacity counters, monotonic over a resumed
  session; count progress events when no result exists; record them in
  `result.json` and wave totals in `fanout.json`, apart from usage.
- [x] Audit capacity flags, the shared directory and result records in replay;
  bind correction turns to the same values; leave runs without them unaudited.

## Task 2: Release pin and full verification

Files: Dockerfile, CI/release workflows, smoke scripts, installed tests and guides.
Interface: every installation uses release 2026.11.99 at commit
05a7fe07f6539a9c92b81a44b4f59866fc53d670 in a separate Python 3.14 environment.

- [x] Update release pin and installed test expectations, retaining historical
  minimum-version documentation where it describes an older capability.
- [x] Prove offline with the installed runtime that refusals are waited out and
  resent byte for byte, accounted apart from usage, fail past the limit, fail as
  before when off, and replay cleanly.
- [x] Run full tests including native Docker, lint, strict types, wheel and
  installed CLI smoke sequentially on Python 3.10 then Python 3.14.
- [x] Obtain independent branch review; fix substantive findings with tests.
- [x] Run disclosure guard, push feature branch, create and link PR.

## Pending decision

Scrutare's defaults stay `off`. nare's defaults (wait 1800 s, 50 refusals,
ceiling 8, floor 1, quiet 60 s) appear only as documented example values until
they are approved for Scrutare.

## Review focus

Off must change no byte. Refusals must never reach token budgets, the ledger or
failed coverage. Replay must reject lost, altered or unconfigured capacity flags
and never require them of earlier bundles.
