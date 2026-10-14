# Qualification of the self-hosted `spark/glm-5.3-flash`

**Result: not qualified.** The one judged schedule (`g2`) completed 14 of 36
cells, all with complete accounting, and failed every gate item that depends on
the defect cases. A first schedule (`g1`) was interrupted after 6 cells when the
operator's tool session ended; it is retained and was not judged. No further
schedule was run, because the failures that sank `g2` include a class no
profile setting addresses (below). External spend was US$0. Judgments are
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
under this package's stricter reading of reruns; none of them needed one.

## Why `g2` failed

`g2` ran 144 discovery sessions and 3 assessor sessions; no discussion round
started, because no discussion arm got past discovery. 109 discovery sessions
completed, and none of the 3 assessors did. The other 38:

| Session outcome | Sessions | Cause |
| --- | ---: | --- |
| `timeout` | 22 discovery, 1 assessor | still generating at 600 s. The sessions inspected were inside one long reasoning turn (one held about 19,900 characters of reasoning after 9.5 minutes), not stalled. |
| `provider` / `schema_violation` | 9 discovery | the answer was not valid JSON for the findings schema, twice. nare accepts an answer only when the whole text (or one fenced block) is JSON and reprompts once. The failures were missing brackets or delimiters in otherwise complete answers, `citations` placed beside `evidence` instead of inside it, a missing required `trigger`, and (in `g1`) valid JSON followed by stray tokens. |
| `budget` | 3 discovery, 2 assessors | used up their grants (25,000 or 20,000 tokens) |
| `citation_validation` | 1 discovery | one citation past the end of a file (`line_range_outside_capture`), the shape seen before with `gpt-6.1-sol` |

Ten of the 22 failed cells failed only because of timeouts. A longer timeout
might have saved those. The other twelve also hold a schema, budget or citation
failure. Two of the schema failures are in the aggregate arm (`security-auth-order`
repeats 2 and 3), so the six-aggregate-cell criterion fails whatever the timeout.
No setting in Scrutare or nare repairs a malformed final answer. The gate allowed
one further schedule only for "a diagnosable reason that a documented,
prospectively frozen profile change addresses", so none was run.

The engine was shared during `g2`. The LiteLLM spend log for its window
(`provider-evidence.json`) shows 488 requests from this evaluator, plus 113 from
opencode (about 7.0 million prompt tokens, contexts up to about 120,000) and 95
from Claude Code (about 2.5 million prompt tokens), all served by the same Spark
deployment. During `g1` there were two other requests. The timeouts are partly
a property of that load, and the deployed App would face the same shared engine.
The schema failures come from the model's own output.

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
cell usage of every attempt.

| | `g2` (glm-5.3-flash) | Sol r1 | Sol r2 | Sol r3 |
| --- | ---: | ---: | ---: | ---: |
| Total tokens | 1,933,565 | 1,643,358 | 1,641,825 | 1,642,692 |
| Output tokens | 393,364 | 137,295 | 136,920 | 137,253 |
| Mean cell latency | 504 s | 38.6 s | 34.5 s | 34.7 s |
| Median cell latency | 596 s | 40.9 s | 36.6 s | 36.6 s |
| Mean tokens, aggregate arm | 55,522 | 37,630 | 37,629 | 37,627 |
| Mean latency, aggregate arm | 425 s | 18.9 s | 17.6 s | 17.2 s |

Completed GLM sessions took a median 318 s (Sol r1: 16.5 s). They emitted a
median 2,836 output tokens, most of it reasoning (Sol r1: 895). The pre-freeze probe
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

- `g1/`: frozen on `c8059bb` and launched 15:20:11Z (hashes posted on #51 at
  15:19:59Z). Interrupted at 15:31:27Z, because the evaluator ran inside the tool
  session's systemd cgroup and was killed with it. Six cells were recorded: five
  complete on the clean case, and `assessed/1` invalid after one discovery
  session's answer carried stray tokens (`INTERRUPTED.json`). It was not judged.
  This package's operator gave the ruling that it does not use the further-schedule
  allowance.
- `g2-withdrawn-c8059bb/`: a replacement frozen on `c8059bb`, withdrawn before any
  call on finding that #76 had merged. The brief required current `main`.
- `g2/`: frozen on `5c4a8b6` (current `main`, including #76, whose opt-in
  settings are absent here). Its `evaluation.json` is byte-identical to `g1`'s and
  its gate criteria are identical. Hashes were posted on #51 before launch, and it
  ran detached in its own systemd user unit.

`freeze-record.json` holds the freeze hashes, the launch ledger and the comment
links. As in earlier packages, the ordering of the freeze before the first call
rests on operator records plus those timestamped public comments.

## Reproduce

```sh
python reproduce.py --output glm-qualification-summary.json
```

Python 3.10 or newer, standard library only, no network. It checks every
published hash here and in the two sibling packages it compares against,
recomputes `g2`'s gate, the automated decisions, `g1`'s recorded cells and the
side-by-side, and fails on any difference. It does not authenticate normalized
evidence against native sessions; that needs the retained private captures.
`original-artifact-sha256.json` in each job directory records their hashes. Raw
transcripts, credentials and provider headers are not published.

## What this does not establish

- How often a larger timeout would complete this profile. That was not run, and
  the schema failures would still void cells.
- Anything about review quality. Too few defect cells completed.
- Behavior on an unshared engine. The `g2` window had other heavy clients.
