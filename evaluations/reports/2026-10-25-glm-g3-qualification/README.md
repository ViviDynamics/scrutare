# GLM 5.3 Flash qualification with line counts and citation correction — g3

**g3 did not pass the frozen execution/protocol gate.** 34 of 36 cells completed and were accounted for. Issue #96; refs #51, #91 and #85. This is a single development-corpus schedule; it does not establish semantic quality or justify promotion.

The gate was [frozen on #51 before launch](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6397590841). It preserves c1's gate criteria and pass-rule string byte for byte. All 23 corpus input hashes also match c1.

The profile uses the actual published Scrutare **2026.10.36** wheel at source `569a253057fbb55b21cc642ba08322f7568f5bac`, with **`context.line_counts: true`** and **`findings.citation_correction: true`**. The wheel's SHA-256 is `08f4b13b109564cc30ad6728c528ba45a5c9f31878c071b6d3f824668ba390c3`.

The other settings remain c1's: three pilot development cases, aggregate/assessed/focused1/focused3, three repeats (36 cells), 200,000 review ceiling, 50,000 assessor reservation, discovery grants 37,500 (50,000 in aggregate), per-request output cap 20,000, `json_schema`, nare 2026.10.12, six initial native turns (plus the bounded citation correction when eligible), concurrency 3 and 1800 s per session. Every attempt remains published. A cell needing a transient rerun fails the first-attempt item regardless of its eventual outcome.

A machine-detectable citation defect receives at most one tool-free, schema-constrained correction within the original session grant. Evidence still invalid after correction fails the session and cell; validation is unchanged. A LiteLLM repetition abort is a `model_repetition` failure and is ineligible for a transient provider rerun. The release also fixes cap attribution when the same request exhausts its session budget (#84/PR #89); c1 had one such misattribution and one repetition abort (#86/PR #90). Apart from those fixes and citation correction, no runtime source changed since c1. No grant, timeout, roster or pass threshold was changed.

The frozen gate allows g3 and at most one prospective re-test, which requires explicit maintainer approval and a newly posted freeze before launch. No re-test or c2 was run.

The launch condition is in the gate: **launch without an idle window**, recording load before and throughout. The authority is the maintainer's 2026-10-25 instruction (recorded in #96) and the [2026-10-22 c1 precedent](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6340502541). on-prem host carries other clients' traffic outside this evaluator's control. The evaluator launched at **12:25:44Z** as `scrutare-glm-g3.service`, with the evaluator as the unit's main process, and a separate load-sampler service.

## Gate results

| Criterion | g3 | c1 | s2 |
| --- | ---: | ---: | ---: |
| Complete and accounted | 34/36 | 31/36 | 34/36 |
| Complete and accounted on first attempt | 34/36 | 31/36 | 34/36 |
| Aggregate defect cells with non-empty valid v2 evidence | 6/6 | 4/6 | 6/6 |
| Assessed defect cells with an actual complete assessor | 5/6 | 6/6 | 5/6 |
| Every attempt within the 200,000 ceiling | pass | pass | pass |
| Transient rerun attempts | 0 | 0 | 0 |

g3 status counts: `{"complete": 34, "failed": 2}`. The experiment is terminal and its matrix has exactly 36 cells. Maximum attempt: 120,132 tokens. Clean-case cells complete: 12/12. Discussion rounds attempted/completed: 0/0.

## Failed cells

| Cell | Causes / failed gate items |
| --- | --- |
| `security-auth-order/focused1/2` | `invalid_finding_anchor`, `accounting_incomplete` |
| `tests-consumer-fixture/assessed/1` | `assessment_candidate_identity`, `accounting_incomplete`, `assessor_not_run_or_incomplete` |

Failed native sessions:

- `security-auth-order/focused1/2` attempt 1, `junior-dev`: `invalid_finding_anchor` (`protocol`).
- `tests-consumer-fixture/assessed/1` attempt 1, `evidence-assessor`: `assessment_candidate_identity` (`protocol`).

The discovery failure included an extra placeholder finding: `file: placeholder-unused`, `line: 0`, with no citations. The released validator refused it with `line: expected a positive integer`; citation-only correction cannot repair an invalid finding anchor. The assessor was given four candidate IDs but returned five assessments, including one unknown ID. The validator refused it with `assessment: duplicate or unknown candidate`. Citation correction applies to discovery citation defects, not assessor candidate coverage. Both native streams ended normally and their saved outputs agree with their terminal events.

Both failed cells carry the engine's `accounting_incomplete` flag. Separately, the recorded token totals exactly reconcile with the proxy rows; no request usage is missing from the spend comparison.

Every failed session and cell is retained. No outcome was excluded and no re-test was run.

## Sessions by cause

| Cause | g3 | c1 | s2 |
| --- | ---: | ---: | ---: |
| Sessions | 161 | 161 | 161 |
| Completed | 159 | 156 | 159 |
| `answer_evidence_refused` | 0 | 1 | 0 |
| `assessment_candidate_identity` | 1 | 0 | 0 |
| `budget_overrun_assessor` | 0 | 1 | 0 |
| `citation_past_file_end` | 0 | 2 | 1 |
| `invalid_finding_anchor` | 1 | 0 | 0 |
| `provider_aborted_repetition` | 0 | 1 | 0 |
| `session_timeout` | 0 | 0 | 1 |

c1's `provider_aborted_repetition` is the old name of the failure now classified `model_repetition`. Its budget-overrun assessor also hit the output cap; that historical misattribution is preserved in c1's frozen report. No published predecessor file was changed.

## Citation correction

Of 144 discovery sessions, 144 returned a first answer. 0 first answers were refused for citation defects. Corrections offered: **0**. Outcomes: `{}`. Correction-turn usage: **0 tokens**. Corrected sessions that completed: 0.

No correction fired, so this schedule cannot estimate the correction's live effect. It does establish the outcome of the profile with both settings enabled; the two settings' individual contributions are not separated.

## Usage, spend and concurrent load

| Measurement | g3 | c1 | s2 |
| --- | ---: | ---: | ---: |
| Recorded total tokens | 2,523,739 | 2,499,181 | 2,522,568 |
| Recorded output tokens | 501,956 | 574,191 | 561,745 |
| Mean cell latency (s) | 531.37 | 659.28 | 732.87 |

The authoritative proxy re-read covers 2026-10-25 12:25:44Z to 2026-10-25 14:14:15Z. It includes **639 rows**, of which **538** are nare evaluator requests and **101** are other on-prem host traffic. The evaluator's proxy totals are 2,021,783 prompt and 501,956 completion tokens. Differences from recorded cell usage: 0 prompt and 0 completion tokens. Failed proxy rows are retained in the summary.

**External spend: US$0. Zero evaluator paid-provider rows and zero attempted fallbacks.** LiteLLM's nominal attribution is US$0.554245, not billed. The read-only router checks before smoke, before launch and after exit all found one on-prem host deployment and no fallback routes. on-prem host base URLs are normalized with `rstrip('/')`; 0 rows use a trailing slash and are still self-hosted. Every sanitized serving row is published for offline verification.

Concurrent on-prem host traffic by user-agent family and key, from the full-window re-read:

| Client / key / base | Requests | Prompt tokens | Completion tokens |
| --- | ---: | ---: | ---: |
| `opencode \| proxy master key \| http://192.0.2.40:8045/v1` | 101 | 9,763,965 | 72,383 |
| `python-httpx2 \| proxy master key \| http://192.0.2.40:8045/v1` | 538 | 2,021,783 | 501,956 |

The preceding-hour read contains 29 other on-prem host requests, plus the separate smoke's three requests. 9 load samples were retained, with 0 read errors. The first covers mostly pre-launch traffic; the final sample was taken after exit and can overlap the preceding sample. They must not be summed as disjoint windows. Samples group by user agent, key and base URL. Each sample is a lower bound because in-flight requests are not yet logged; the full-window re-read after exit counts rows persisted by its recorded read time. Other clients still in flight can be logged later; requests bypassing LiteLLM are invisible. This schedule did not run on an idle engine.

The 88 returned findings include 85 matches under the unchanged prospective keyword rules; 3 remain pending for a person. These are mechanically validated observations, not unique or human-confirmed defects. No quality improvement is claimed.

## Pre-launch smoke and reporting deviations

One discovery session ran on the released wheel from 12:22:55Z to 12:24:37Z. It completed using 12,740 tokens. Prepared context contained explicit file line counts; the reviewer invocation included the line-bound guidance; the captured configuration and runtime correction-policy record showed citation correction enabled. A producer provenance inventory covered those artifacts. No citation defect occurred, so this smoke checked activation, not the correction's effect.

The frozen smoke harness had two incorrect assertions: it expected a shortened correction-policy record and the status `completed`. The released runtime correctly wrote the full record (`offered`, `defects`, `outcome`, `exit_code`) and status `complete`. A separate read-only verifier checked the already-retained artifacts and every provenance hash. No provider call was repeated, and no frozen file was edited. All three smoke requests were served by on-prem host with zero attempted fallbacks; their 10,022 prompt and 2,718 completion tokens exactly match the session.

Independent pre-launch review found two inherited reporting gaps: the old diagnosis script omitted correction-stream request costs, and it could not parse a transient-rerun path. The new `extract-sessions.py` counts both streams and retains cell-attempt identity. These are reporting changes; frozen scripts, the gate, and the running job were left intact. Tests exercise both cases.

## Reproduce and limits

Run `python3 reproduce.py --output g3-summary.json` with Python 3.10 or newer. It uses only the standard library and the c1 and s2 sibling report packages. It checks the public manifest, posted freeze hashes, c1 criteria and corpus identity, runtime settings, mechanical cells, per-session causes/correction accounting, and the serving/load evidence. It recomputes the published summary and refuses differences. `extract-sessions.py` optionally regenerates session metadata from the retained private job; it does not make provider calls.

Private native captures and raw proxy reads remain private and are represented by hashes. Public mechanical validation and first-answer classifications are exported claims from those captures; this package checks their consistency without authenticating the original responses. Public spend rows include only allowlisted fields, with personal aliases and nonstandard client names replaced by stable numbered labels. No key, key hash, personal email, client IP, request content or transcript is published.

The runs on different days/releases and concurrent load conditions are a side-by-side comparison, not a paired estimate of either setting's effect. Backend model revision is unverified. These pilot cases do not establish general reliability or semantic quality. Automated keyword judgments are provisional; zero human judgments exist. The holdout was not opened. No default was changed, nothing was promoted, and no App or infrastructure was changed. #51 remains open.
