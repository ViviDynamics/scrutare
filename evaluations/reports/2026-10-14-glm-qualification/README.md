# Qualification of the self-hosted `spark/glm-5.3-flash`

**Result: not qualified.** In the one judged schedule (`g2`), 14 of 36 cells
completed with complete accounting, and every gate item that depends on the
defect cases failed. A first schedule (`g1`) was interrupted after 6 cells when
the operator's tool session ended; it is retained and was not judged. The
operator chose not to run the one further schedule the gate permitted, because
`g2`'s failures include a class no profile setting addresses (below). External
spend was US$0. Judgments are
automated and provisional, there are zero human judgments, nothing here changes
a default, and `evaluations/holdout-v1` was not opened or used.

The profile is the one that qualified `gpt-6.1-sol` (three pilot development
cases, aggregate/assessed/focused1/focused3 arms, three repetitions, a 100,000
token review ceiling, the default 20,000 assessor reservation, six native turns,
concurrency 3, nare 2026.10.4 on its streaming OpenAI rail), with two forced
changes recorded in `gate.json` before launch: the model alias, and a 600 s
per-session timeout in place of 120 s.

## Gate

| Criterion | `g2` | `gpt-6.1-sol` r1 / r2 / r3 |
| --- | --- | --- |
| 36 cells complete and accounted | **14 of 36** (fail) | 36 / 36 / 36 |
| ... on the first attempt (a rerun counts as a failure here) | 14 of 36 | 36 / 36 / 36 |
| Six aggregate defect cells with non-empty valid v2 evidence | **2 of 6** (fail) | 6 / 6 / 6 |
| Six assessed defect cells whose assessor actually ran and completed | **0 of 6** (fail) | 6 / 6 / 6 |
| Every attempt within 100,000 tokens | pass (max 84,649) | pass |
| Upstream 5xx reruns used | 0 of 6 allowed | 0 / 0 / 0 |

The reference columns are recomputed by `reproduce.py` from the sibling packages
under this package's stricter reading of reruns; none of them needed one. They ran
on a different source commit (`2f1e95c` vs `5c4a8b6`), a day earlier, with a 120 s
session timeout and another model, so the columns are a side-by-side, not a
paired comparison.

## Why `g2` failed

`g2` ran 144 discovery sessions and 3 assessor sessions. No discussion round
started: the focused defect cells failed in discovery, except
`tests-consumer-fixture/focused3/3`, which failed in its assessor, and the clean
focused cells had no candidate to discuss. 109 discovery sessions completed, and
none of the 3 assessors did. The other 38:

| Session outcome | Sessions | Cause |
| --- | ---: | --- |
| `timeout` | 22 discovery, 1 assessor | still generating at 600 s. None had stalled: the longest gap between stream events in any of them was 62 s. Most were reasoning after a tool call, and several were streaming their final answer. One reasoning stretch ran to about 19,600 characters over 9.5 minutes. |
| `provider` / `schema_violation` | 9 discovery | the answer was not valid JSON for the findings schema, twice. nare accepts an answer only when the whole text (or one fenced block) is JSON and reprompts once. The failures were missing brackets or delimiters in otherwise complete answers, `citations` placed beside `evidence` instead of inside it, missing required `trigger` or `start_line` properties, and valid JSON followed by extra text (in `g1`, by stray tokens and a U+FFFD replacement character). |
| `budget` | 3 discovery, 2 assessors | exceeded 20,000-token grants. Two of the discovery sessions finished their answer and overshot by only 17 and 610 tokens. |
| `citation_validation` | 1 discovery | one citation past the end of a file (`line_range_outside_capture`), the shape seen before with `gpt-6.1-sol` |

Ten of the 22 failed cells failed only on timeouts. The other twelve hold a
schema, budget or citation failure (five of them with no timeout at all). Three schema failures (in
`security-auth-order` aggregate repeats 2 and 3) void aggregate defect cells.

The frozen gate permitted ("may run") one further schedule for a failure a
documented profile change addresses. Some `g2` failures were of that kind:
- **Timeouts:** a longer session timeout. The LiteLLM router's own request
  timeout is also 600 s, and the cut-off turns had already run for minutes, so a
  longer session timeout alone might not save them.
