# GLM 5.3 Flash operational qualification under rule A — g5

**g5 ${field_001} the frozen operational gate.** Accepted complete/accounted cells: **${field_002}/36**; first-attempt view: **${field_003}/36**. Issue #103; refs #102 and #51. This single development-corpus schedule does not establish semantic quality or authorize deployment or promotion.

The [prospective freeze](${field_004}) was posted before smoke and qualification calls. Release **${field_005}** at `${field_006}` uses the actual downloaded asset `${field_007}` (SHA-256 `${field_008}`). Its installed package bytes were checked against the wheel. All 23 corpus hashes and evaluation configuration bytes are identical to g4.

Rule A follows the [maintainer ruling](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6479128978) and common predicates 1–7, reproduced verbatim in `rule-a-common-predicates.md` and the frozen gate. Run all originals, then at most one fresh whole-pipeline replay for each eligible failed cell in fixed order, using identical captured inputs in a new directory. Keep the original success, otherwise the sole successful rerun; failed-attempt answers are discarded from acceptance. A second failed attempt fails qualification. A completed empty/invalid answer and hard accounting/provenance/routing failure are not permission to retry for a better answer. Original failures remain visible. Every attempt is charged; no grant, cap, timeout, prompt or correction allowance increases. No schedule-wide failure cap is added. All 24 defect cells require nonempty valid evidence, all 12 clean cells complete, six aggregate cells valid evidence, and six assessed cells actual completed accounted assessors. Human adjudication and holdout validation remain separate.

The only qualification changes from g4 are the released rule A implementation and `--qualification-rule-a`. Transient retries are disabled to avoid stacking policies. Other settings: three pilot cases × four arms (aggregate/assessed/focused1/focused3) × three repeats; 200,000 shared per-attempt ceiling; 50,000 assessor reservation; 37,500 discovery grants (50,000 aggregate); 20,000 per-request output cap; six initial native turns; 1800-second session timeout; concurrency 3; nare 2026.10.12; `json_schema`; line counts and citation/protocol correction on. Protocol correction takes precedence for one bounded within-session opportunity, under unchanged validators; it is not a new session or whole-cell rerun. No overrun fallback. The g4 grant overrun/caps remain failures; the maintainer explicitly retained these bounds for g5.

The gate permits **g5 only**; any re-test needs explicit maintainer approval and a separately posted prospective freeze. None was run. The frozen launch condition requires **no idle window**, citing the maintainer's 2026-10-25 (#96) and 2026-10-28 (#99/#103) instructions and [c1 precedent](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6340502541). Read-only routing and a completed released-wheel smoke preceded launch. g5 started at **${field_009}** as `scrutare-glm-g5.service`, with separate load and finish units.

## Gate and first-attempt views

${field_010}

Historical rows retain their published verdicts. No recovery was measured in g4/g3/c1/s2; they are not re-declared passes under A. The first-attempt view uses only successful originals and never borrows evidence or assessor execution from a rerun. Failed native sessions count once each; failed whole cells add no session failure. Started sessions: ${field_011}; complete: ${field_012}; unstarted planned sessions: ${field_013} (excluded from the failure denominator). Original/rerun failed sessions: ${field_014}/${field_015}. Original status counts: `${field_016}`; final: `${field_017}`.

${field_018}

## Original failures, recoveries and remaining failed cells

All native failures, including recovered originals:

${field_019}

Whole-cell recoveries (${field_020} reruns, ${field_021} total attempts):

${field_022}

Remaining failed cells:

${field_023}

A per-session grant overrun, cap, timeout or protocol failure stays failed even if its provider HTTP status is success. All failed requests and overshoots are charged. A complete cell with empty/invalid required evidence fails the gate and cannot be rerun for improved evidence. `g5/session-diagnosis.json` and `g5/provider-ledger.json` retain every original/rerun session/request identity and cost; `g5/mechanical-qualification.json` retains accepted selection and failed predicates.

## Observations, latency and discussion

${observations}

## Corrections

Protocol corrections offered across all attempts: **${field_024}**. By type: `${field_025}`; by outcome: `${field_026}`; type/outcome: `${field_027}`. Correction-turn tokens: **${field_028}**. Corrected sessions completed: ${field_029}. First answers returned/refused: ${field_030}/${field_031}. Types count session incidences; one turn may name several types and must not be counted several times. Bounded corrections and schema-constrained turns remain within their owning session and original grant.${field_032}

## Cumulative usage and spend

Known retained provider usage by phase (originals, reruns and any unassigned requests; unavailable attribution is not zero total cost): `${cost_by_phase}`.

Native cumulative usage across all g5 attempts: `${field_033}`. Known retained provider usage: `${field_034}`. Explicit missing-request supplements: `${field_035}` (${field_036} requests). Native accounting flags are unchanged. Attribution result: `${field_037}`; unresolved errors: `${field_038}`. Unknown/missing cost is not zero and fails qualification.

${field_039}

The s2/c1 reconciled totals add their retained missing failed requests once (51,569 / 6,152 tokens), as documented in the approved gate proposal. Their original accounting flags and historical failed verdicts remain unchanged. g3/g4 have no supplement.

${spend_statement}

## Smoke, concurrent load and deviations

${smoke_statement}

${field_050}

${field_051}

Load samples: **${field_052}**, read errors: **${field_053}**. Export/read receipt: `${load_receipt}`. Available preceding-hour and full-window reads retain sanitized agent/key/base splits; a missing read is unknown. Samples overlap and must not be summed. In-flight requests appear only when logged; full-window counts cover records persisted by their read time, and traffic bypassing LiteLLM is invisible. These are different-day, different-release schedules under concurrent load, not a paired causal comparison.

Deviations and scope clarifications: the first publishing agent stopped on model capacity while g5 was still running; this continuation performed publication only after the evaluator, load and finish units exited. Nothing frozen changed, and no additional model call, smoke or qualification was made. The named Part 1 handoff was missing from both supplied locations; preparation used merged source, the actual release and retained unfinished helpers. The three-cell shape of the one released-evaluator smoke was declared prospectively. Only measurement/launch/collection/check scripts were execution-frozen; copied incomplete report/export templates were explicitly excluded. New offline publication helpers handle missing evidence, use an atomic destination, and have separate tests/review/manifest; no frozen file was edited. Any inaccurate historical wording in inherited frozen prose is preserved as a historical statement and does not override rule A's new pass rule. g5 is the only qualification schedule run; no re-test, exclusion, deployment, infrastructure/default change or promotion was authorized.

## Reproduce and limits

Run `python3 reproduce.py --output /tmp/g5-summary.json` with Python 3.10 or newer. It verifies the public file manifest, frozen execution hashes, g4 profile/corpus identity, unchanged predecessor reports, rule A predicates, native/provider request attribution, correction accounting and first-attempt view. The summary must match `summary.json`. `test_report_g5.py` covers counter/identity tampering, supplements, selection and missing-metadata exports. Extraction helpers and hashes bind private native/provider captures; those captures remain private. Public exports cannot independently authenticate the provider, replay omitted transcripts, or establish true-positive semantics. The frozen preparation's pinned local nare path is retained only to preserve its posted byte hash; personal provider aliases/emails, API keys/hashes, client IPs, request bodies and transcripts are excluded from serving evidence. Backend revision is unverified.

This is an operational-qualification pass only. The #51 quality promotion gate (human adjudication and holdout validation) is separate and still open. No actual human judgments were added. Mechanical evidence validity is not semantic confirmation. The holdout was not opened. #51 remains open; the maintainer decides any subsequent deployment or quality evaluation.
