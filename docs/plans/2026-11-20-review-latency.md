# Review latency and the App deadline

## Goal and design

Nobody knows how long a review takes on the self-hosted rail, or whether it
fits the App (#135, part of #137). The App gives each job a 3300 second
deadline so it finishes inside one installation token's hour. A slower rail
needs a longer deadline, and a longer deadline needs a token that outlives the
first one.

This change does two things:

1. Record latency for every review, hosted and self-hosted, in the run's
   artifacts, the CLI result, the App's job record and one line of the posted
   summary.
2. Refresh the installation token while a job runs, so the deadline can be set
   for the self-hosted rail without a review failing at the post.

The rules of the earlier plans stay. Nothing here changes the argv or request
bytes a review sends to nare, nor how a verdict is derived. Latency is a
measurement: replay checks its form and consistency, never equality with a
rerun.

No live model call, credential, deployment or live infrastructure is part of
this work. Every proof runs offline, against scripted consoles and the
installed 2026.11.107 console.

### What nare offers, and what it does not

Read at 2026.11.107 (`docs/contract.md`, `src/nare/events.py`,
`src/nare/loop.py`):

- Every event has an ISO `timestamp`, taken when the event is built.
- Each model request that completes ends in a `cost` event with its usage and
  nothing about time.
- Each resend emits a `progress` event with `detail.retry.wait`, the seconds it
  waits before the next attempt.
- The result line carries `capacity_wait_seconds` and
  `capacity_slot_wait_seconds`, which Scrutare already records (#126).
- No event marks when a request was sent or when its first byte arrived. With
  `--stream`, only text and reasoning arrive as deltas, and a turn that only
  calls tools streams none. An unstreamed turn's events are all emitted when it
  ends.

So time to first token and generation time (and with it decode rate) cannot be
computed reliably by a caller. Filed as ViviDynamics/nare#315, asking for
per-request timing on each `cost` event. Until it lands both fields are
recorded as `null`, never estimated.

### Where the records live

`verdict.json` is recomputed byte for byte by replay, so a measurement cannot
go there. Each launched session writes `timing.json` beside its `result.json`,
and each review run writes one `timing.json` at its root, after its verdict and
before anything is posted. That keeps `result.json`, `fanout.json` and the
outcome dictionaries byte for byte what they were, and gives the poster a fixed
record to render, so a resumed post renders the same payload.

## Task 1: Session records

Files: engine/timing.py, engine/session_output.py, engine/nare_session.py,
engine/session_artifacts.py, tests/test_timing.py.

- [x] `SessionTiming(provider, model, invocations, wall_seconds, tokens,
  output_tokens, retries, retry_wait_seconds, capacity_wait_seconds,
  capacity_slot_wait_seconds)`, strictly validated, seconds rounded to
  milliseconds, with `first_token_seconds` and `decode_tokens_per_second`
  always `null`.
- [x] Wall clock from the session's launch (once `invocation.json` is written)
  to its last record, on the event loop's monotonic clock, correction turn
  included.
- [x] `retry_events` counts resends and sums their announced waits from the
  retained streams, tolerantly, like `capacity_events`.
- [x] Capacity waits are the session's recorded `capacity` counters, 0 without
  the block.
- [x] Written before `result.json`, so a session whose record could not be
  written has neither, as an unwritable result always left it.
- [x] A session that never launched writes none.

## Task 2: Review record and CLI result

Files: engine/timing.py, engine/review.py, engine/panel_artifacts.py,
tests/test_review_run.py.

- [x] `review_pr` measures from its start (capture included) to the verdict and
  writes the run's `timing.json`: totals over the session records and a
  breakdown per provider and model.
- [x] Each whole-review retry attempt gets its own record. A failed review
  writes one best effort before its manifest, without hiding the primary
  error.
- [x] `ReviewRunResult` carries it as `timing`. Deliberate change: the two
  exact-result tests in `test_review_run.py` now pop and check `timing`.
- [x] `timing.json` is prior evidence for a panel reservation, like
  `verdict.json`.

## Task 3: Summary footer

Files: poster/payload.py, poster/posting.py, config.py,
tests/test_timing_footer.py.

- [x] `build_review_payload(footer=...)` places one plain line above the run
  marker. Without it the body is unchanged, so no existing exact-output payload
  test changes.
- [x] `post_review` renders the line from the run's `timing.json`, after
  checking it against its session records. A record that disagrees refuses the
  post before any write.
- [x] `github.timing_footer: false` turns it off, recorded in `config.json` only
  when `false`, so default configurations keep their canonical bytes.
- [x] Escalation bodies keep their targets readable to replay.

## Task 4: Replay

Files: replay/audit.py, tests/test_timing.py, docs/replay.md.

- [x] Each launched session that wrote a result has a record
  (`timing_missing`).
- [x] The record names the invocation's rail, counts the correction stream,
  matches the result's tokens and capacity counters, and its retry count and
  waits are recomputed from the streams (`timing_disagrees`).
- [x] The run's record is the sum of its sessions' and is not shorter than its
  longest session.
- [x] Wall clock is never compared with anything else. Runs without records are
  not audited, so earlier bundles replay as before.

## Task 5: App deadline and token refresh

Files: interfaces/app.py, interfaces/app_worker.py, engine/github.py,
poster/client.py, tests/test_app_token_refresh.py, tests/test_app_worker.py,
docs/app.md.

- [x] The worker writes the token to a private job file and names it in
  `SCRUTARE_GH_TOKEN_FILE`. A background thread replaces it every 30 minutes
  while the CLI runs, retries a refused refresh after a minute and removes the
  file when the job ends.
- [x] `gh_environment()` reads the file before every gh call, capture and
  posting included, and falls back to the inherited `GH_TOKEN`.
- [x] `SCRUTARE_APP_JOB_TIMEOUT` accepts 1 to 21600. The default stays 3300.
  Deliberate change: the bound test now rejects 21601 instead of 3301.
- [x] Job records that ran the CLI carry `job_seconds` and `token_refreshes`,
  and the run's `timing` (with its rails) on success, or the latest run's on
  failure.
- [x] No change to `deploy/` or the deployed App; the new range is documented.

## Task 6: Verification

- [x] Targeted tests on Python 3.14 and 3.10, with
  `SCRUTARE_TEST_NARE_EXECUTABLE` at the 2026.11.107 build, including the
  installed retry proof, which now checks the session's timing against the real
  stream.
- [x] `ruff check` and strict `mypy`.
- [x] Disclosure guard on the pull request body.
- [ ] Linux CI on the pull request.

## Remaining work (not this change)

- [ ] The latency report: median and p90 review time per rail on the
  development corpus, from real reviews. It needs live runs.
- [ ] Set the App's deadline and decide concurrency or a smaller self-hosted
  panel (#109) from that report.
- [ ] Record time to first token and decode rate once nare reports
  per-request timing (ViviDynamics/nare#315), gated on `nare contract`.
- [ ] Dollar cost per review stays with #116.

## What stays unchanged

- The argv and request bytes of every nare invocation, in every configuration.
- `verdict.json`, `findings.json`, session `result.json`, `fanout.json` and the
  other outcome documents.
- The posted summary of a run without a review timing record, and of any run
  configured with `github.timing_footer: false`.
- The App's default deadline (3300 seconds), its single worker, and every
  `deploy/` manifest.

## Review focus

No byte sent to nare may change. A measurement must never reach a verdict or
make replay depend on a rerun's clock. The footer must be deterministic for a
resumed post. The token file must stay private, be replaced atomically and be
removed at the end of a job, and a refused refresh must leave the current token
in place.
