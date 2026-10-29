# GLM 5.3 Flash operational qualification under rule A — g5

**g5 passed the frozen operational gate.** Accepted complete/accounted cells: **36/36**; first-attempt view: **34/36**. Issue #103; refs #102 and #51. This single development-corpus schedule does not establish semantic quality or authorize deployment or promotion.

The [prospective freeze](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6482221015) was posted before smoke and qualification calls. Release **2026.10.40** at `f6162931052b4f96e24cc82f29820aaa5fce6c4f` uses the actual downloaded asset `scrutare-2026.10.40-py3-none-any.whl` (SHA-256 `4c5635d60ff104d1778362315584cf7ed20fc9f85e1c658fc65f34bbaa328b6f`). Its installed package bytes were checked against the wheel. All 23 corpus hashes and evaluation configuration bytes are identical to g4.

Rule A follows the [maintainer ruling](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6479128978) and common predicates 1–7, reproduced verbatim in `rule-a-common-predicates.md` and the frozen gate. Run all originals, then at most one fresh whole-pipeline replay for each eligible failed cell in fixed order, using identical captured inputs in a new directory. Keep the original success, otherwise the sole successful rerun; failed-attempt answers are discarded from acceptance. A second failed attempt fails qualification. A completed empty/invalid answer and hard accounting/provenance/routing failure are not permission to retry for a better answer. Original failures remain visible. Every attempt is charged; no grant, cap, timeout, prompt or correction allowance increases. No schedule-wide failure cap is added. All 24 defect cells require nonempty valid evidence, all 12 clean cells complete, six aggregate cells valid evidence, and six assessed cells actual completed accounted assessors. Human adjudication and holdout validation remain separate.

The only qualification changes from g4 are the released rule A implementation and `--qualification-rule-a`. Transient retries are disabled to avoid stacking policies. Other settings: three pilot cases × four arms (aggregate/assessed/focused1/focused3) × three repeats; 200,000 shared per-attempt ceiling; 50,000 assessor reservation; 37,500 discovery grants (50,000 aggregate); 20,000 per-request output cap; six initial native turns; 1800-second session timeout; concurrency 3; nare 2026.10.12; `json_schema`; line counts and citation/protocol correction on. Protocol correction takes precedence for one bounded within-session opportunity, under unchanged validators; it is not a new session or whole-cell rerun. No overrun fallback. The g4 grant overrun/caps remain failures; the maintainer explicitly retained these bounds for g5.

The gate permits **g5 only**; any re-test needs explicit maintainer approval and a separately posted prospective freeze. None was run. The frozen launch condition requires **no idle window**, citing the maintainer's 2026-10-25 (#96) and 2026-10-28 (#99/#103) instructions and [c1 precedent](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6340502541). Read-only routing and a completed released-wheel smoke preceded launch. g5 started at **2026-10-28T22:37:19Z** as `scrutare-glm-g5.service`, with separate load and finish units.

## Gate and first-attempt views

| Criterion | g5 rule A | g5 first attempt | g4 | g3 | c1 | s2 |
| --- | --- | --- | --- | --- | --- | --- |
| Complete/accounted accepted cells | 36/36 | 34/36 | 33/36 | 34/36 | 31/36 | 34/36 |
| Clean cells complete | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| Defect cells with nonempty valid evidence | 24/24 | 22/24 | 21/24 | 22/24 | 19/24 | 22/24 |
| Aggregate defect evidence | 6/6 | 6/6 | 6/6 | 6/6 | 4/6 | 6/6 |
| Actual complete assessed defect assessors | 6/6 | 6/6 | 4/6 | 5/6 | 6/6 | 5/6 |
| Published operational verdict | pass | not qualified | not qualified | not qualified | not qualified | not qualified |

Historical rows retain their published verdicts. No recovery was measured in g4/g3/c1/s2; they are not re-declared passes under A. The first-attempt view uses only successful originals and never borrows evidence or assessor execution from a rerun. Failed native sessions count once each; failed whole cells add no session failure. Started sessions: 170; complete: 168; unstarted planned sessions: 0 (excluded from the failure denominator). Original/rerun failed sessions: 2/0. Original status counts: `{"complete": 34, "failed": 2}`; final: `{"complete": 36}`.

| Common predicate / retained execution check | Result |
| --- | --- |
| accepted_replay_valid | pass |
| all_12_clean_complete | pass |
| all_24_defect_cells_nonempty_valid | pass |
| all_36_complete_accounted | pass |
| all_attempts_provenance_routing | pass |
| all_attempts_reconciled | pass |
| all_attempts_within_ceiling | pass |
| all_provider_requests_attributed_once | pass |
| bounded_rule_a_selection | pass |
| cumulative_cost_complete | pass |
| every_attempt_terminal | pass |
| experiment_terminal | pass |
| g5_spend_verified | pass |
| holdout_untouched | pass |
| independent_attempt_execution | pass |
| independent_pipeline_sessions | pass |
| independent_request_attribution | pass |
| matrix_exact | pass |
| native_attempts_consistent | pass |
| provider_attribution_succeeded | pass |
| provider_window_ledger_consistent | pass |
| released_predicates_consistent | pass |
| released_process_exit_success | pass |
| router_after | pass |
| router_prelaunch | pass |
| router_smoke | pass |
| rule_a_frozen | pass |
| six_aggregate_nonempty_valid | pass |
| six_assessed_actual_complete | pass |
| smoke_complete_verified | pass |
| smoke_spend_verified | pass |
| terminal_experiment_record | pass |
| zero_paid_calls_and_fallbacks | pass |

