# Recorded transient retry, measurable development corpus and qualification

Issue #51

## Scope
In: diagnose the residual Sol100k qualification failures from retained evidence;
add an opt-in, bounded, fully retained evaluator retry for upstream HTTP 5xx
session failures; let a comparison run an explicit subset of its arms; add an
original development-only corpus whose changed paths activate the built-in
specialist rules and whose code contains plausible-but-wrong allegations; then
prospectively freeze and run one qualification schedule and, only if it
qualifies, the highest-value component comparison that fits the spend limit.
Out: product default changes, in-session product retries, altered schemas, gates
or validators, human adjudication, any use of `evaluations/holdout-v1`, closing
#51 or any component issue.

## Diagnosis of the Sol100k residual (retained evidence, no new calls)
- All six non-complete cells (five invalid, one failed) contain exactly one
  discovery session whose native result is `status: error` with
  `chat completion failed with HTTP 500` and the proxy's upstream overload text.
  Every other session in those cells completed. Six cells, six HTTP 500s.
- No schema, citation, budget, turn-limit or timeout refusal occurs in that
  schedule. No protocol or product defect is implicated by the residual.
- Three of the six failures hit the three concurrently running repeats of one
  cell within one second, so the overload is bursty, not independent per call.
- The status difference is classification only: an unassessed panel reports
  `failed`; an assessed panel without a verdict is recorded `invalid`.

## Assumptions
- nare 2026.10.4 has no retry on its OpenAI rail and no flag for one (its
  Anthropic rail delegates to the vendor SDK). Nothing existing can be enabled.
- A product in-session retry is rejected: after a provider failure the ledger
  deliberately marks accounting uncertain and seals admission, because a failed
  call's billing is unknown. Retrying inside the review would either weaken that
  invariant or still yield an unaccounted cell. Reliability upstream of the
  engine belongs in nare or the proxy.
- The evaluator therefore reruns the whole cell in a fresh directory prepared
  from the same frozen bytes. Eligibility is mechanical: the attempt did not
  complete, raised no evaluator exception, and every native session either
  completed, never started, or failed with reason `provider` and a retained
  HTTP 500, 502, 503, 504 or 529 chat-completion error. Budget, protocol,
  citation, timeout, 4xx and changed-evidence failures are never retried.
- Every attempt keeps its directory, record, usage and latency. `usage` is the
  final attempt's ledger; `retry_usage` sums earlier attempts as a lower bound;
  `latency_seconds` is total wall time including waits. The policy is written
  to the snapshot before the first call. Default is zero retries.
- Qualification keeps the same three cases, four arms, three repetitions,
  100,000 ceiling, six turns, 120 seconds, concurrency 3 and the same three
  criteria. The only declared difference is at most three recorded retries per
  cell and twelve per job, with a 20-second wait. Cells that needed a retry are
  reported as such.
- New cases are original Elastic-2.0 development fixtures in a separate corpus
  directory. Routing activation comes only from changed paths and built-in
  rules. No label is visible to a model and no dispute is forced.
- Spend uses the stream's established conservative convention: attempts times
  the configured ceiling at the proxy-advertised output rate. Every rerun is a
  further attempt with its own ceiling, so each job also declares a job-wide
  rerun budget and its worst case is (cells + budget) times the ceiling. Limit
  for this increment: US$75. Observed-usage cost is reported beside it.

## Tasks
- [x] 1. Classify retained session failures: red tests for HTTP 5xx, 4xx,
  budget, protocol and unreadable evidence.
- [x] 2. Recorded evaluator retry: red tests for retained attempts, bounded
  count, ineligible failures, unchanged default records and snapshot policy.
- [x] 3. Arm subset selection: red tests for order, unknown names and schedule.
- [x] 4. Installed-nare proof that a scripted HTTP 500 is retried and retained.
- [x] 5. Development corpus v2 with executable gold controls: red contract and
  routing-activation tests.
- [x] 6. Freeze and run the qualification schedule; retain every outcome.
- [x] 7. If qualified, run the component subset that fits; publish the report
  with reproduction and hashes.
- [ ] 8. Independent review, both Python lanes, CI, merge, issue status blocks.

## Outcome

Tasks 1 to 5 merged in PR #67 at `2f1e95c33a959c4fe761bed999d8d149f30c9a36`, the
source frozen for every run below.

The qualification schedule passed its unchanged criteria on the first attempt of
all 36 cells; no upstream failure occurred, so no rerun was used and the rerun
path was not exercised against the live provider. Nominal $36. The Sol100k gate
had declared a stop if unqualified; this schedule ran because #51 then planned a
separate prospective run and this increment's brief allowed one schedule plus at
most two more, which supersedes that stop rule.
One of two schedules with this configuration has therefore passed.

With $39 left, the subset was chosen before any call, from label classes only:
routed versus fixed panel on two covered and two missed development-v2 cases
(24 cells), because specialist activation is certain there and had never been
measured; and the four focused arms on one dispute-prone clean case (12 cells),
the cheapest chance of a first real dispute. Captured Ruff evidence was not run:
the fixtures pass the selected rules, so that arm had nothing to add.

The first routing freeze had an operator error (the related paths were frozen as
one space-joined string). It was stopped within about 13 seconds, is retained and
published as aborted, counts $3, and is used in no result. The corrected jobs
were refrozen with zero reruns so that first attempts fit the remaining $36.
Total nominal $75.

Results are in `evaluations/reports/2026-10-13-qualification-rerun`. Every arm
found every labeled defect in every completed cell, no reviewer raised the
tempting allegation, and no discussion round ran, so the runs measure cost,
latency and failures but no quality difference. Two fixed-panel cells failed on
an out-of-range citation. Remaining for #51: human adjudication, harder
development cases that discriminate between arms and produce disputes, a
reliability estimate over more than one schedule, and unseen validation.
