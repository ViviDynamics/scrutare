# Provisional automated repository context comparison

Actual model evidence: 180 scheduled runs across 20 development cases, 13 documented defects and 7 clean controls; all records retained. 351 observations received explicit automated judgments and 31 remain pending actual human review. **Zero human judgments. Defaults unchanged.**

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | 15 / 10 / 35 | 31 / 34 | 47.7% | 11 / 39 | 28.2% | 10 | 1,502,739 ≥ | 136,613 | 31.63 | 6 / 6 | 30.8% |
| panel | 60 / 0 / 0 | 112 / 117 | 48.9% | 32 / 39 | 82.1% | 18 | 481,360 | 15,042 | 7.12 | 21 / 21 | 34.9% |
| senior | 60 / 0 / 0 | 24 / 33 | 42.1% | 23 / 39 | 59.0% | 3 | 100,692 | 4,378 | 5.81 | 17 / 21 | 1.8% |

## Paired initial baseline, exact same development cases

The following scores restrict the original baseline to the identical 20 development case IDs and their original source observations/decisions. Each variant has 39 possible defects; the original all 30-case aggregate with 60 possible defects is deliberately not used. Exact original finding IDs bind reused decisions to original records.

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | 17 / 4 / 39 | 25 / 40 | 38.5% | 10 / 39 | 25.6% | 3 | 1,088,667 ≥ | 108,867 | 30.81 | 9 / 9 | 23.1% |
| panel | 60 / 0 / 0 | 128 / 102 | 55.7% | 37 / 39 | 94.9% | 15 | 364,419 | 9,849 | 6.66 | 21 / 21 | 39.6% |
| senior | 60 / 0 / 0 | 33 / 24 | 57.9% | 31 / 39 | 79.5% | 2 | 88,600 | 2,858 | 4.83 | 20 / 21 | 3.5% |

Compared with that paired baseline, these context results do not establish a reliable accuracy gain. The tests-consumer-fixture case still produced generic status-return allegations without identifying the stale ready assertion; these did not satisfy its strict gold criterion. Context availability alone does not establish that a reviewer used its evidence.

## Failure stages

{"failed:chair": 11, "failed:chair_budget": 4, "failed:perspective_budget": 6, "failed:perspectives": 14, "partial:converged": 2, "partial:deadlock": 8}

Stage labels identify where the engine stopped; they are not assertions that all failures have an identical provider/protocol cause.

## Repeat measurements


### Repeat 1

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | 5 / 4 / 11 | 17 / 12 | 58.6% | 6 / 13 | 46.2% | 5 | 548,236 ≥ | 91,373 | 31.63 | 2 / 2 | 37.9% |
| panel | 20 / 0 / 0 | 37 / 37 | 50.0% | 11 / 13 | 84.6% | 7 | 157,696 | 14,336 | 6.50 | 7 / 7 | 35.1% |
| senior | 20 / 0 / 0 | 6 / 12 | 33.3% | 6 / 13 | 46.2% | 1 | 32,871 | 5,478 | 5.43 | 5 / 7 | 0.0% |

### Repeat 2

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | 4 / 3 / 13 | 6 / 9 | 40.0% | 2 / 13 | 15.4% | 2 | 514,492 ≥ | 257,246 | 31.54 | 2 / 2 | 26.7% |
| panel | 20 / 0 / 0 | 40 / 38 | 51.3% | 10 / 13 | 76.9% | 6 | 155,732 | 15,573 | 7.24 | 7 / 7 | 38.5% |
| senior | 20 / 0 / 0 | 9 / 9 | 50.0% | 9 / 13 | 69.2% | 2 | 33,654 | 3,739 | 5.81 | 6 / 7 | 0.0% |

### Repeat 3

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | 6 / 3 / 11 | 8 / 13 | 38.1% | 3 / 13 | 23.1% | 3 | 440,011 ≥ | 146,670 | 25.66 | 2 / 2 | 23.8% |
| panel | 20 / 0 / 0 | 35 / 42 | 45.5% | 11 / 13 | 84.6% | 5 | 167,932 | 15,267 | 6.95 | 7 / 7 | 31.2% |
| senior | 20 / 0 / 0 | 9 / 12 | 42.9% | 8 / 13 | 61.5% | 0 | 34,167 | 4,271 | 5.84 | 6 / 7 | 4.8% |

## Unique detections

Distinct defect detections reported by exactly one persona within a run; observations corroborated by multiple personas do not count as unique.

- debate: {"devops": 0, "junior-dev": 0, "security": 1, "senior-dev": 0}
- panel: {"devops": 0, "junior-dev": 2, "security": 0, "senior-dev": 0}
- senior: {"senior-dev": 23}

## Limits

- Independent Codex matching is automated; genuinely ambiguous observations remain unscored pending actual human review. Zero human judgments are claimed.
- One aliased gpt-4.1-nano model through LiteLLM; the underlying dated model revision is not established.
- Twenty original development cases with thirteen documented defects and seven clean controls. Three stochastic repeats produce 39 defect opportunities per variant, not 39 independent defects.
- All original held-out cases were seen during the initial baseline. These development comparisons provide no unseen held-out validation and cannot justify default promotion.
- Executable gold controls and frozen source hashes validate recorded triggers and tested clean invariants, not universal absence of further defects.
- Failures remain in recall denominators; incomplete accounting makes observed tokens and tokens per detected defect lower bounds. Partial output is not a clean approval.
- Precision counts emitted source observations including corroborations. Praise, safe refactor descriptions and unsupported allegations are retained as false observations rather than discarded.
- Clean false-positive rate includes only complete accounted fully judged clean runs. Latency uses nearest-rank p95 across all actual scheduled runs including failures.
- The context and procedure jobs overlapped in wall-clock time with uncontrolled provider proxy load. Latency and reliability differences cannot be attributed solely to architecture.
- Published quality measurements remain provisional; no defaults or architecture promotion follows from these reports.

## Reproduction

Use this job’s locked evaluator venv. Export the blinded packet with production `scrutare.evaluation adjudicate`, replay recorded semantic decisions with `adjudicate-comparison.py`, then production `scrutare.evaluation score` against the job’s frozen pilot and `automated-decisions.json`. Run `report-comparison.py` for aggregate/repeat summaries. The recorded decision script checks the exact packet hash and independently verified original source hashes; it does not call a model.

For context, `paired-baseline-experiment.json` and `paired-baseline-decisions.json` retain the exact original baseline records restricted to the same 20 development cases. The report script verifies matching case/label hashes and identical expected schedules before scoring. Raw experiments and snapshots are unchanged; all report artifact hashes are recorded in JSON.
