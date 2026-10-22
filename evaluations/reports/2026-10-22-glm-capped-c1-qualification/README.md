# Schedule `c1` of `spark/glm-5.3-flash` with a per-request output cap

**Result: not qualified.** `c1` is the first schedule of the gate frozen for #82. It is `s2`'s profile ([PR #81](../2026-10-21-glm-structured-s2-qualification/README.md)) plus `max_output_tokens: 20000` (PR #83, release 2026.10.29).

- **Cells:** 31 of 36 completed with complete accounting. `s2` completed 34.
- **Assessed defect cells:** all 6 completed their assessor (this item passed; `s2` had 5).
- **Aggregate defect cells:** only 4 of 6 produced valid evidence (this item failed; `s2` had 6).
- **Five sessions out of 161 decided it.** Each was in a different cell, and they had four different causes:
  - two discovery citations past a file's end;
  - one discovery answer with a placeholder citation content hash, which Scrutare refused;
  - one stream that LiteLLM aborted for repetition;
  - one assessor that overran its budget on a request cut off at the cap.
- **No session timed out.**

External spend was US$0. Judgments are automated and provisional, with zero human judgments. Nothing here changes a default, and the model is not promoted. `evaluations/holdout-v1` was not opened or used.

## What changed from `s2`

The gate (`c1/gate.json`) was frozen before any call. Its hashes and the cap sizing were posted on #51 ([comment](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6331986053)). Every gate criterion and the pass rule are byte-identical strings to `s2`'s gate.

| Setting | `s2` | `c1` | Why |
| --- | --- | --- | --- |
| `max_output_tokens` | none | 20,000 | `s2`'s runaway assessor request: 45,217 tokens until the 1800 s timeout |
| Source | 2026.10.26 (`b0dfd0f`) | 2026.10.29 (`9b77c9b`) | carries the cap; the only source change since `b0dfd0f` is #83 |
| Launch condition | waived | binding, then **waived** after 6 h | see below |

Everything else is identical to `s2`:
- `structured_output: json_schema`, nare 2026.10.12 (`96672e1`);
- the corpus (byte-identical input hashes), cases, arms, 3 repeats and 36 cells;
- 6 turns, concurrency 3 and the retry policy;
- the 200,000 ceiling, the 50,000 assessor reservation, the 1800 s timeout, and no overrun fallback;
- the LAN path.

The gate also states the following up front:
- **Citation past a file's end:** treated as in `s2`. The session fails citation validation, and its cell counts against 36 of 36.
- **Schedules allowed:** one, plus at most one re-test (`c2`). `c2` may run only on a maintainer decision, under a gate posted before it launches.

**Cap sizing** (`c1/cap-sizing.json`). In `s2`'s retained session streams there are 536 model requests, one per nare cost event. `s2` logged 537 spend-log rows for this evaluator: the same 536, plus the runaway request, which produced no cost event because the timeout killed its session.
- The largest request in any completed session was 12,387 completion tokens (an assessor). p99 was 5,882 and p50 was 325. 20,000 is 1.61x that maximum.
- The runaway request decoded at 25.2 tok/s. At that rate a repeat would reach the cap after about 794 s, inside the 1800 s timeout.

**The launch rule was waived again.** The waiter (`scrutare-glm-c1-wait.service`) polled the spend log 72 times, from 03:30:58Z to its 6 h deadline at 09:31:16Z. It never launched: every poll saw 4 to 46 Spark rows in the previous 30 minutes, mostly from opencode and an unidentified `python-httpx` client.
- The maintainer then waived the rule, in a ruling dated 2026-10-22. It was recorded in `c1/amendment.json` and posted on #51 before launch ([comment](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6340502541)).
- The amendment is a separate file, so every frozen file keeps its posted hash.
- One correction to its evidence note: this operator's pre-freeze cap smoke (`python-httpx2`, 03:25 to 03:28Z) appears in the first five polls, not only the first. The amendment's hash is posted, so the file is left as it is.
- `c1` launched at 12:16:16Z as `scrutare-glm-c1.service`.

