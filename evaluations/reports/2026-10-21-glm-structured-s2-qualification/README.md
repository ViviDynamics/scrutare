# Final schedule `s2` of `spark/glm-5.3-flash` with schema-constrained output

**Result: not qualified.** `s2` was the one further schedule the frozen `s1` gate
allowed, and the last. 34 of 36 cells completed with complete accounting. All 6
aggregate defect cells produced valid evidence (this item passed), but only 5 of
the 6 assessed defect cells completed their assessor. Two sessions out of 161
decided it: one assessor that ran into the 1800 s timeout, and one discovery
citation past a file's end. External spend was US$0. Judgments are automated and
provisional, with zero human judgments. Nothing here changes a default, the model
is not promoted, and `evaluations/holdout-v1` was not opened or used.

`s2` is `s1` ([PR #80](../2026-10-21-glm-structured-qualification/README.md)) with
changes sized from `s1`'s data and recorded before launch in
`s2/deviations.json`. Its hashes were posted on #51, and so was the amendment:

| Setting | `s1` | `s2` | Why |
| --- | --- | --- | --- |
| Total ceiling, and the gate's per-attempt bound | 100,000 | 200,000 | the larger grants below cannot fit 100,000 |
| Assessor reservation | 20,000 (default fifth) | 50,000 | all 8 `s1` assessors overran, ending at 25,200 to 33,468 |
| Discovery grant, assessed arms (derived) | 20,000 | 37,500 | `s1` maximum 22,885; aggregate arm 25,000 -> 50,000 |
| Session timeout | 600 s | 1800 s | all 7 `s1` timeouts were still generating |
| Overrun fallback | off | off | a fallback degrades verdicts |
| Launch condition | — | idle window **waived** | see below |

Everything else is identical to `s1`: release 2026.10.26 at `b0dfd0f` (the same
wheel), `structured_output: json_schema`, nare 2026.10.12, corpus, cases, arms,
three repeats, six turns, concurrency 3, the retry policy, and every other gate
criterion. Before freezing, the upstream timeouts were read, and none cuts a
stream sooner than 1800 s:
- the LiteLLM router timeout and `request_timeout` are 3600 s, and so are the
  LAN WAF proxy's read and write timeouts (infrastructure `c752f20`);
- the vLLM serving role sets no request timeout;
- nare's 600 s httpx timeout applies per read on a stream.

The path was LAN only, not Cloudflare.

**The launch rule was waived.** `s2` was to launch only after 30 minutes with no
other Spark traffic. The waiter polled the spend log 26 times, 11:53Z to 13:58Z,
and every poll saw opencode (and sometimes an unidentified `python-httpx` client).
The maintainer then ruled to launch without waiting. That amendment was dated,
recorded and posted on #51 before launch. It changed no frozen job file.

## Gate

| Criterion | `s2` | `s1` | `g2` | `gpt-6.1-sol` r1 / r2 / r3 |
| --- | --- | --- | --- | --- |
| 36 cells complete and accounted | **34 of 36** (fail) | 13 | 14 | 36 / 36 / 36 |
| ... on the first attempt (no rerun) | 34 of 36 | 13 | 14 | 36 / 36 / 36 |
| Six aggregate defect cells with non-empty valid v2 evidence | **6 of 6** (pass) | 4 | 2 | 6 / 6 / 6 |
| Six assessed defect cells whose assessor ran and completed | **5 of 6** (fail) | 0 | 0 | 6 / 6 / 6 |
| Every attempt within the ceiling | pass (max 106,800 of 200,000) | pass | pass | pass |
| Upstream 5xx reruns used | 0 of 6 allowed | 0 | 0 | 0 / 0 / 0 |
| Clean-case cells complete | 12 of 12 | 9 | 12 | 12 / 12 / 12 |

`reproduce.py` recomputes every column from this package and the published `s1`,
`g2` and Sol packages. The columns are a side-by-side, not a paired comparison.
The largest attempt (106,800 tokens) would not have fit `s1`'s ceiling.

## The two failed cells

- **`security-auth-order/assessed/3`, assessor timeout.** After one `read` at
  14:42:36Z, the assessor's second request ran until the 1800 s timeout killed the
  session at 15:12:32Z. LiteLLM logged that request as generating 45,217 completion
  tokens before the client disconnected (HTTP 499). None of them reached nare's
  event stream, which holds nothing after the tool call: no reasoning, text or
  answer. This package does not establish why. A runaway generation that the
  serving stack buffered (for example inside a tool call) would fit. The request
  alone would also have passed the 50,000 reservation. This is the cell that fails
  the assessed criterion.
- **`security-auth-order/focused3/2`, citation past a file's end.** One discovery
  session (`senior-dev`, after a constrained correction) cited beyond the end of a
  file. The cell is not a gate-item cell, but it breaks 36 of 36.

## Sessions by cause

From `s2/session-diagnosis.json`; `s1` and `g2` come from the `s1` package:

| | `s2` | `s1` | `g2` |
| --- | ---: | ---: | ---: |
| Sessions | 161 | 152 | 147 |
| Completed | 159 | 124 | 109 |
| Assessor over its reservation | 0 (of 17) | 8 (of 8) | 2 (of 3) |
| Discovery over its grant | 0 | 10 | 3 |
| Session timeout | 1 (1800 s) | 7 (600 s) | 23 (600 s) |
| Findings schema failure | 0 | 0 | 9 |
| Citation past a file's end | 1 | 2 | 1 |
| nare: reasoning but no answer | 0 | 1 | 0 |

- **Budgets held.** The 16 completed assessors used 21,145 to 45,017 tokens
  against 50,000. Discovery reached at most 33,110 against 37,500 (50,000 in the
  aggregate arm).
- **The longer timeout mattered.** Completed sessions took a median 318 s, a p90
  of 540 s and at most 1,123 s. 13 of them ran past 600 s and would have timed out
  under `s1`'s setting. None passed 1200 s.
- **The constraint fired** in 42 sessions, one constrained correction each: 41
  completed and 1 was the citation refusal above. No session failed the findings
  schema. Each recorded count equals the constrained-turn events in its stream.

## Detection

The 34 complete cells produced 88 findings. No complete clean cell reported one.
The frozen keyword rules (copied unchanged from the 2026-10-13 package, so
prospective here) match 84 to the labeled defect. The 4 they do not match each
state the labeled defect, but word it differently (for example "moved ahead of
the authorization check") or anchor it one line above the labeled line. They are
left pending for a person, as the rules require. By arm, using the automated
decisions, defect cells detected: aggregate 6/6, assessed 5/6, focused1 6/6,
focused3 5/6. No discussion round started.

## Cost, speed and load

| | `s2` | `s1` | `g2` | Sol r1 | Sol r2 | Sol r3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Total tokens (recorded) | 2,522,568 | 2,194,057 | 1,933,565 | 1,643,358 | 1,641,825 | 1,642,692 |
| Output tokens | 561,745 | 489,217 | 393,364 | 137,295 | 136,920 | 137,253 |
| Mean cell latency | 733 s | 485 s | 504 s | 38.6 s | 34.5 s | 34.7 s |
| Median cell latency | 731 s | 482 s | 596 s | 40.9 s | 36.6 s | 36.6 s |

The proxy logged 541 requests from this evaluator in the `s2` window (2,672,212
tokens). Wall clock: 13:59:42Z to 16:35:09Z, 2 h 35 min for 36 cells.

Concurrent load was the heaviest of any schedule here. In the run window, opencode
sent 205 requests (17.7 million prompt tokens, 136,240 completion tokens), and the
unidentified `python-httpx` client sent 12 (1.86 million prompt tokens). Both were
served by the same Spark engine. In the hour before launch, opencode alone sent 90
requests (7.5 million prompt tokens). The `s2` result was obtained under that
load, not on an idle engine.

## Spend and fallbacks

The router was read at 13:59:42Z, immediately before launch: no `fallbacks`,
`context_window_fallbacks` or `content_policy_fallbacks`, no wildcard groups, and
one deployment for `spark/glm-5.3-flash`. From 13:59 to 16:37Z, all 758 spend-log
rows were served by `openai/glm-5.3-flash` on the Spark host, with 0 attempted
fallbacks and 0 retries. The spend script's count of "12 paid-provider rows" is a
string comparison: those rows give the same Spark base URL with a trailing slash
(the `python-httpx` client). No paid provider was called. LiteLLM's nominal
attribution, which no provider bills, is in `provider-evidence.json`.

## Outcome

`spark/glm-5.3-flash` is **not qualified** on this profile. `s2` was the last
schedule the gate allows, so no further schedule may run under it. With the
constraint, larger grants and a longer timeout, the model came within one assessor
session and one citation of the 36-cell criterion. Five of the six assessed defect
cells completed. A new qualification would need a new, prospectively frozen gate.

## Reproduce

```sh
python reproduce.py --output glm-structured-s2-summary.json
```

Python 3.10 or newer, standard library only, no network. It fails on any
difference, and it:
- checks every published hash here and in the `s1`, `g2` and Sol packages, and
  refuses files outside the manifest;
- requires `s2`'s `preparation.json`, `gate.json`, `evaluation.json` and the
  amended `deviations.json` to match the hashes posted on #51 and the freeze
  record;
- requires the gate's changed settings (ceiling, timeout, assessor reservation) to
  equal the preparation's, and the constraint to be frozen;
- checks every session diagnosis against its mechanical cells and frozen rail;
- recomputes the gate, the cause tables, the constraint counts, the medians, the
  automated decisions and the side-by-side.

Not recomputed are figures that come from private captures or proxy reads: nare's
error text, the constrained-turn counts and each session's input and output split
(counted, but not authenticated), session durations, the stalled request's proxy
row, the proxy-logged tokens, spend-log counts and the per-client load. The
retained captures' hashes are in `original-artifact-sha256.json` and each
diagnosis's `private_records_sha256`. `s2/preparation.json` is published
byte-identical to its posted hash, so it keeps one local path, the operator's
nare console.

## What this does not establish

- Why the assessor's stalled request produced no stream events.
- Behavior on an idle engine. `s2` ran under the heaviest load of these schedules.
- Review quality beyond the automated keyword decisions. Human adjudication is
  still outstanding.
