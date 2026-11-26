# Campaign resume, screening tier and rail watch

Issue #134; amends the #51 protocol by the maintainer's decision of 2026-11-26.
Part of #137.

## Goal

A full self-hosted qualification takes 11-20 hours, and so far every
infrastructure fault has ended one for good. GLM attempts 2-5 halted after 2-31
minutes each. Every halt meant a new packet, a new freeze and a dated amendment,
and none of them said anything about the model. This plan adds three things.
None of them loosens the gate:

1. **Resume.** A campaign halted by its rail continues under the same freeze.
   Scored cells are never rerun or rescored.
2. **Screen.** A six-cell development screen. A candidate must pass it before a
   full qualification is frozen.
3. **Rail watch.** A measured objective for the rail. A campaign does not
   launch on a rail that fails it.

No live model call, credential, holdout content or deployment is part of this
work. Every proof runs offline.

## Design

### Resume (`run --resume HALT`)

- Only rule A campaigns resume, and only after a halt of one of four kinds:
  `transport`, `gateway`, `preflight` or `launch`. The launcher's guard
  classifies the halt, as it does today. Scrutare records the kind and refuses
  any other.
- The same arguments rebuild the snapshot, and every field fixed at freeze must
  match it exactly. The fields filled in after freeze are the nare version and
  contract, which must also equal the runtime's, and the preflight, which each
  segment records itself. The resume's own preflight runs before it touches the
  campaign.
- Cell state comes from what is on disk:
  - **Retained.** The cell has a record that is neither `missing` nor
    `Interrupted`, in `experiment.json` or in its own `evaluation.json` (that
    covers a record the process wrote but had not yet persisted). It keeps the
    record unchanged.
  - **Halted.** The cell appears in `started.jsonl` but has no final record. A
    rule A cell writes that journal line before any spend. The cell's directory
    moves to `_halted/segment-NNNN/`, the cell is prepared again from the frozen
    bytes, and the stopped attempt is kept under `halted_attempts` with its
    observed usage as a lower bound.
  - **Unstarted.** The cell's capture and prepared hashes must still match the
    freeze.
- `halted_usage` is reported apart from `usage` and `cumulative_usage`, and
  scoring reads neither.
- Rule A's order holds. Originals finish before replays begin, a finished replay
  is retained, and a halted replay replays again without touching its original.
  A replay that finished but was never persisted before a kill is set aside and
  runs again. That costs one replay, never a rescore.
- A halt before the first cell starts (while the launch is still preparing
  cells) leaves no segment record and cannot be resumed. Nothing was spent, so
  relaunch into a fresh output directory.
- A job's status is `complete` only when every cell has a final record and no
  rule A replay is still owed. Before this change, a job cancelled after each
  cell had any record read `complete`, and so did a job killed in its replay
  phase.
- The resume checks every cell before it changes anything, so a refused resume
  leaves the campaign as the halt left it. It claims its segment before it moves
  any directory. It adopts any `_halted/` directory that no record mentions, so
  a resume killed while moving cells loses no attempt. A cell that was set aside
  and prepared again, but never ran, is not counted as halted a second time.
  Every set-aside attempt stays on its cell's record until the cell runs again,
  so a second halt cannot drop the first one's spend.
- `segments.json` records each segment's halt kind, start time, preflight, nare
  version, retained cells and halted cells.

### Screen (`run --screen`, `screen-gate`)

- `evaluations/screens/screen-v1.json` is fixed before any screen ran: the first
  labeled-defect case (`dev2-01`) and the first clean case (`dev2-03`) of
  `development-v2`, in manifest order, bound to that manifest's SHA-256.
- A screen runs one arm under rule A. That gives 6 cells against the 36 of a
  qualification, about an hour on the GLM rail.
- The gate passes only when every cell is complete with complete accounting,
  the same bar each qualification cell must meet. A screen never reads the
  holdout.

### Rail watch (`rail-check`)

- Repeats the route preflight's two checks, with both on, every 60 seconds over
  a 30-minute window, and probes at least 30 times.
- The request check is one streamed, schema-bound request through nare, sent
  with the rail's own options. It covers route availability and stream
  completion in one probe.
- Objective v1 is strict: a single failed probe fails the rail. The report gives
  the typed reason and p50/p95 request latency, and names rails by provider and
  model only.

## #51 amendment (adopted 2026-11-26)

These rules apply from the next self-hosted qualification. Earlier campaigns,
their packets and results are unchanged.

1. **An infrastructure halt is resumed, not restarted.** If the launcher's guard
   halts on a transport, gateway, preflight or launch fault, the same packet and
   freeze continue with `run --resume`. Each resume is recorded in
   `segments.json` and announced on #51 before its first model call.
   - A model or protocol fault still ends the campaign. So do a cap, the
     circuit breaker or a mismatch.
   - Retained cells are never rerun or rescored.
   - A halted cell runs again from its frozen inputs. Its stopped attempt stays
     on its record, and the stopped spend stays in the ledger and in
     `halted_usage`.
   - The 100-charge circuit breaker and the ledger and request caps count across
     every segment.
2. **Screen before freeze.** A candidate model or answer encoding is frozen for a
   full qualification only after `screen-v1` passes on the same configuration.
   A failed screen is reported, and the candidate is not frozen.
3. **Rail watch before every launch and resume.** A campaign or a resume
   launches only after `rail-check` passes, and its report is retained in the
   packet.

Unchanged: rule A, the 36-cell matrix and its gate, the non-inferiority pass
rule, seeds, holdout access, runtime and proxy proof, zero retries, and every
earlier GLM amendment.

## Work outside this repository

The launcher's guard lives in the private campaign files (#121), and it has to
do three things:
- classify a halt into one of the four kinds;
- keep the circuit breaker and caps across segments;
- call `rail-check` and then `run --resume` instead of declaring the campaign
  terminal.

## Tasks

- [x] Resume with retained, halted and unstarted cells, segments, halted usage
  and refusals; offline tests for each halt point, including the replay phase
  and a killed process.
- [x] `screen-v1` manifest, `run --screen` and `screen-gate`, with tests.
- [x] `rail-check` with objective v1, with tests on a fake clock and probe.
- [x] Docs: evaluation, self-hosted.
- [ ] Full suite, ruff, mypy, disclosure guard, exact-head CI, merge.
- [ ] Post the amendment on #51; update #134 and #137.