## Original failures, recoveries and remaining failed cells

All native failures, including recovered originals:

| Cell | Cell attempt | Persona | Terminal status/reason | Cause | Native tokens |
| --- | --- | --- | --- | --- | --- |
| `security-auth-order/focused3/3` | 1 | devops | failed/output_cap | output_cap | 30287 |
| `tests-consumer-fixture/focused1/1` | 1 | security | failed/output_cap | output_cap | 29956 |

Whole-cell recoveries (2 reruns, 38 total attempts):

| Cell | Original | Rerun | Selected attempt | Original/rerun reconciled tokens |
| --- | --- | --- | --- | --- |
| `security-auth-order/focused3/3` | failed | complete | 2 | 68860 / 113418 |
| `tests-consumer-fixture/focused1/1` | failed | complete | 2 | 65922 / 79700 |

Remaining failed cells:

None recorded. The complete gate table above remains authoritative, including any schedule-level accounting failure.

A per-session grant overrun, cap, timeout or protocol failure stays failed even if its provider HTTP status is success. All failed requests and overshoots are charged. A complete cell with empty/invalid required evidence fails the gate and cannot be rerun for improved evidence. `g5/session-diagnosis.json` and `g5/provider-ledger.json` retain every original/rerun session/request identity and cost; `g5/mechanical-qualification.json` retains accepted selection and failed predicates.

## Observations, latency and discussion

Accepted source observations: 96; automated matches under the unchanged prospective c1/Sol keyword rules: 96; pending for a person: 0. Clean cells with findings: 0. These are observations across repeats, not unique or human-confirmed defects; unmatched findings are pending, not scored false. Zero human judgments. Pipeline latency per logical cell, including failed attempt time but excluding queue wait: `{"cells": 36, "max": 2161.01, "mean": 765.73, "median": 749.38, "p95_nearest_rank": 1824.25}`. Discussion across retained attempts: `{"attempts_with_trace": 20, "complete": true, "expected_focused_attempts": 20, "known_rounds_subtotal": 0, "rounds_reported": 0, "trace_statuses": {"complete": 18, "failed": 2}, "unavailable_traces": 0}`. A missing trace is unknown and is never successful zero-round execution. All observations lack human adjudication; the unmatched count is only the subset not matched automatically.

## Corrections

Protocol corrections offered across all attempts: **0**. By type: `{}`; by outcome: `{}`; type/outcome: `{}`. Correction-turn tokens: **0**. Corrected sessions completed: 0. First answers returned/refused: 168/0. Types count session incidences; one turn may name several types and must not be counted several times. Bounded corrections and schema-constrained turns remain within their owning session and original grant. No correction fired, so its live effect remains unmeasured.

## Cumulative usage and spend

Known retained provider usage by phase (originals, reruns and any unassigned requests; unavailable attribution is not zero total cost): `{"originals": {"cache_read": 0, "cache_write": 0, "input": 1912008, "output": 525895, "total": 2437903}, "reruns": {"cache_read": 0, "cache_write": 0, "input": 152042, "output": 41076, "total": 193118}, "unassigned": {"cache_read": 0, "cache_write": 0, "input": 0, "output": 0, "total": 0}}`.

Native cumulative usage across all g5 attempts: `{"cache_read": 0, "cache_write": 0, "input": 2064050, "output": 566971, "total": 2631021}`. Known retained provider usage: `{"cache_read": 0, "cache_write": 0, "input": 2064050, "output": 566971, "total": 2631021}`. Explicit missing-request supplements: `{"cache_read": 0, "cache_write": 0, "input": 0, "output": 0, "total": 0}` (0 requests). Native accounting flags are unchanged. Attribution result: `True`; unresolved errors: `[]`. Unknown/missing cost is not zero and fails qualification.

| Run | Native tokens, all attempts | Reconciled tokens, all attempts |
| --- | --- | --- |
| g5 | 2631021 | 2631021 |
| g4 | 2514190 | 2514190 |
| g3 | 2523739 | 2523739 |
| c1 | 2499181 | 2505333 |
| s2 | 2522568 | 2574137 |

The s2/c1 reconciled totals add their retained missing failed requests once (51,569 / 6,152 tokens), as documented in the approved gate proposal. Their original accounting flags and historical failed verdicts remain unchanged. g3/g4 have no supplement.