## Gate

| Criterion | `c1` | `s2` | `s1` | `g2` | `gpt-6.1-sol` r1 / r2 / r3 |
| --- | --- | --- | --- | --- | --- |
| 36 cells complete and accounted | **31 of 36** (fail) | 34 | 13 | 14 | 36 / 36 / 36 |
| ... on the first attempt (no rerun) | 31 of 36 | 34 | 13 | 14 | 36 / 36 / 36 |
| Six aggregate defect cells with non-empty valid v2 evidence | **4 of 6** (fail) | 6 | 4 | 2 | 6 / 6 / 6 |
| Six assessed defect cells whose assessor ran and completed | **6 of 6** (pass) | 5 | 0 | 0 | 6 / 6 / 6 |
| Every attempt within the ceiling | pass (max 104,505 of 200,000) | pass | pass | pass | pass |
| Upstream 5xx reruns used | 0 of 6 allowed | 0 | 0 | 0 | 0 / 0 / 0 |
| Clean-case cells complete | 12 of 12 | 12 | 9 | 12 | 12 / 12 / 12 |

`reproduce.py` recomputes every column from this package and the published `s2`, `s1`, `g2` and Sol packages. The columns are a side-by-side, not a paired comparison.

## The five failed cells

| Cell | Session | Cause |
| --- | --- | --- |
| `security-auth-order/aggregate/3` | discovery `junior-dev` | **Citation past a file's end** (by 1 line). This is an aggregate defect cell, so it fails that item. |
| `tests-consumer-fixture/aggregate/3` | discovery `devops` | **Stream aborted for repetition.** LiteLLM ended the request mid-stream with HTTP 500, "The model is repeating the same chunk", after 2,328 completion tokens. nare reported `stream provider error` with code 500. The gate's wording makes a retained provider 5xx eligible for a rerun, but Scrutare's transient classifier matches only nare's `chat completion failed with HTTP 5xx` form, so no rerun was offered (#86). A rerun could not have changed the outcome: the gate also requires every cell to complete on its first attempt. This is an aggregate defect cell, so it fails that item. |
| `security-auth-order/focused3/3` | discovery `senior-dev` | **Answer evidence refused.** nare accepted the JSON shape, and the citation's revision was the correct head revision. But its content hash was a placeholder string, not a SHA-256, so Scrutare's evidence parser refused it and the session failed as `protocol`. No constrained correction was involved. |
| `tests-consumer-fixture/focused3/1` | discovery `devops` | **Citation past a file's end** (by 2 lines). |
| `tests-consumer-fixture/focused1/2` | assessor | **Assessor overrun, on a request cut off at the cap.** See below. |

All five cells break 36 of 36, and the two aggregate cells also fail the aggregate item. Four of the five are accounting-incomplete, which is the usual record for a failed discovery session. The exception is the assessor cell, whose accounting is complete.

## The cap

All 161 sessions recorded the cap (`{"limit": 20000, ...}`), and every argv carried `--max-tokens 20000`.
- The 532 requests in the session streams ranged up to 20,000 completion tokens.
- Exactly one request reached the cap. The next largest was 10,967.
- The proxy logged that request as 7,442 prompt and 20,000 completion tokens over 986 s. That is the cap enforced by vLLM.

The request at the cap was the assessor's fifth turn on `tests-consumer-fixture/focused1/2`. In `s2`, the comparable request ran to 45,217 tokens until the session timeout killed it. Here it stopped at 20,000.
- nare reported "the model's turn was cut off at max_tokens".
- nare's after-turn budget check then also fired, because the session had used 56,946 of its 50,000 tokens. It replaced the terminal stop reason with `budget`.
- Scrutare reads the terminal record, so it recorded the session as a budget overrun (`partial`, `budget`, `reached: false`) instead of `output_cap`.
- What did hold: no answer was used, accounting was complete, and the cell failed. But the failure is attributed to the budget, not the cap, as #82 intended. This gap in #83 is filed as **#84**.