- **Assessor overruns:** the `--assessment-tokens` and #76 overrun-fallback
  opt-ins.
- **The one out-of-range citation:** `context.line_counts`.

No setting in Scrutare or nare repairs a malformed final answer, and nare
reprompts once. Schema failures hit 9 of 144 discovery sessions (6.25%; Wilson
95% from 3.3%). At that rate, the chance that all 144 discovery sessions of a
schedule avoid one is about 0.0001 (about 0.008 at the lower bound), so the
36-cell criterion is practically unreachable whatever else changes. The
six-aggregate-cell criterion alone needs 24 clean sessions, about a 1 in 5 to
1 in 8 chance at the observed rates. On that arithmetic the operator chose not to
run the further schedule. This is a judgment within the clause, not a
prohibition by it.

The engine was shared during `g2`. The LiteLLM spend log for its window
(`provider-evidence.json`) shows 488 requests from this evaluator, plus 113 from
opencode (about 7.0 million prompt tokens, contexts up to about 120,000) and 95
from Claude Code (about 2.5 million prompt tokens), all served by the same Spark
deployment. During `g1`'s run there was one other request (a second fell in the
same log window before launch). The timeouts are partly a property of that load,
and the deployed App would face the same shared engine. The schema failures are
in the model's output as served. This package cannot separate the model from the
serving stack: a vLLM development build, NVFP4 weights and speculative decoding
per the serving role. The `g1` case, with stray non-Latin tokens and a
replacement character after valid JSON, could be either.

## What the clean case shows

All 12 clean-case cells of `g2` completed, and none reported a finding, as with
`gpt-6.1-sol`. The defect cases are where it failed. Of the 8 findings `g2`
produced, all in the two complete aggregate defect cells, the frozen keyword rules
match 7 to the labeled defect. One more states the labeled defect but is anchored
one line above the labeled line, so it is left pending for a person. In the
refused answers read while diagnosing `security-auth-order/aggregate/2`, the model
had stated the labeled defect. A refused session contributes nothing, so that is
not detection, and the refused answers were not systematically read.

## Cost and speed against `gpt-6.1-sol`

Different model, source commit (`5c4a8b6` vs `2f1e95c`), day (2026-10-14 vs
2026-10-13) and session timeout (600 s vs 120 s). The token figures are recorded
cell usage of every attempt. For `g2` they **understate** what the model consumed:
19 of its 36 cells have incomplete accounting, and the 23 turns cut off by the
timeout are missing from cell usage. The proxy logged 488 requests from this
evaluator in the `g2` window, totalling 2,113,836 tokens (1,642,331 prompt and
471,505 completion), against 1,933,565 recorded. The Sol schedules had no
cut-off turns.

| | `g2` (glm-5.3-flash) | Sol r1 | Sol r2 | Sol r3 |
| --- | ---: | ---: | ---: | ---: |
| Total tokens | 1,933,565 | 1,643,358 | 1,641,825 | 1,642,692 |
| Output tokens | 393,364 | 137,295 | 136,920 | 137,253 |
| Mean cell latency | 504 s | 38.6 s | 34.5 s | 34.7 s |
| Median cell latency | 596 s | 40.9 s | 36.6 s | 36.6 s |
| Mean tokens, aggregate arm | 55,522 | 37,630 | 37,629 | 37,627 |
| Mean latency, aggregate arm | 425 s | 18.9 s | 17.6 s | 17.2 s |

Completed GLM discovery sessions took a median 318 s and emitted a median 2,836
output tokens, most of it reasoning. Sol r1's completed discovery sessions took
16.0 s and emitted 888.5. The pre-freeze probe
measured 39 output tokens/s for one stream and 15 to 19 per stream with 12
concurrent streams. Wall clock: `g1` ran 15:20:11Z to 15:31:27Z, and `g2` ran
18:48:07Z to 20:36:34Z (1 h 48 min for 36 cells; the Sol schedules took 7 to 8
minutes each, from their private launch records).

## Spend and the paid-fallback check

