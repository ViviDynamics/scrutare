# Provisional automated architecture comparison

420 actual runs,20 development cases,13 defects,7 guarded clean controls. 652 explicit automated judgments:294 matched observations and358 false observations; 54 await actual human judgment. **Zero human judgments; defaults unchanged.**

| Arm | Complete / failed / invalid | TP / FP | Precision | Detected /39 | Recall | Pending | Observed tokens | p95 seconds | Clean FP / eligible |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| assessment | 1 / 0 / 59 | 0 / 0 | n/a | 0 /39 | 0.0% | 0 | 1,096,345 ≥ | 11.25 | None / 0 |
| baseline | 60 / 0 / 0 | 105 / 119 | 46.9% | 33 /39 | 84.6% | 20 | 368,772 | 5.50 | 20 / 20 |
| context | 60 / 0 / 0 | 104 / 127 | 45.0% | 30 /39 | 76.9% | 17 | 473,663 | 5.57 | 20 / 20 |
| evidence | 3 / 57 / 0 | 0 / 0 | n/a | 0 /39 | 0.0% | 0 | 1,087,821 ≥ | 9.44 | 0 / 2 |
| procedures | 60 / 0 / 0 | 85 / 112 | 43.1% | 31 /39 | 79.5% | 17 | 466,445 | 5.71 | 21 / 21 |
| routing | 1 / 59 / 0 | 0 / 0 | n/a | 0 /39 | 0.0% | 0 | 1,161,795 ≥ | 9.64 | 0 / 1 |
| tools | 0 / 0 / 60 | 0 / 0 | n/a | 0 /39 | 0.0% | 0 | 1,202,284 ≥ | 10.83 | None / 0 |

Context and revised procedures completed reliably here, but do not show an accuracy gain over the paired baseline. Context changes precision46.9%→45.0% and recall84.6%→76.9%; adding v1 procedures changes these to43.1% and79.5%. Precision is provisional and may move in either direction after pending judgments; recall is currently a lower bound where judgments remain pending. These are descriptive observations, not significance claims.

The structured arms produce no valid surviving source findings. Their measured0/39 recall reflects refused/incomplete runs, not clean approvals or proof that context, assessment or tools reduce code-review quality under other rails.

Recorded evaluation statuses are185 complete,116 failed,119 invalid. The runner tags a nonfailed engine outcome with no valid verdict as invalid; for example, assessment has59 engine partial/discovery_incomplete results recorded as59 invalid evaluation runs. Production scoring counts these exact recorded statuses. JSON separately preserves engine and native-session statuses, so invalid runs are never represented as clean approvals.

## Concrete reliability diagnosis

- 402 v2 sessions stop at native schema_violation. These are classified under Scrutare provider refusal, but do not demonstrate unavailable service.
- 187 native-complete v2 sessions fail protocol.189 individual claims fail the fixed complete revision/hash check;186 inspected citations have65-character hashes where64 are required. Additional hashes are truncated, nonhex or other wrong lengths.
- 52 checked-citation refusals:45 content_hash_mismatch,4 line_range_outside_capture, 2 revision_mismatch and1 path_not_captured. Mechanical validity is distinct from truth.
- 40 sessions stop under token budgets. No v2 session reaches the six-turn ceiling; most complete or fail after2–3 turns. No timeout evidence was recorded.
- Assessment/tools discovery is incomplete in59/60 and60/60 runs respectively. The independent assessor cannot repair candidates that failed discovery admission.
- All60 routed rosters remain senior-dev,junior-dev,security,devops; this corpus does not measure newly activated specialists.

## Paired component comparisons

All comparisons pair the exact same case/repeat cells. Detailed JSON contains each paired detection cell, domain/repeat metrics, source TP/FP, duplicate rates, unique detections, actual quotas, prompt hashes and tool statuses.