## Sessions by cause

From `c1/session-diagnosis.json`. The other columns come from the `s2` and `s1` packages.

| | `c1` | `s2` | `s1` | `g2` |
| --- | ---: | ---: | ---: | ---: |
| Sessions | 161 | 161 | 152 | 147 |
| Completed | 156 | 159 | 124 | 109 |
| Session timeout | **0** | 1 (1800 s) | 7 (600 s) | 23 (600 s) |
| Assessor over its reservation | 1 (of 17, at the cap) | 0 | 8 | 2 |
| Discovery over its grant | 0 | 0 | 10 | 3 |
| Findings schema failure | 0 | 0 | 0 | 9 |
| Citation past a file's end | 2 | 1 | 2 | 1 |
| Answer evidence refused (placeholder content hash) | 1 | 0 | 0 | 0 |
| Stream aborted for repetition (LiteLLM) | 1 | 0 | 0 | 0 |
| nare: reasoning but no answer | 0 | 0 | 1 | 0 |

- **Budgets held,** except for the one assessor above.
  - The 16 completed assessors used 20,989 to 43,139 tokens against 50,000.
  - The largest attempt used 104,505 tokens against 200,000.
- **Sessions finished sooner than in `s2`.** Measured from the first to the last stream event, completed sessions took a median of 290 s, a nearest-rank p90 of 530 s, and at most 1,034 s. 9 ran past 600 s and none passed 1200 s.
- **The constraint fired** in 38 sessions, one constrained correction each: 37 completed and 1 cited past a file's end. No session failed the findings schema.

## Detection

The 31 complete cells produced 78 findings. The frozen keyword rules (unchanged since the 2026-10-13 package) match 74 of them to the labeled defect, and the other 4 are left pending for a person.

By arm, using the automated decisions, the defect cells detected were:
- aggregate: 4 of 6;
- assessed: 6 of 6;
- focused1: 5 of 6;
- focused3: 4 of 6.

**One complete clean cell reported a finding**, which `s2` had none of: `contract-explicit-default-clean/assessed/3`, a `docs` finding about a docstring on `change.py`. It is not a gate item. No discussion round started.

## Cost, speed and load

| | `c1` | `s2` | `s1` | `g2` | Sol r1 | Sol r2 | Sol r3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Total tokens (recorded) | 2,499,181 | 2,522,568 | 2,194,057 | 1,933,565 | 1,643,358 | 1,641,825 | 1,642,692 |
| Output tokens | 574,191 | 561,745 | 489,217 | 393,364 | 137,295 | 136,920 | 137,253 |
| Mean cell latency | 659 s | 733 s | 485 s | 504 s | 38.6 s | 34.5 s | 34.7 s |
| Median cell latency | 598 s | 731 s | 482 s | 596 s | 40.9 s | 36.6 s | 36.6 s |

The run took 2 h 14 min of wall clock for 36 cells, from 12:16:16Z to 14:30:20Z.

The proxy logged 533 requests from this evaluator (nare on the proxy master key) in the window:
- the 532 turns in the session streams, whose 1,924,990 input and 574,191 output tokens equal recorded cell usage;
- plus the aborted request, whose 3,824 prompt and 2,328 completion tokens nare never received as usage.

**Concurrent load** was recorded before launch, every 15 minutes during the run (by user agent, key alias and base URL), and in a full re-read of the window after exit (`provider-evidence.json`).
- **The hour before launch:** 45 other Spark requests, 36 from `python-httpx` and 9 from opencode.
- **During the run** (604 rows, all on the proxy master key):
  - this evaluator: 533 requests;
  - the unidentified `python-httpx` client: 45 requests (3.64 million prompt and 121,580 completion tokens);
  - opencode: 26 requests (2.61 million prompt tokens).
