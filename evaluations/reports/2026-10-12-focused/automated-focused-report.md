# Provisional focused-protocol execution comparison

240 actual scheduled runs over20 development cases,13 documented defects and7 guarded clean controls. **No valid source findings, no independent-assessor invocation and no discussion round executed. Zero automated judgments and zero human judgments. Defaults unchanged.**

| Arm | Complete / failed / invalid | Detected /39 | Precision | Observed tokens | p95 seconds | Clean FP / eligible |
|---|---:|---:|---:|---:|---:|---:|
| aggregate | 1 / 59 / 0 | 0 /39 | n/a | 1,057,666 ≥ | 9.59 | None / 0 |
| assessed | 1 / 0 / 59 | 0 /39 | n/a | 1,141,705 ≥ | 11.73 | 0 / 1 |
| focused1 | 1 / 59 / 0 | 0 /39 | n/a | 1,050,785 ≥ | 9.20 | None / 0 |
| focused3 | 0 / 60 / 0 | 0 /39 | n/a | 1,098,769 ≥ | 10.52 | None / 0 |

Aggregate and assessed each have1 complete empty result; focused1 has1 and focused3 has0.178 runs are failed and59 invalid. The assessed arm’s59 engine partial/discovery_incomplete outcomes are recorded as invalid because no valid verdict exists. Production scoring preserves these eligibility classifications.

## Actual round availability

| Arm | Configured bound | Applicable traces available | Actual completed rounds | Assessor invocations |
|---|---:|---:|---:|---:|
| aggregate | disabled (default3) | not applicable | not applicable | 0 |
| assessed | disabled (default3) | not applicable | not applicable | 0 |
| focused1 | 1 | 60 | 0 in all60 runs | 0 |
| focused3 | 3 | 60 | 0 in all60 runs | 0 |

The one-round/multiple-round limits were never exercised. All120 focused debate traces are available:119 stop at incomplete discovery and1 is undisputed with an empty candidate set. Round-level new evidence, disposition changes, round tokens and round latency are therefore unavailable—not zero-cost completed discussions. Neither a quality winner nor a benefit from extra rounds can be inferred.

## Concrete reliability evidence

- Native/session stop counts: {"budget:done:end_turn": 18, "budget:error:budget": 24, "citation_validation:done:end_turn": 35, "done:done:end_turn": 284, "protocol:done:end_turn": 221, "provider:error:schema_violation": 378}
- Checked-citation refusal counts: {"content_hash_mismatch": 31, "line_range_outside_capture": 3, "path_not_captured": 1}
- Fixed protocol claim diagnostics: {"FindingError:citation: expected complete revision and content hash": 225}

These are mechanical/protocol diagnostics under the fixed rail. They do not establish that claims were semantically false, that the provider was unavailable, or that the implementation has an identified defect. Detailed JSON preserves actual quotas, rosters, prompt hashes, turn counts, malformed-hash shapes and every noncomplete outcome.

## Limits

- The fixed gpt-4.1-nano alias, shared40000-token ceiling,max6turns,120s timeout and concurrency3 match all four arms. Backend dated revision remains unverified.
- Twenty original development cases contain13 documented defects and7 guarded clean controls; repeats give39 opportunities per arm, not39 independent defects.
- All240 records and failed/invalid outcomes remain. No valid source observations survive, so automated judgments and pending human judgments are both0. This is not evidence that reviewed changes were clean.
- All120 focused traces exist but contain0 actual discussion rounds.119 stop at discovery_incomplete;1 is undisputed with no findings. The configured1-vs3 round bound was never exercised.
- No native independent-assessor invocation occurred in any arm. Reserved quotas and empty no-candidate assessment artifacts do not demonstrate a model assessment.
- Failure mechanisms are normalized from retained private sessions: schema violations, malformed revision/hash protocol, checked-citation mismatch and token budgets. Native error/schema_violation does not imply service unavailable.
- Every v2 session remains below the max6turns bound; most stop after2–3 turns. No timeout or turn-limit mechanism is asserted without a recorded stop.
- Token totals with incomplete accounting are lower bounds. Missing valid verdicts count against recall and never count as clean approval.
- No semantic winner, benefit from more discussion, or supported-minority retention quality can be measured when neither assessment nor discussion executed.
- Quality metrics reproduce from public experiment/snapshot/decisions/labels; normalized diagnostics can be inspected without native sessions but are unsigned retained summaries, not authenticated proofs of model behavior.
- Original executable controls validate source triggers and specific clean invariants, not universal absence of other defects. No human judgments are invented.
- This used only the original development corpus. The fresh40-case holdout remains locally model-unseen; no holdout quality evidence or default promotion is claimed.
- Concurrent proxy load confounds latency. These data characterize reliability of this candidate/rail/schedule, not all multi-agent review architectures.

## Public reproduction

Run the frozen candidate evaluator’s production score CLI against experiment.json, the original pilot corpus and automated-decisions.json. Then run `python report-focused.py --experiment REPORT_DIRECTORY --corpus PILOT_DIRECTORY` using the pinned evaluator package. The script reads normalized-diagnostics.json and never opens native run directories or .agents state. Quality/domain/repeat/paired metrics reproduce independently from public records; diagnosis summaries are imported as retained unsigned evidence. Original private sessions remain retained for audit.