| Transition | Precision delta | Recall delta | Observed token ratio | p95 ratio |
|---|---:|---:|---:|---:|
| baseline_to_context | -1.9% | -7.7% | 1.28 | 1.01 |
| context_to_procedures | -1.9% | 2.6% | 0.98 | 1.02 |
| procedures_to_evidence | n/a | -79.5% | 2.33 | 1.65 |
| evidence_to_assessment | n/a | 0.0% | 1.01 | 1.19 |
| assessment_to_tools | n/a | 0.0% | 1.10 | 0.96 |
| evidence_to_routing | n/a | 0.0% | 1.07 | 1.02 |

## Repeat variation

Each repeat has13 defect opportunities per arm. These repeats share the same cases and are not independent datasets or statistical confidence intervals.
- baseline: recall 84.6%, 84.6%, 84.6%; precision 40.8%, 52.6%, 46.8%.
- context: recall 76.9%, 76.9%, 76.9%; precision 47.4%, 45.5%, 42.3%.
- procedures: recall 76.9%, 84.6%, 76.9%; precision 37.5%, 44.3%, 47.6%.

## Limits

- All judgments are automated;54 genuine ambiguities remain actual-human pending. Zero human judgments.
- One gpt-4.1-nano alias through LiteLLM; the dated backend revision is not verified. Results do not generalize across model rails.
- Twenty original development cases have13 distinct defects and7 clean controls. Three repeats give39 opportunities per arm, not39 independent defects; repeat ranges are descriptive, not confidence intervals.
- The original pilot holdout was already exposed during baseline work. The new40-case holdout was not used here and remains locally model-unseen. This development experiment cannot justify promotion.
- All failures and invalid verdicts remain in recall denominators. Zero observations from an invalid/no-verdict run do not mean clean approval; token totals with incomplete accounting are lower bounds.
- Native error/schema_violation is recorded under Scrutare provider refusal; it does not demonstrate provider unavailability. Malformed hashes and checked citation mismatches are protocol failures, not evidence that allegations are semantically false.
- No v2 source findings survive into valid verdicts. Evidence completed3/60,assessment1/60,tools0/60,routing1/60; quality comparisons of those arms are dominated by execution reliability, not proven semantic gains.
- Every routing run used the same original four personas; generic original pilot paths never activated a specialist. The routing arm measures routing artifacts/focus under unchanged roster, not specialist benefit.
- All tools-arm capture statuses/diagnostics are reported; caller-supplied unsigned Ruff results do not prove absence of other defects.
- Praise, safe refactor descriptions and unsupported style/security allegations are retained as false source observations rather than discarded. Precision includes corroborating source observations; recall deduplicates gold defects per run.
- Only complete accounted fully judged clean runs enter clean-FP denominators. Near-zero v2 clean-FP rates based on1–2 eligible runs are not a quality win.
- Concurrent schedules and uncontrolled proxy load confound latency. Native max-turns6 was not reached by the recorded v2 sessions; most stop after2–3 turns at schema, protocol, citation or token limits.
- Independent executable controls validate concrete gold triggers and tested clean invariants, not universal absence of additional defects. Potential unlabeled scenarios remain human-pending.
- No defaults changed; no architecture was promoted.

## Reproduction

Use the reconstructed recorded source or current compatible evaluator. Production `scrutare.evaluation adjudicate` exports the exact blinded packet. `adjudicate-architectures.py` checks packet, disposition, source and executable-control hashes before reproducing recorded automated decisions. Production `scrutare.evaluation score` checks frozen labels and schedule. Quality tables reproduce from the published experiment/snapshot, decisions and original pilot labels without private native sessions. The public `report-architectures.py` reconstructs paired/domain/repeat quality summaries and imports retained normalized diagnoses; published normalized diagnosis JSON is sufficient to inspect counts but cannot authenticate underlying native captures. Neither operation calls providers or reruns jobs.

Raw native sessions remain private. The pinned wheel, source patch/archive, experiment/snapshot and public report artifact hashes retain provenance. These files contain no credential values.