- Every 15-minute sample during the run saw other clients.
- The samples are a lower bound. Each was read when its window closed, so requests still in flight were missed: they count 474 evaluator rows against the re-read's 533. The first sample (12:02 to 12:17Z) is mostly from before launch. The full re-read after exit is the authoritative count.
- The load was lighter than in `s2`'s window, where opencode alone sent 209 requests, but `c1` still did not run on an idle engine.

## Spend and fallbacks

**Router.** It was read immediately before launch: no `fallbacks`, `context_window_fallbacks` or `content_policy_fallbacks`, no wildcard groups, and one deployment for `spark/glm-5.3-flash` (`openai/glm-5.3-flash` on the Spark host).

**Run window.** All 604 spend-log rows that started from 12:16 to 14:32Z, on every key, were served by that deployment, with 0 attempted fallbacks and 0 retries. One row is a failure: the aborted request above.

No paid provider was called. One caveat about the spend script: its "paid provider rows" count (45) is a string comparison against the base URL without a trailing slash. Those 45 rows are the `python-httpx` client's, which use the same Spark base URL with a trailing slash. `provider-evidence.json` normalizes the slash and reports the count separately (`base_url_with_trailing_slash_rows`). LiteLLM's nominal attribution for the self-hosted group is not billed by any provider.

**Live smoke of the cap** (released wheel 2026.10.29, 03:25 to 03:28Z, before the freeze). The smoke used two personas:
- one on the default rail with the cap at 20,000, which completed;
- one with an override of 64, whose second request ended at exactly 64 completion tokens. That stopped the session as `output_cap`, with complete accounting.

The spend log stores no request parameters. That 64-token row is the evidence that the cap reached vLLM through LiteLLM. All of the smoke's rows were served by the Spark deployment, with 0 fallbacks.

## Outcome

`spark/glm-5.3-flash` is **not qualified** on this profile.
- The cap did what it was sized for. No session timed out, and the one runaway-length request stopped at 20,000 tokens instead of 45,217.
- The assessed item passed for the first time.
- The remaining failures are mostly about evidence quality: three of the five are citations the model got wrong. Of the other two:
  - one is a LiteLLM repetition abort;
  - one is an assessor whose cut-off request also exhausted its budget.

The gate allows one re-test (`c2`), only on a maintainer decision and under a gate frozen and posted on #51 first. None has been run.

## Reproduce

```sh
python reproduce.py --output glm-capped-c1-summary.json
```

It needs Python 3.10 or newer and uses only the standard library, with no network access. It fails on any difference. It:
- checks every published hash here and in the `s2`, `s1`, `g2` and Sol packages, and refuses files outside the manifest;
- requires `c1`'s `preparation.json`, `gate.json`, `evaluation.json`, the wheel hash, `deviations.json`, `cap-sizing.json` and `amendment.json` to match the hashes posted on #51 and the freeze record, and requires the amendment to name the unchanged freeze hashes;
- requires the frozen files to agree on the ceiling, timeout, assessor reservation, constraint and cap;
- checks every session diagnosis against its mechanical cells, the frozen rail and the cap (limit, `reached`, and per-request output summing to the session's output);
- recomputes the gate, the cause tables, the cap use, the constraint counts, the automated decisions and the side-by-side.

Some figures come from private captures or proxy reads, and are not recomputed:
- nare's error text;
- each session's per-request output split and constrained-turn counts (counted, but not authenticated);
- session durations;
- the proxy rows (the request at the cap, the aborted request, the smoke), the proxy-logged tokens, spend-log counts and the per-client load.

The retained captures' hashes are in `c1/original-artifact-sha256.json`, in the session diagnosis's `private_records_sha256`, and in `provider-evidence.json`. `c1/preparation.json` is published byte-identical to its posted hash, so it keeps one local path, the operator's nare console.

## What this does not establish

- Behavior on an idle engine. The launch rule was waived, and other clients used the engine throughout.
- Why the model cites past file ends or omits citation metadata, and whether LiteLLM's repetition check fired on true repetition.
- Review quality beyond the automated keyword decisions. Human adjudication is still outstanding.