Before the first call, the LiteLLM configuration (`ViviDynamics/infrastructure`
`origin/main` `0a3de12`) and the live proxy were read. They showed no `fallbacks`,
`context_window_fallbacks` or `content_policy_fallbacks`, and the model-group
aliases name only Claude models. There is no wildcard or database-stored
deployment, and `spark/glm-5.3-flash` has exactly one deployment, `openai/glm-5.3-flash`
on the Spark LAN host. The router was read again before `g2` with the same result.
Afterwards, every spend-log row in both windows on this key, 822 rows from all
clients, was served by that deployment with zero attempted fallbacks. No paid
provider was called. LiteLLM attributes a nominal US$0.08 (g1 window, with
probes) and US$0.48 (g2) to this evaluator at its configured
US$0.15/US$0.50 per million rate for an unmapped self-hosted model. No provider
bills that.

Limits: the managed serving role sets `max_model_len` 262,144 and
`max_num_seqs` 16 (vLLM, tensor-parallel 3). LiteLLM advertises 262,144 input
and 32,768 output, the latter a cap chosen in the proxy and not an engine limit
shown here. An older unmanaged file still says 1,048,576. The host itself was not
read. Backend identity rests on the response `system_fingerprint` (vLLM, `tp3`)
and the serving role. The model revision behind the alias is not verified.

## Schedules retained

- `g1/`: frozen on `c8059bb` at 15:19:59Z, hashes posted on #51 at 15:20:00Z,
  launched 15:20:11Z. Interrupted at 15:31:27Z, because the evaluator ran inside the tool
  session's systemd cgroup and was killed with it. Six cells were recorded: five
  complete on the clean case, and `assessed/1` invalid after one discovery
  session's answer carried stray tokens (`INTERRUPTED.json`). It was not judged.
  This package's operator gave the ruling that it does not use the further-schedule
  allowance.
- `g2-withdrawn-c8059bb/`: a replacement frozen on `c8059bb`, withdrawn before any
  call on finding that #76 had merged. The brief required current `main`.
- `g2/`: frozen on `5c4a8b6` (current `main`, including #76, whose opt-in
  settings are absent here). Its `evaluation.json` is byte-identical to `g1`'s,
  and its gate criteria are identical. Its `gate.json` differs only in the profile
  label, the source-commit lines, the predecessor note and the fallback clause,
  which now names the further schedule `g3`. The #51 comment for `g2` listed the
  first three of those and missed the fallback clause. Hashes were posted at
  18:47:48Z and it launched at 18:48:07Z, detached in its own systemd user unit.

`freeze-record.json` holds the freeze hashes, the launch ledger and the comment
links. The ordering of each freeze before its first call rests on operator records
plus those unedited, timestamped public comments. The launcher refused to start
unless every input matched `preparation.json`. The nare runtime itself is not
hashed in the freeze.

## Reproduce

```sh
python reproduce.py --output glm-qualification-summary.json
```

Python 3.10 or newer, standard library only, no network. It fails on any
difference, and it:
- checks every published hash here and in the two sibling packages it compares
  against, and refuses files outside the manifest;
- requires `g1`'s and `g2`'s `preparation.json`, `gate.json` and
  `evaluation.json` to match the hashes posted on #51, the freeze record, the
  private-original hashes and the preparation's own input hashes. The withdrawn
  freeze is bound to the freeze record only, since it was never posted;
- requires each mechanical summary to name that gate and experiment;
- recomputes `g2`'s gate, the automated decisions, `g1`'s recorded cells and the
  side-by-side.

It does not recompute figures that come from private captures or proxy reads:
session durations and medians, probe rates, wall clock, spend-log counts and the
per-client split in `provider-evidence.json`. It does not authenticate normalized
evidence against native sessions. Both need the retained private captures, whose
hashes `original-artifact-sha256.json` records in each job directory. Raw
transcripts, credentials and raw provider responses are not published.

## What this does not establish

- How often a larger timeout would complete this profile. That was not run, and
  the schema failures would still void cells.
- Whether the schema failures belong to the model or to this serving stack.
- Anything about review quality. Too few defect cells completed.
- Behavior on an unshared engine. The `g2` window had other heavy clients.
