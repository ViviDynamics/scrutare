# A passing qualification schedule, and first routing and dispute measurements

One prospectively frozen qualification schedule passed its gate: all 36 cells
completed with complete accounting, on the first attempt, with no rerun. It is
the second schedule with this model, ceiling and inputs; the first failed. Two
small development comparisons then ran on the qualified profile. Every arm found
every labeled defect in every cell it completed, so these runs show cost,
latency and failure differences but no quality difference between arms. No
discussion round occurred. Judgments are automated and provisional; there are
zero human judgments. Nothing here promotes a default, and
`evaluations/holdout-v1` was not opened or used.

All runs used source `2f1e95c33a959c4fe761bed999d8d149f30c9a36` (on `main`), the
proxy alias `gpt-6.1-sol` (backend revision unverified), a 100,000-token review
ceiling, three repetitions, six native turns, a 120-second session timeout,
concurrency 3 and nare 2026.10.4.

## Why the last trial failed

`residual-diagnosis.json` replays the merged failure classifier over the 180
retained cells of the five trials in `../2026-10-13-protocol`, with no new
provider call.

| Earlier trial | Cells | Not complete | Stopped only by upstream HTTP 5xx |
| --- | ---: | ---: | ---: |
| gpt-6-luna at 40,000 | 36 | 36 | 0 |
| gpt-6.1-sol at 40,000 | 36 | 33 | 0 |
| gpt-6-luna batching at 40,000 | 36 | 30 | 0 |
| gpt-6-luna batching at 80,000 | 36 | 7 | 0 |
| gpt-6.1-sol batching at 100,000 | 36 | 6 | 6 |

Each of the six Sol100k cells (five recorded invalid, one failed) holds exactly one
discovery session that ended with `HTTP 500` upstream overload; every other
session in those cells completed. Three of the six hit the three concurrent
repeats of one cell within a second. The schedule contained no schema, citation,
budget, turn or timeout failure. The residual was upstream, not a protocol or
product defect. The other trials' 106 failures were budget or protocol stops and
are not eligible for a rerun.

