# Capacity queueing and adaptive capacity

## Goal and design

Let a review wait out a provider capacity refusal instead of failing on it, and
let the personas of one review share an in-flight limit, using what nare
2026.11.100 offers. This builds on
[the pin of that release](2026-11-12-nare-2026-11-100.md), which sends no
capacity option.

Two earlier proposals are combined here. One exposed a review-wide
`models.capacity` block with fixed and adaptive modes, a per-review shared
directory and a `capacity` record. The other exposed per-rail fields for the
queue only, credited queue time against the session timeout and failed a
session on capacity evidence it had not asked for. This plan takes the block,
the modes, the directory and the record from the first, and the deadline
credit and the protocol checks from the second. There is one configuration
surface and one record shape.

The rules of the earlier plans stay: capabilities are read from `nare
contract`, and nothing changes what a default-configuration review sends or
how its verdict is derived (the #51 promotion gate is still open). With no
`models.capacity` block, argv, accounting, artifacts and the session wait path
are what they were.

No live model call, credential, holdout content or deployment is part of this
work. Every proof runs offline against the installed 2026.11.100 console.

### What nare offers

- Capacity queueing (nare #291): `--capacity-queue`, `--capacity-wait-seconds`,
  `--capacity-refusals`. A refusal that is HTTP 429 with the transport's
  capacity marker, raised before any response started, is waited out and the
  same request is sent again. Only the openai rail declares a marker. Each
  queued refusal emits a `progress` event with `detail.capacity`; the result
  and session file gain `capacity_refusals` and a wait total.
- Adaptive capacity (nare #292): `--capacity-adaptive`, `--capacity-ceiling`,
  `--capacity-floor`, `--capacity-quiet-seconds`, `--capacity-shared-dir`. A
  limit on requests in flight per provider route, cut by refusals and raised
  after a quiet period, shared by the processes that name one directory.
  Events carry `detail.capacity_limit`; the result and session file gain the
  last limit and a slot wait total.

## Task 1: Configuration

Files: config.py, tests/test_capacity_config.py, docs/config.md.
Interface: `ModelSettings.capacity` is a `CapacitySettings(mode, wait_seconds,
refusals, ceiling, floor, quiet_seconds)`.

- [ ] `mode` is `off` (default), `fixed` (the queue) or `adaptive` (the queue
  and the shared limit). Each enabled mode requires every value it uses, so no
  nare default is inherited, and refuses a value it does not use.
- [ ] Upper bounds with their own messages: `wait_seconds` 3600, `refusals`
  1000, `ceiling` 64, `quiet_seconds` 3600. `floor` must not exceed `ceiling`.
- [ ] An enabled mode needs at least one openai rail. The block applies to
  every openai rail of the review and to no other rail.
- [ ] `off` is never recorded, so a configuration without the block keeps its
  canonical bytes.

## Task 2: Capability and argv

Files: engine/session_models.py, engine/nare_session.py, session tests.
Interface: `NareCapability.capacity` lists the modes the runtime offers.
`capacity_argv(rail, capacity, run_dir)` is the flags one invocation passes.

- [ ] Read the modes from the `capacity_queue` and `capacity_adaptive` contract
  entries and the advertised flag list, as the other options are read.
  Contracts 1 and 2 offer none.
- [ ] Refuse a wave before any session when the runtime lacks the configured
  mode, or does not list `--stream`: the deadline credit below reads queue
  records that only a streamed turn writes before the wait.
- [ ] Pass the flags on the first invocation and on a resumed correction turn
  of every openai rail, every value explicit.
- [ ] Adaptive mode names the review run's own `capacity/` directory, created
  owner-only beside `review-inputs/` and `sessions/`, outside every persona's
  read root, never through a link and never from configuration.

## Task 3: Accounting, protocol checks and records

Files: engine/session_output.py, engine/nare_session.py,
engine/session_models.py, decoder and session tests.
Interface: `CapacityUsage(refusals, wait_seconds, limit, slot_wait_seconds,
source)` on a session outcome, recorded as `capacity` in `result.json`, with
wave totals under `result.capacity` in `fanout.json`.

- [ ] A queued refusal was never dispatched: nare adds no turn, no `cost` event
  and no usage for it. Tokens are counted only from `cost` events, so nothing
  about the ledger changes.
- [ ] Cross-check nare's result line against its saved session: counts equal,
  seconds equal after rounding to milliseconds, and nothing shrinks over a
  resumed correction turn.
- [ ] Reconcile the stream with the result: the number of queue records and
  the sum of their announced waits must equal nare's totals, cumulative over a
  resumed turn, and so must the reported slot waits. A stream that shows a
  limit record needs a result that reports a limit.
- [ ] Evidence the session did not ask for is a protocol failure: any capacity
  record or nonzero counter on a session sent no capacity flag, and any limit
  record, limit or slot wait on a session sent the queue alone.
- [ ] A session launched with the flags always records `capacity`, zeros
  included, with `source: result`. One that ends without nare's result (a
  timeout, a protocol failure) records what its retained streams show, with
  `source: events`.

## Task 4: Timeout

`NareRuntime.timeout_seconds` is a liveness limit on one session process. A
queued session is alive and waiting on purpose, and a timeout there would be
the worst outcome: the process is killed, its accounting is incomplete, and
the ledger closes to further admissions.

- [ ] Queue waits are credited. On a streamed turn nare writes each `capacity`
  record before it sleeps, with the `wait` it is about to take. Scrutare reads
  the stream live and moves that session's deadline out by the wait.
- [ ] The credit is bounded: one session is credited at most the configured
  `wait_seconds` in total, so its wall clock is at most the timeout plus that
  bound. A correction turn continues the session's deadline and its credit.
- [ ] Only records earn credit. A process that goes silent is still stopped at
  its deadline.
- [ ] A session sent no capacity flag is given no credit and waits on the code
  path it used before.

### Slot waits

What nare does, read at the pinned commit (`src/nare/loop.py` `_admitted`,
`src/nare/capacity.py` `RouteLimiter.acquire`): before each model request
under an adaptive limit, the process polls for a slot every 0.05 seconds. The
poll has no bound and writes nothing. Once a slot is granted, nare emits its
`capacity_limit` records for that request: any raise it found, and one with
`reason: slot` and the seconds `waited`. So a slot wait is reported only after
it has ended.

- [ ] A completed slot wait is credited like a queue wait, from its record,
  under the same per-session bound. The time was spent waiting on purpose and
  nare has said so.
- [ ] A slot wait in progress cannot be credited. There is no record yet, and
  from outside a process polling for a slot looks the same as one that hung.
  Guessing from the lock files in the shared directory was considered and
  rejected: a waiting process holds no lock. The session timeout stays
  authoritative, so a session whose one slot wait outlasts its remaining time
  is stopped as `timeout` with incomplete accounting, and its `capacity`
  record counts what its stream showed (`source: events`).

## Task 5: Replay

Files: replay/audit.py, tests/test_replay_capacity.py, docs/replay.md.

- [ ] Each launched session's capacity flags, in order and with their values,
  must be the ones its configured block gives its recorded provider, with the
  shared directory of the run that recorded it.
- [ ] `result.json` carries a well-formed `capacity` record exactly when the
  flags were passed.
- [ ] The recorded refusals, queue seconds and slot seconds are recomputed
  from the queue and slot records in the retained streams.
- [ ] A correction turn repeats the session's capacity flags and values.
- [ ] Runs without the block, the flags or the record are not audited.

## Task 6: Installed proof and verification

- [ ] Through the real openai transport with a scripted HTTP boundary that
  refuses with 429 and the marker: refused requests are resent byte for byte in
  fixed and adaptive mode, only answered requests are usage, a refusal past a
  limit or with the block off ends the session, a resumed correction turn keeps
  the counters cumulative, queue time is credited against the timeout, and the
  run replays clean.
- [ ] Full suite on Python 3.10 and 3.14 with `SCRUTARE_TEST_NARE_EXECUTABLE`
  at the 2026.11.100 build, `ruff check`, strict `mypy`, wheel build and CLI
  smoke. Compare failures with the same suite on the unchanged base commit.
- [ ] Disclosure guard on the pull request body.

## Open questions

1. A slot wait in progress is silent and unbounded, as above. Closing this
   needs nare to write a record when a slot wait begins, or to bound the wait.
   Until then adaptive mode under heavy contention can still end a healthy
   session as `timeout`.
2. One limit per review run. Concurrent reviews against one proxy each hold
   their own limit, so together they can exceed what the proxy allows. A
   host-wide directory would fix that and would have to be an operator
   setting, not configuration a reviewed repository controls.
3. The evaluation gate. nare's contract says evaluation gates that must not
   adapt use fixed mode. Nothing here stops an evaluation run from configuring
   `adaptive`, where one session's timing depends on another's refusals.
4. Values. Scrutare's default is `off` and it supplies no values. The guide's
   example uses nare's own defaults (1800 seconds, 50 refusals, ceiling 8,
   floor 1, quiet 60 seconds), which have not been measured for reviews. The
   upper bounds are Scrutare's choice.
5. Limits outside the session are not moved: the App's job timeout and a CI
   job's own limit still apply to a session that queues.
6. The shared directory must be on a local filesystem, because nare uses
   `flock`. Neither Scrutare nor nare checks this.

## Review focus

With no block, no byte of argv, configuration or artifact may differ, and no
session may take the new deadline path. A refused or queued request must never
reach the ledger. The deadline credit must be bounded and must not be earned
by a silent process. A configured mode the runtime does not offer must be
refused before any session. Replay must reject lost, altered or unconfigured
capacity flags and a record its streams do not support, and must not require
anything of earlier bundles.
