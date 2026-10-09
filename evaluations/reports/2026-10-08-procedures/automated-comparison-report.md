# Provisional automated inspection procedure comparison

Actual model evidence: 180 scheduled runs across 20 development cases, 13 documented defects and 7 clean controls; all records retained. 667 observations received explicit automated judgments and 32 remain pending actual human review. **Zero human judgments. Defaults unchanged.**

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current4 | 59 / 0 / 1 | 108 / 123 | 46.8% | 34 / 39 | 87.2% | 12 | 363,770 ≥ | 10,699 | 7.51 | 21 / 21 | 32.0% |
| revised4 | 59 / 0 / 1 | 96 / 103 | 48.2% | 32 / 39 | 82.1% | 6 | 385,231 ≥ | 12,038 | 6.67 | 20 / 20 | 32.2% |
| revised5 | 59 / 0 / 1 | 114 / 123 | 48.1% | 33 / 39 | 84.6% | 14 | 481,889 ≥ | 14,603 | 5.88 | 21 / 21 | 34.2% |

The three arms use the same 40,000 total token ceiling: current4 has baseline prompts and 10,000 per reviewer; revised4 uses v1 prompts with 10,000 each; revised5 adds testing-verification with 8,000 each. The observed revised arms do not demonstrate the frozen five-point quality improvement without other losses. Pending judgments, failures, accounting gaps and lack of unseen validation make promotion inconclusive regardless.

## Failure stages

{"failed:initial": 3}

Stage labels identify where the engine stopped; they are not assertions that all failures have an identical provider/protocol cause.

## Repeat measurements


### Repeat 1

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current4 | 20 / 0 / 0 | 39 / 40 | 49.4% | 12 / 13 | 92.3% | 4 | 120,003 | 10,000 | 6.44 | 7 / 7 | 34.2% |
| revised4 | 19 / 0 / 1 | 30 / 30 | 50.0% | 10 / 13 | 76.9% | 1 | 129,648 ≥ | 12,965 | 7.07 | 6 / 6 | 33.3% |
| revised5 | 20 / 0 / 0 | 33 / 43 | 43.4% | 11 / 13 | 84.6% | 6 | 160,197 | 14,563 | 5.74 | 7 / 7 | 28.9% |

### Repeat 2

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current4 | 20 / 0 / 0 | 35 / 41 | 46.1% | 11 / 13 | 84.6% | 4 | 123,576 | 11,234 | 7.51 | 7 / 7 | 31.6% |
| revised4 | 20 / 0 / 0 | 34 / 37 | 47.9% | 11 / 13 | 84.6% | 1 | 130,002 | 11,818 | 6.05 | 7 / 7 | 32.4% |
| revised5 | 20 / 0 / 0 | 38 / 41 | 48.1% | 12 / 13 | 92.3% | 4 | 160,145 | 13,345 | 5.69 | 7 / 7 | 32.9% |

### Repeat 3

| Variant | Complete / partial / failed | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds | Clean FP / eligible | Duplicate rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| current4 | 19 / 0 / 1 | 34 / 42 | 44.7% | 11 / 13 | 84.6% | 4 | 120,191 ≥ | 10,926 | 6.07 | 7 / 7 | 30.3% |
| revised4 | 20 / 0 / 0 | 32 / 36 | 47.1% | 11 / 13 | 84.6% | 4 | 125,581 | 11,416 | 6.67 | 7 / 7 | 30.9% |
| revised5 | 19 / 0 / 1 | 43 / 39 | 52.4% | 10 / 13 | 76.9% | 4 | 161,547 ≥ | 16,155 | 6.19 | 7 / 7 | 40.2% |

## Unique detections

Distinct defect detections reported by exactly one persona within a run; observations corroborated by multiple personas do not count as unique.

- current4: {"devops": 0, "junior-dev": 0, "security": 2, "senior-dev": 2}
- revised4: {"devops": 1, "junior-dev": 1, "security": 3, "senior-dev": 0}
- revised5: {"devops": 1, "junior-dev": 2, "security": 1, "senior-dev": 1, "testing-verification": 0}

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