Only onprem/glm-5.3-flash through LAN LiteLLM to192.0.2.40:8045 was authorized. Trailing-slash on-prem host URLs, including python-httpx, are normalized as self-hosted. Router and spend records cover smoke and every qualification attempt. Evaluator requests: 554; paid calls: 0; attempted fallbacks: 0. External spend: US$0. Known retained g5 provider rows: 554; known paid rows: 0; known fallback count: 0. Nominal operation attribution: 0.602180; known nominal subtotal: US$0.602180 (not billing). Complete reconciled operation tokens including smoke: 2,670,398; conservative known token lower bound: 2,670,398. Native/provider counters overlap and are not added together. No failed spend is erased by selection or token reset.

## Smoke, concurrent load and deviations

One released-wheel smoke job: 2026-10-28T22:25:05Z–2026-10-28T22:35:26Z, with 1800-second sessions matching g5. The released evaluator fixes three repeats, so the one job uses three senior-only security cells,50000 grants, and actual bounded A mode. It remains outside the36qualification cells. Completion, active prepared line counts/corrections, line-bound prompt, rule A snapshot/policy, provenance and accepted replay verified. No waiver/second smoke. Recorded completed cells: 3; attempts: 3; known native usage: {"cache_read": 0, "cache_write": 0, "input": 30290, "output": 9087, "total": 39377}; known reconciled usage: {"cache_read": 0, "cache_write": 0, "input": 30290, "output": 9087, "total": 39377}.

| Window | All logged rows | Evaluator requests | Other on-prem host requests | Other non-on-prem host requests | Nominal evaluator attribution |
| --- | --- | --- | --- | --- | --- |
| g5 | 844 | 554 | 290 | 0 | 0.593093 |
| prelaunch | 194 | 9 | 185 | 0 | 0.009087 |
| smoke | 47 | 9 | 38 | 0 | 0.009087 |

| Qualification client / key / base | Requests | Prompt tokens | Completion tokens |
| --- | --- | --- | --- |
| opencode \| proxy master key \| http://192.0.2.40:8045/v1 | 233 | 21078930 | 164044 |
| python-httpx \| proxy master key \| http://192.0.2.40:8045/v1 | 57 | 3991753 | 56256 |
| python-httpx2 \| proxy master key \| http://192.0.2.40:8045/v1 | 554 | 2064050 | 566971 |

Load samples: **13**, read errors: **0**. Export/read receipt: `{"errors": [], "sha256_private": "c31bd8c75b5d570eb0124b94cc7e5a4478988b65855121db8687e52e1814c6cd", "state": "available"}`. Available preceding-hour and full-window reads retain sanitized agent/key/base splits; a missing read is unknown. Samples overlap and must not be summed. In-flight requests appear only when logged; full-window counts cover records persisted by their read time, and traffic bypassing LiteLLM is invisible. These are different-day, different-release schedules under concurrent load, not a paired causal comparison.

Deviations and scope clarifications: the first publishing agent stopped on model capacity while g5 was still running; this continuation performed publication only after the evaluator, load and finish units exited. Nothing frozen changed, and no additional model call, smoke or qualification was made. The named Part 1 handoff was missing from both supplied locations; preparation used merged source, the actual release and retained unfinished helpers. The three-cell shape of the one released-evaluator smoke was declared prospectively. Only measurement/launch/collection/check scripts were execution-frozen; copied incomplete report/export templates were explicitly excluded. New offline publication helpers handle missing evidence, use an atomic destination, and have separate tests/review/manifest; no frozen file was edited. Any inaccurate historical wording in inherited frozen prose is preserved as a historical statement and does not override rule A's new pass rule. g5 is the only qualification schedule run; no re-test, exclusion, deployment, infrastructure/default change or promotion was authorized.

## Reproduce and limits

Run `python3 reproduce.py --output /tmp/g5-summary.json` with Python 3.10 or newer. It verifies the public file manifest, frozen execution hashes, g4 profile/corpus identity, unchanged predecessor reports, rule A predicates, native/provider request attribution, correction accounting and first-attempt view. The summary must match `summary.json`. `test_report_g5.py` covers counter/identity tampering, supplements, selection and missing-metadata exports. Extraction helpers and hashes bind private native/provider captures; those captures remain private. Public exports cannot independently authenticate the provider, replay omitted transcripts, or establish true-positive semantics. The frozen preparation's pinned local nare path is retained only to preserve its posted byte hash; personal provider aliases/emails, API keys/hashes, client IPs, request bodies and transcripts are excluded from serving evidence. Backend revision is unverified.

This is an operational-qualification pass only. The #51 quality promotion gate (human adjudication and holdout validation) is separate and still open. No actual human judgments were added. Mechanical evidence validity is not semantic confirmation. The holdout was not opened. #51 remains open; the maintainer decides any subsequent deployment or quality evaluation.
