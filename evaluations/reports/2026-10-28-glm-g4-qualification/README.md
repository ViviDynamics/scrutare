# GLM 5.3 Flash qualification with protocol correction — g4

**g4 did not pass the frozen execution/protocol gate.** 33 of 36 cells completed and were accounted for. Issue #99; refs #98, #51, #96 and #91. This single development-corpus schedule does not establish semantic quality or authorize promotion.

The gate was [frozen on #51 before launch](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6472089607). All 24 declared criteria/profile fields and the pass-rule string are byte-identical to g3/c1; all 23 corpus input hashes match. The actual published Scrutare **2026.10.38** wheel comes from `fe1f3dc8dd5e80e6240b5d2148d58d37ec0ed057`. Its SHA-256 is `43634957026d0e3d1ec049f7a8f6cabebf986a7191b60e793408769b890a2e6b`.

The profile retains g3's `context.line_counts: true` and `findings.citation_correction: true`, adding `findings.protocol_correction: true`. Protocol correction takes precedence: a session receives at most one correction. The model supplies a complete replacement answer for deterministic protocol defects; unchanged validation decides acceptance. The engine never drops, clamps or inserts findings, lines or candidate IDs. A still-invalid or unanswered correction fails its session and cell and remains in the 36/36 gate.

Other settings remain unchanged: three pilot development cases; aggregate/assessed/focused1/focused3; three repeats; 200,000 review ceiling; 50,000 assessor reservation; 37,500 discovery grants (50,000 aggregate); 20,000 per-request output cap; `json_schema`; nare 2026.10.12; six initial native turns plus at most one eligible bounded correction; concurrency 3; 1800 s session timeout; no overrun fallback. Every attempt is retained. A cell needing a transient provider rerun fails the first-attempt item. Protocol refusals and `model_repetition` are ineligible for transient reruns.

The gate allows g4 plus at most one prospective re-test, only on explicit maintainer approval and a newly posted freeze. No re-test was run. The frozen launch condition requires no idle window under the maintainer's 2026-10-25 (#96) and 2026-10-28 (#99) instructions and the [c1 precedent](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6340502541). g4 launched at **2026-10-28T15:45:39Z** as `scrutare-glm-g4.service`, with separate load and finish-evidence units.

## Gate results

| Criterion | g4 | g3 | c1 | s2 |
| --- | --- | --- | --- | --- |
| Complete and accounted | 33/36 | 34/36 | 31/36 | 34/36 |
| Complete and accounted on first attempt | 33/36 | 34/36 | 31/36 | 34/36 |
| Aggregate defect cells with non-empty valid v2 evidence | 6/6 | 6/6 | 4/6 | 6/6 |
| Assessed defect cells with an actual complete assessor | 4/6 | 5/6 | 6/6 | 5/6 |
| Every attempt within the 200,000 ceiling | pass | pass | pass | pass |
| Transient rerun attempts | 0 | 0 | 0 | 0 |

g4 status counts: `{"complete": 33, "failed": 2, "invalid": 1}`. The matrix contains 36 cells; clean cells complete: 12/12. Maximum recorded attempt: 120,273 tokens. Discussion rounds attempted/completed: 0/0.

## Failed cells

| Cell | Causes / failed gate items |
| --- | --- |
| `security-auth-order/assessed/3` | `output_cap`, `assessor_not_run_or_incomplete` |
| `tests-consumer-fixture/assessed/3` | `budget_overrun_assessor`, `assessor_not_run_or_incomplete` |
| `tests-consumer-fixture/focused3/2` | `output_cap` |

Failed native sessions:

- `security-auth-order/assessed/3`, attempt 1, `junior-dev`: `output_cap` (`output_cap`).
- `tests-consumer-fixture/assessed/3`, attempt 1, `evidence-assessor`: `budget_overrun_assessor` (`budget`).
- `tests-consumer-fixture/focused3/2`, attempt 1, `evidence-assessor`: `output_cap` (`output_cap`).

The discovery loss in `security-auth-order/assessed/3` stopped on request three: recorded outputs were `66, 509, 20000`. Its 30,725 total tokens were below its 37,500 grant. The cap stopped the request before an acceptable final answer; no correction was offered, and assessment did not run after failed discovery.

The `tests-consumer-fixture/assessed/3` assessor returned native status `done/end_turn`, but consumed **58,769 tokens (38,471 input + 20,298 output)** against its **50,000 grant**, an overrun of **8,769**. The engine retained it as `partial/budget` and made the cell `invalid`. Its request outputs were `23, 517, 4958, 11783, 3017`; none reached the per-request cap. The cell's 120,273 total tokens remain below the shared 200,000 ceiling. Passing that shared ceiling item does not excuse exceeding the assessor's own reservation.

The `tests-consumer-fixture/focused3/2` assessor stopped at the cap on request four: recorded outputs were `96, 224, 67, 20000`. Its 45,951 total tokens were below its 50,000 grant. No correction was offered. Per-session output totals can exceed 20,000 across several requests; neither cap failure exceeded the 20,000 limit on an individual request.

All three losses have complete recorded accounting. Every request cost reconciles exactly with the proxy, including both capped requests. There were no g4 session timeouts, repetition aborts, transient provider reruns or post-hoc exclusions. All 36 first attempts remain in the gate.


## Protocol corrections

Sessions completed: 158/161. Protocol-policy sessions: 161; phase counts: `{"assessment": 17, "discovery": 144}`. First answers returned: 159; refused: 0. Corrections offered: **0**. Outcomes: `{}`. Recorded correction-turn usage: **0 tokens**. Corrected sessions that completed: 0.

No correction fired, so this schedule cannot estimate its live effect.

## Usage, spend and concurrent load

| Measurement | g4 | g3 | c1 | s2 |
| --- | --- | --- | --- | --- |
| Recorded total tokens | 2514190 | 2523739 | 2499181 | 2522568 |
| Recorded output tokens | 534084 | 501956 | 574191 | 561745 |
| Mean cell latency (s) | 722.8 | 531.37 | 659.28 | 732.87 |

The authoritative proxy re-read covers 2026-10-28 15:45:39Z to 2026-10-28 18:16:21Z. It contains **861 rows**: **529** nare evaluator requests, **331** other Spark requests, and **one unrelated opencode request to the self-hosted Ada/Qwen deployment on another key**. The evaluator called only Spark. The Ada row is concurrent traffic, not an evaluator call or fallback, and is retained in the public evidence. Native/proxy differences: `{"input_difference": 0, "output_difference": 0, "request_difference": 0}`. Exact match: `True`. Any nonzero difference remains visible and requires a cause explanation; no outcome is excluded.

**External spend: US$0. Zero evaluator paid-provider rows and zero attempted fallbacks.** LiteLLM's nominal attribution is US$0.564058, not billed. Read-only router checks before smoke, before launch and after exit found one Spark deployment and no fallback routes. Spark base URLs are normalized with `rstrip('/')`; 0 window rows use a trailing slash and remain self-hosted. Sanitized serving rows are published for offline verification.

| Client / key / base | Requests | Prompt tokens | Completion tokens |
| --- | --- | --- | --- |
| `opencode \| odysseus \| http://192.168.3.40:8045/v1` | 28 | 875995 | 26418 |
| `opencode \| odysseus \| http://ollama-ada-w4v.ollama.svc.cluster.local:11434/api/generate` | 1 | 1745 | 319 |
| `opencode \| proxy master key \| http://192.168.3.40:8045/v1` | 284 | 26215015 | 112074 |
| `python-httpx \| proxy master key \| http://192.168.3.40:8045/v1` | 19 | 1309122 | 44768 |
| `python-httpx2 \| proxy master key \| http://192.168.3.40:8045/v1` | 529 | 1980106 | 534084 |

The preceding-hour read contains 161 Spark rows, including smoke and other native-client traffic. 12 load samples were retained, with 0 read errors. Samples overlap and must not be summed. Each is a lower bound until in-flight requests are logged. The full-window re-read counts rows persisted by its recorded read time; other clients still in flight can arrive later, and traffic bypassing LiteLLM is invisible. This schedule did not run on an idle engine.

The 85 returned findings include 82 matches under the unchanged prospective keyword rules; 3 remain pending for a person. One clean aggregate cell returned a finding, which remains unadjudicated. These are mechanically validated observations, not unique or human-confirmed defects. No quality improvement is claimed.

## Smoke timeout, maintainer ruling and deviations

The only released-wheel smoke ran from **2026-10-28T14:44:37Z to 2026-10-28T14:59:39Z**. It **failed at its own 900 s session timeout** (901.6 s elapsed), while request four was still generating. That request produced 11,536 completion tokens at about 17 tok/s, below the 20,000 output cap. The gate's session timeout remains 1800 s. Prepared line counts, citation and protocol settings, runtime policy, line-bound prompt and every producer provenance hash verified from retained artifacts. This establishes activation; no correction was offered and no answer completed.

The first three requests exactly match the retained native usage: 10,057 input + 2,131 output = 12,188 tokens. Proxy totals are 14,545 prompt + 13,667 completion = 28,212 tokens. Request four accounts for the entire difference: 4,488 prompt + 11,536 completion = 16,024 tokens, retained at local cancellation without a native terminal cost record. The smoke window had 40 Spark rows, zero paid calls, zero attempted fallbacks and US$0 external spend.

Jason's maintainer ruling dated 2026-10-28 was recorded in `g4-amendment.json` and [posted on #51 before launch](https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6473582253):

> Launch g4 now. The released-wheel smoke verified that the settings are active in prepared inputs and provenance. Its failure was a session timeout at the smoke’s own 900 s limit, below the gate’s 1800 s session timeout, on a request still generating under the output cap. The smoke’s completion requirement is waived for g4. Launch g4 unchanged, with no change to any frozen gate file or criterion. No further smoke.

The amendment SHA-256 is `2b573b08565c607a227df0adf65cdc60a7d7256b3c83174f57b3fe2cf60f068b`. All 55 recorded frozen artifacts and preparation inputs re-hashed unchanged. The frozen launcher requires complete smoke verification, so a **new** `launch-g4-amended.py` replaced only that check with the posted amendment/hash requirement. Its SHA-256 is `f83d8d5e4e558db1a05b0c86a44eefee599d88c69d5044e53ab91e7422999bcd`; the exact bytes are published as `launch-g4-amended.py.txt`. It retained all original hash checks, the absent-process requirement, the read-only router check, the preceding-hour capture and the same three systemd units. It added read-backs of the posted freeze/amendment bodies and verification of the launcher and preparation input hashes. No frozen script or criterion changed. No further smoke or re-test ran.

New read-only reporting helpers replace the inherited smoke-completion assumptions. Their exact bytes are retained as `.py.txt` files. The report extractor includes both correction and initial streams, all cell attempts, and discussion/reassessment sessions without counting them as initial sessions. No frozen file or published predecessor package was edited.

## Reproduce and limits

Run `python3 reproduce.py --output g4-summary.json` with Python 3.10 or newer. It uses only the standard library and the immutable g3, c1 and s2 sibling packages. It verifies the public manifest, posted freeze and amendment hashes, amendment timing, unchanged criteria/corpus/settings, mechanical cells, session corrections/accounting, smoke cancellation accounting and serving/load evidence. It refuses a differing summary. `extract-sessions.py` can regenerate session metadata from retained private captures without provider calls.

Private native captures and raw proxy reads remain private and are represented by hashes. Public exports cannot independently authenticate the provider or establish semantic truth. Personal aliases/emails, keys/hashes, client IPs, request content and transcripts are excluded. Runs on different days/releases and concurrent loads provide a side-by-side comparison, not a paired causal estimate. Backend model revision is unverified. These pilot cases do not establish general reliability. Zero actual human judgments were added. The holdout remains unopened. No defaults, promotion, App, infrastructure or deployment changes were made. #51 remains open.
