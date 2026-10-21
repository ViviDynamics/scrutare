# Re-qualification of `spark/glm-5.3-flash` with schema-constrained output

**Result: not qualified.** In the one frozen schedule (`s1`), 13 of 36 cells
completed with complete accounting. Two of the three gate items that depend on the
defect cases failed. The constraint did what #78 asked of it: no session failed
the findings schema (9 did in `g2`), and no constrained correction failed
validation. The binding failure moved to token budgets. All 8 assessor sessions
overran the default 20,000-token assessor reservation, so no assessed defect cell
completed. External spend was US$0. Judgments are automated and provisional, with
zero human judgments. Nothing here changes a default, the model is not promoted,
and `evaluations/holdout-v1` was not opened or used.

The schedule is `g2`'s ([PR #77](../2026-10-14-glm-qualification/README.md)) with
three changes, recorded before launch in `freeze-record.json`:

- `models.default.structured_output: json_schema` (#78). This is the only
  difference in `evaluation.json`.
- nare 2026.10.4 to 2026.10.12 (`96672e1`, contract 2), the release that carries
  `--structured-output`.
- The released source: Scrutare 2026.10.26 at `b0dfd0f` (#79).

Everything else is unchanged from `g2`: cases, arms, three repeats, the 100,000
ceiling, the default assessor reservation, six turns, the 600 s timeout,
concurrency 3, the retry policy, and every gate criterion and the pass rule.

## Gate

| Criterion | `s1` (constraint on) | `g2` (off) | `gpt-6.1-sol` r1 / r2 / r3 |
| --- | --- | --- | --- |
| 36 cells complete and accounted | **13 of 36** (fail) | 14 of 36 | 36 / 36 / 36 |
| ... on the first attempt (a rerun counts as a failure here) | 13 of 36 | 14 of 36 | 36 / 36 / 36 |
| Six aggregate defect cells with non-empty valid v2 evidence | **4 of 6** (fail) | 2 of 6 | 6 / 6 / 6 |
| Six assessed defect cells whose assessor actually ran and completed | **0 of 6** (fail) | 0 of 6 | 6 / 6 / 6 |
| Every attempt within 100,000 tokens | pass (max 90,346) | pass | pass |
| Upstream 5xx reruns used | 0 of 6 allowed | 0 | 0 / 0 / 0 |
| Clean-case cells complete | 9 of 12 | 12 of 12 | 12 / 12 / 12 |

Cell statuses: `s1` 13 complete, 14 failed, 9 invalid. `g2` 14 complete, 17
failed, 5 invalid. `reproduce.py` recomputes every column from this package and
the published `g2` and Sol packages. None needed a rerun. The columns are a
side-by-side, not a paired comparison: `g2` ran a week earlier, with more
concurrent load, on nare 2026.10.4 and source `5c4a8b6`. Sol is another model, on
`2f1e95c`, with a 120 s timeout.

## Why `s1` failed

`s1` ran 144 discovery sessions and 8 assessor sessions. No discussion round
started. 124 sessions completed. The other 28, by cause, from each session's
result record and nare's result event (`s1/session-diagnosis.json`;
`g2-session-diagnosis.json` is built the same way from `g2`'s retained records):

| Cause | `s1` | `g2` |
| --- | ---: | ---: |
| Assessor over its 20,000 reservation | 8 (of 8 assessors) | 2 (of 3) |
| Discovery session over its 20,000 grant | 10 | 3 |
| Still generating at 600 s | 7 | 23 |
| Final answer failed the findings schema | **0** | 9 |
| Citation past a file's end | 2 | 1 |
| nare: "model emitted reasoning but no answer" | 1 | 0 |

- **Assessor budget** is now decisive. Every `s1` assessor used 25,200 to 33,468
  tokens against 20,000 (`assessor_tokens_vs_allocation` in `summary.json`). In
  the one session inspected, input alone was 24,895 tokens. These overruns are
  why all 8 cells ended `assessment_incomplete`. They include 5 of the 6 assessed
  defect cells; the sixth (`security-auth-order/assessed/1`) failed earlier, in
  discovery, on a budget overrun. None of the 8 assessor sessions used a
  constrained turn.
- **Discovery budget:** all 10 discovery overruns finished their answer (nare
  `done`) and went over the 20,000 grant by 339 to 2,885 tokens. Five of them had
  a constrained correction turn, which costs a turn of context. The other five
  did not.
- **Timeouts** fell from 23 to 7, and none was in a constrained turn. Concurrent
  load was also much lower (below), so this package does not credit the drop to
  the constraint.
- **The nare error** was a clean-case session whose constrained correction came
  back as reasoning with no answer content: 373 output tokens, all reasoning.
  This is the one correction in 31 that produced nothing usable. A plausible
  reading is that the serving stack's reasoning parser and `json_schema` decoding
  interact, so the model never left its reasoning block. This package does not
  establish that.

Three aggregate cells failed. Two defect cells each lost one session to a
timeout (`security-auth-order/aggregate/3`, `tests-consumer-fixture/aggregate/2`),
and one clean cell lost one to the nare error. In `g2`, four aggregate defect cells
failed: two on schema failures and two on timeouts.

## Did the constraint fire?

Every one of the 152 sessions requested it, and every `invocation.json` carries
`--structured-output json_schema`. nare recorded 31 constrained turns, one in each
of 31 sessions. Under the accepted reading of #78, nare sends the constraint only
on its tool-free schema correction turn, after a first answer fails its schema
check. The count in each `result.json` equals the constrained-turn events in that
session's stream. The 31 ended as follows: 24 complete, 5 over the discovery grant, 1 citation
past a file's end, and 1 nare reasoning-without-answer. **None failed schema
validation.** The other 121 sessions gave a valid first answer and were accepted
unconstrained, as the accepted reading of #78 intends.

## Detection

The four complete aggregate defect cells produced 16 findings. The frozen keyword
rules (copied unchanged from the 2026-10-13 package, so prospective here) match
all 16 to the labeled defect, so none is left pending. No complete clean cell
reported a finding. Too few defect cells completed in the other arms to say
anything about them.

## Cost and speed

| | `s1` | `g2` | Sol r1 | Sol r2 | Sol r3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Total tokens (recorded) | 2,194,057 | 1,933,565 | 1,643,358 | 1,641,825 | 1,642,692 |
| Output tokens | 489,217 | 393,364 | 137,295 | 136,920 | 137,253 |
| Mean cell latency | 485 s | 504 s | 38.6 s | 34.5 s | 34.7 s |
| Median cell latency | 482 s | 596 s | 40.9 s | 36.6 s | 36.6 s |
| Mean tokens, aggregate arm | 56,336 | 55,522 | 37,630 | 37,629 | 37,627 |

The proxy logged 501 requests from this evaluator in the `s1` window, totalling
2,273,470 tokens (1,735,438 prompt, 538,032 completion), against 2,194,057
recorded. Ten cells have incomplete accounting. Completed discovery sessions
used a median 12,371 tokens, of which 2,704 were output. Wall clock: 03:55:25Z to
05:33:21Z on 2026-10-21, 1 h 38 min for 36 cells.

## Spend, fallbacks and concurrent load

The live router was read at preparation and again at 03:55Z, immediately before
launch. Both reads showed no `fallbacks`, `context_window_fallbacks` or
`content_policy_fallbacks`, no wildcard groups, and one deployment for
`spark/glm-5.3-flash` (`openai/glm-5.3-flash` on the Spark LAN host).

From 03:55 to 05:34Z, all 516 spend-log rows on any key were served by that
deployment: 0 paid-provider rows, 0 attempted fallbacks, 0 retries. LiteLLM
attributes a nominal US$0.69 at its configured rate, which no provider bills.
Of the 516 rows, 501 are this evaluator and 15 are opencode (04:01 to 05:31Z;
1.06 million prompt and 6,362 completion tokens). That is far below `g2`'s
window, which had 113 opencode requests (about 7.0 million prompt tokens) and 95
from Claude Code. `provider-evidence.json` also records the lighter load before
launch. Backend identity and limits are as in the `g2` package. The model
revision is not verified.

## Further schedule

The frozen gate permits ("may run") one further schedule, `s2`, for a
diagnosable failure that a documented, prospectively frozen profile change
addresses. The assessor and discovery overruns are of that kind:
`--assessment-tokens` and the #76 phase allowances with the
last-complete-stage overrun fallback. The timeouts are not. At `s1`'s rate (7 of
152 sessions) the chance that all of a schedule's roughly 150 sessions avoid a
timeout is about 0.001, and one timed-out session voids its cell. So the
36-cell criterion stays practically unreachable unless timeouts also disappear,
on a longer timeout (the gateway's own is 600 s) or an unshared engine.
`s2` was **not run** in this package. Whether to run it is a maintainer decision.

## Reproduce

```sh
python reproduce.py --output glm-structured-summary.json
```

Python 3.10 or newer, standard library only, no network. It fails on any
difference, and it:
- checks every published hash here and in the sibling packages it compares
  against (`g2` and the three Sol schedules), and refuses files outside the
  manifest;
- requires `s1`'s `preparation.json`, `gate.json` and `evaluation.json` to match
  the hashes posted on #78, the freeze record, the private-original hashes and the
  preparation's own input hashes, and requires the constraint to be frozen;
- requires the mechanical summary to name that gate and experiment;
- checks both session diagnoses, session by session, against the mechanical cells
  (persona, status, reason, tokens, and assessor reason and tokens);
- recomputes the gate, the cause tables, the constraint counts, the automated
  decisions and the side-by-side.

It does not recompute figures that come from private captures or proxy reads:
nare's error text and constrained-turn counts (it counts them, but cannot
authenticate them), session medians, the proxy-logged tokens, spend-log counts
and the per-client split. Those need the retained captures, whose hashes
`original-artifact-sha256.json` and each diagnosis's `private_records_sha256`
record. Raw transcripts, credentials and raw provider responses are not
published. `s1/preparation.json` is published byte-identical to the hash posted
on #78, so it keeps one local filesystem path, the operator's nare console.

## What this does not establish

- Whether a larger assessor reservation, with the overrun fallback, would let the
  assessed defect cells complete. `s2` was not run.
- Whether the timeout drop comes from the constraint or from the lighter load.
- Why one constrained correction came back as reasoning only.
- Anything about review quality beyond four aggregate defect cells.