nare 2026.10.4 has no retry on its OpenAI rail, and the review engine seals a
review after a provider failure because that call's billing is unknown. The
rerun was therefore added to the evaluator, not the product; see
[docs/evaluation.md](../../../docs/evaluation.md#recorded-reruns-after-upstream-http-5xx).

## Qualification schedule

`qualification/` keeps the same three pilot development cases, four arms and 36
cells as the Sol100k trial, with byte-identical corpus inputs and configuration.
Its gate (`gate.json`) keeps the three criteria and adds the declared rerun
policy: at most three recorded reruns per cell and twelve per job, 20 seconds
apart, for cells stopped only by upstream 5xx.

Two things about that gate need saying plainly. First, the Sol100k gate declared
`fallback: none; stop candidate search if unqualified` and that trial did not
qualify. This schedule exists because #51's follow-up of the same day then set
"qualify reliable execution in a separately planned prospective run" as the next
step, and the brief for this increment allowed one schedule plus at most two
more after a diagnosed and fixed cause. The new gate's fallback clause records
that, and it supersedes the earlier stop rule. Second, "frozen before the first call" rests on local evidence only: the
job's `gate.json` and `preparation.json` were written about six seconds before
launch, the launcher refuses to start unless every input hash matches
`preparation.json`, and the criteria were described in the plan merged with the
source. The gate file itself was not committed before the run.

| Criterion | Result |
| --- | --- |
| 36 of 36 cells complete with complete accounting | Pass |
| Six aggregate defect-case cells with non-empty valid v2 evidence | Pass (6 of 6) |
| Six assessed defect-case cells with an actual complete assessor | Pass (6 of 6) |
| Every attempt within the 100,000 ceiling | Pass (maximum 57,203) |
| Reruns used | 0 of 12 allowed |

The pass did not depend on the rerun: no upstream failure occurred in this
window, so the rerun path was not exercised against the live provider and the
only declared change made no difference. In effect this is a second draw of the
Sol100k configuration: one of two schedules passed, the other lost six cells to
upstream overload. "Qualified profile" in this package means exactly that. It
does not establish how often the profile completes, and mechanical completion is
not semantic quality.

## What was measured

| Comparison | Cases | Detection (cells) | Tokens, candidate / baseline | Mean latency |
| --- | --- | --- | --- | --- |
| Assessment vs aggregation (#47) | 2 defect, 1 clean | 6/6 vs 6/6; clean 0/3 vs 0/3 with findings | 1.41 on defect cases, 1.00 on the clean case | 45.9 s vs 18.9 s |
| One discussion round vs assessment (#49) | same | 6/6 vs 6/6 | 1.00 | 43.1 s vs 45.9 s |
| Up to three rounds vs one (#49) | same | 6/6 vs 6/6 | 1.00 | 46.6 s vs 43.1 s |
| Routed vs fixed panel, covered paths (#50) | `dev2-01`, `dev2-06` | 6/6 vs 5/6 | 1.39 to 1.40 | 24.7 s vs 23.8 s across all four cases |
| Routed vs fixed panel, missed paths (#50) | `dev2-09`, `dev2-10` | 6/6 vs 5/6 | 1.09 to 1.10 | (as above) |
| Dispute-prone clean case, four arms (#47, #49) | `dev2-07` | 0 of 12 cells raised any finding | 1.00 | 10.4 to 11.8 s |

Detection counts a defect cell only when the cell completed with accounting and
at least one finding matched the label. With six or twelve cells per arm the 95%
Wilson intervals are wide: 6/6 is [61%, 100%], 12/12 is [76%, 100%], 10/12 is
[55%, 95%], and 0/3 clean cells with findings is [0%, 56%]. `summary.json` has
every arm, case, interval, usage counter and latency.

**Assessment.** The independent assessor ran and completed in all 18 defect
cells of the three assessed arms and supported every candidate. It changed no
outcome: aggregation alone already reported the defect in every cell and nothing
on the clean case. Its measured cost was 41% more tokens and about 2.8 times the
latency on defect cases, and nothing on clean cases where there was no candidate.

**Discussion.** Zero discussion rounds ran in 24 focused cells. In the 12
defect-case cells every candidate was supported and undisputed, so discussion
was bypassed as designed; the 12 clean-case cells had no candidate at all. On the
dispute-prone case `dev2-07`, no reviewer raised the tempting allegation in any
of 12 cells, so there was nothing to assess or dispute. One round and three
rounds are therefore indistinguishable here, and discussion quality remains
unmeasured. Nothing was forced.

**Routing.** Specialists activated from changed paths alone for the first time:
`performance-concurrency` in the three routed `dev2-01` cells and
`data-integrity-migrations` in the three routed `dev2-06` cells, as a fifth
reviewer with the same 100,000 ceiling split five ways. On the two missed-path
cases the routed roster was the fixed four. The specialist's finding duplicated
what all four generalists already reported; it added no detection and cost about
39% more tokens. That figure combines two effects: routing mode gives every
reviewer an extra focus file to read, which is the roughly 10% seen on missed
paths with the same roster, and the fifth reviewer accounts for the rest. The two-cell detection difference in favour of routing is not a
routing effect: both fixed-panel losses were whole cells failed by one reviewer's
out-of-range citation (below), in an arm with no specialist involved.

**Failures.** Two of 24 routing-job cells failed, both in the fixed-panel arm:
`dev2-06/evidence/3` and `dev2-10/evidence/2`. In each, one reviewer cited a line
range one line past the end of a file (`line_range_outside_capture`); the
unchanged validator refused the session and the cell produced no verdict. The
refused findings named the labeled defect, and in each cell the other three
reviewers had returned valid findings naming it too; a failed cell reports none
of them, which is why 94 findings are retained and not 102. These were not
upstream failures and were not rerun. All 48 cells of the other two jobs
completed. The comparison jobs had no pass or fail gate; the `not_qualified`
value in `routing/mechanical-qualification.json` only restates that not every
cell completed.

## What this does not establish

- No quality difference between any two arms. Every case was easy enough for this
  model that all arms were at the ceiling, so these cases cannot discriminate.
- Nothing about discussion quality, or about one round versus several.
- Nothing about whether a specialist helps on a defect generalists miss; no such
  defect occurred. The routing subset contained no clean case, so specialist
  false positives were not measured.
- Nothing about captured Ruff evidence (#48): not run. The fixtures pass the
  selected Ruff rules, so the arm would have had no diagnostics to add.
- No reliability rate. One 36-cell pass, and 2 of 24 cells lost to citation
  validation on new cases, is a small sample.
- No unseen validation and no human adjudication.

## Judgments

`automated-decisions.json` matches 190 of 190 findings to the one labeled defect of
their case, using the rules in `reproduce.py`: the finding must be anchored on
the labeled changed line of the labeled file, new side, and its problem
statement must pass a keyword filter. The filters were written after the outputs
existed and are not a semantic judge; a differently worded wrong finding on the
same line could pass them. What supports the 190 matches is that the authoring
agent and, separately, an independent reviewer agent each read all 71 distinct
problem statements against the labels and found every one to state the labeled
defect. Both readers are models. A finding no rule matches would be left pending
for a person, never scored false; none was. No finding was reported on a clean
case. Because nothing was judged false, no precision figure is published. These
are provisional decisions against author-defined labels, and 144 of the 190 are
repeated reports of a defect already matched in the same cell.

## Spend

The limit for this increment was US$75, counted the conservative way used across
this stream: attempts times the configured ceiling at the proxy-advertised output
rate of $10 per million tokens.

| Job | Attempts | Nominal |
| --- | ---: | ---: |
| Qualification | 36 | $36 |
| Routing, first freeze (aborted, see below) | 3 started | $3 |
| Routing | 24 | $24 |
| Dispute-prone case | 12 | $12 |
| Total | | $75 |

Measured usage was 3,274,806 tokens across the three completed jobs. Priced
entirely at the advertised input and output rates, with cached input charged as
uncached, that is about $8.45. It excludes whatever the aborted job's in-flight
calls cost. No invoice or billed amount is asserted.

The first routing freeze (`aborted-routing/`) carried an operator error: its
related-path list was one space-joined string, so reviewers would not have
received unchanged supporting files. The operator stopped it within about 13
seconds of launch (its last artifact was written about six seconds after launch)
with three cells and twelve sessions interrupted, no completed cell and zero
observed tokens; whether the in-flight calls were billed is unknown. It is retained, counted in the spend, and
used in no result. The corrected jobs were refrozen with zero reruns so first
attempts fit the remaining allowance.

Not run for lack of allowance, at the same convention: the full four-arm
focused comparison on all twelve development-v2 cases ($144), routed versus
fixed on the remaining eight ($48), and assessment versus tools on twelve ($72).

## Reproduce

```sh
python reproduce.py --package . --output /tmp/qualification-rerun-summary.json
```

Python 3.10 or newer, standard library only, no network. It checks every
published hash, the exact cell matrices, agreement between public experiment
rows and normalized cells, recomputes each gate, the automated decisions and
`summary.json`, and fails on any difference. It does not rerun a model or
authenticate normalized evidence against native sessions; that needs the
retained private captures. In particular, whether an assessor actually ran and
whether evidence validated are read from `mechanical-qualification.json`, which
was produced from private captures with the measured wheel, and the residual
diagnosis is checked only for its totals.

Each completed job directory holds `preparation.json` (source, inputs and their hashes,
frozen before the first call), `gate.json`, `evaluation.json`, `snapshot.json`
(actual reviewers, prompts, allocations and rerun policy), `experiment.json`,
`mechanical-qualification.json` and `corpus-manifest.json`; `aborted-routing/`
has no mechanical file. Run directories are
relative identities and the runtime path is `nare@2026.10.4`.
`original-artifact-sha256.json` records hashes of the private originals.
Raw native transcripts, credentials and provider headers are not published.
Case sources and labels are in `evaluations/pilot` and
`evaluations/development-v2`.
