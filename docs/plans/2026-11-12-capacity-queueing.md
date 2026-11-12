# Capacity queueing

## Goal and design

Let a review wait out a provider capacity refusal instead of failing on it,
using the queue nare 2026.11.100 offers. This builds on
[the pin of that release](2026-11-12-nare-2026-11-100.md), which sends no
capacity option. The rules of the earlier plans stay: capabilities are read
from `nare contract`, new behavior is an opt-in rail field whose default
reproduces today's invocation exactly, and nothing changes what a
default-configuration review sends or how its verdict is derived (the #51
promotion gate is still open).

No live model call, credential, holdout content or deployment is part of this
work. Every proof runs offline against the installed 2026.11.100 console.

### What nare offers

- Capacity queueing (nare #291): `--capacity-queue`, `--capacity-wait-seconds`,
  `--capacity-refusals`. A refusal that is HTTP 429 with the transport's
  capacity marker, raised before any response started, is waited out and the
  same request is sent again. Off by default. Only the openai rail declares a
  marker. Each queued refusal emits a `progress` event with `detail.capacity`;
  the result and session file gain `capacity_refusals` and the result
  `capacity_wait_seconds`.
- Adaptive capacity (nare #292): `--capacity-adaptive`, `--capacity-ceiling`,
  `--capacity-floor`, `--capacity-quiet-seconds`, `--capacity-shared-dir`. A
  limit on requests in flight per provider route, cut by refusals and raised
  after a quiet period. Off by default and needs the queue. Events carry
  `detail.capacity_limit`; the result gains `capacity_limit` and
  `capacity_slot_wait_seconds`.

This plan adopts the first and leaves the second as a written open question.

## Task 1: Capacity queueing (opt-in)

Files: config.py, engine/session_models.py, engine/nare_session.py,
engine/session_output.py, replay/audit.py, docs/config.md, docs/replay.md,
docs/session-fanout.md, config, session, replay and installed tests.
Interface: `ModelRail` gains `capacity_queue` (boolean), `capacity_wait_seconds`
and `capacity_refusals`. Off by default, openai rail only, recorded only when
some rail sets one, and refused before any session when the runtime does not
advertise the queue for the rail's provider.

- [ ] Config: validate at load, inherit through overrides, clear with `null`.
  The two limits need `capacity_queue: true` on the same rail and are not
  inherited by an override that turns the queue off.
- [ ] Capability: read the `capacity_queue` contract entry and the three flags.
  Contracts 1 and 2 never queue. The runtime must also list `--stream`: nare
  writes a queue record before the wait only on a streamed turn (a buffered
  turn's events are written when the step ends), and the timeout credit below
  depends on that order.
- [ ] Argv: `--capacity-queue --capacity-wait-seconds S --capacity-refusals N`
  on every session of the rail, correction turns included. Both limits are
  always passed, 1800 and 50 when the rail sets none (nare's values at this
  release), so a later nare default never decides them.
- [ ] Decoding: a `capacity` record is not a retry and not a turn. The result's
  `capacity_refusals` must equal the records in the stream, cumulative across a
  resumed correction turn. A record on a session that did not ask is a protocol
  failure, as an unrequested request retry is. That rule is what holds the
  default from this side: a rail that sets nothing was not told to queue, so a
  queued refusal is not its own.
- [ ] Timeout: see below.
- [ ] Records and replay: `result.json` records `{"enabled": false}`, or
  `{"enabled": true, "wait_seconds": S, "refusals_limit": N, "queued": M,
  "waited_seconds": W}`; replay binds rail, argv and record to the
  configuration and the retained streams.
- [ ] Installed proof through the real transport with a scripted HTTP boundary
  that refuses with 429 and the marker before it answers.

### Accounting

A queued refusal was never dispatched: nare adds no turn, no `cost` event and
no usage for it, and does not count it in `retries`. Scrutare counts tokens
only from `cost` events and reconciles their sum with nare's result, so a
refused or queued request cannot enter the ledger, once or twice. The installed
proof asserts it: the number of `cost` events equals the number of completed
replies, and the ledger equals the result.

### Queue time and the session timeout

`NareRuntime.timeout_seconds` is a liveness limit on one session process. A
queued session is alive and waiting on purpose, and a timeout there would be
the worst outcome: the process is killed, its accounting is incomplete, and
the ledger closes to further admissions. So queue time is credited:

- On a streamed turn nare writes each `capacity` record before it sleeps, with
  the `wait` it is about to take. Scrutare reads the stream live and moves the
  session's deadline out by that wait. The installed proof runs twelve seconds
  of queueing under an eight second timeout through the real transport.
- The credit is bounded. One session is credited at most its rail's
  `capacity_wait_seconds` in total, so a session's wall clock is at most
  `timeout_seconds + capacity_wait_seconds`. nare applies the same number per
  request; a session whose requests together queue longer than that runs the
  remainder on its ordinary timeout.
- Only announced waits are credited. A runtime that hangs without writing a
  record gets no credit, so the liveness limit still catches it.
- A correction turn continues the session's deadline and its credit.
- A rail that does not queue is given no credit at all, and its sessions wait
  on the code path they used before.

Limits outside the session are not moved: the App's job timeout and a CI job's
own limit still apply, and the guide says so.

### Open question: adaptive capacity is not adopted

`--capacity-adaptive` is left out on purpose. What would have to be decided:

1. Who owns the shared directory. Scrutare runs one nare process per persona,
   each making one request at a time, so a per-process limit never binds. The
   limit only means something with `--capacity-shared-dir`. A path in
   `scrutare.yaml` would let a reviewed repository name a host directory that
   nare creates lock and state files in, so it cannot be a configuration field.
   The candidates are a directory Scrutare creates per review (personas of one
   review share a limit, concurrent reviews do not) or an operator setting
   beside `NareRuntime` (one limit per host). They give different results and
   neither is obviously right.
2. Slot waits cannot be handled against the timeout. A request that finds the
   limit full polls for a slot with no bound, and nare reports the wait only
   after it ends (`reason: slot`). A session can therefore sit silent for
   longer than its timeout while it is healthy, and Scrutare cannot tell that
   from a hang. The credit above needs the wait announced before it starts.
   This needs a nare change (a record when a slot wait begins, or a bound on
   the wait) before Scrutare can account for it deliberately.
3. The gate. nare's contract says evaluation gates that must not adapt use
   fixed mode. A limit that persists between sessions makes one session's
   timing depend on another's refusals, which the evaluation runner would have
   to exclude or record.
4. The defaults (ceiling 8, floor 1, quiet 60 seconds) are nare's first values
   and have not been settled for review workloads.

Until then a session that shows a `capacity_limit` record is not failed for it
(Scrutare never asks for one, and the record changes no accounting), and the
result fields `capacity_limit` and `capacity_slot_wait_seconds` are ignored.

Also open: the two queue defaults Scrutare passes (1800 seconds, 50 refusals)
are nare's defaults at this release, not values measured for reviews.

## Task 2: Verification

- [ ] Full suite on Python 3.10 and 3.14 with `SCRUTARE_TEST_NARE_EXECUTABLE`
  at the 2026.11.100 build, `ruff check`, strict `mypy`. Compare failures with
  the same suite on the unchanged base commit and its pinned runtime.
- [ ] Disclosure guard on the pull request body.

## Review focus

A configuration that sets no queue field must produce the argv, records and
token accounting it produced before. A queued refusal must never reach the
ledger. The deadline credit must be bounded and must not be earned by a silent
process. A requested queue the runtime does not advertise must be refused
before any session.
