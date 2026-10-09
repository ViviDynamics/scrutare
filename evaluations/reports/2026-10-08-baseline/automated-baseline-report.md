# Provisional automated baseline adjudication

This records actual model runs with independent **automated** matching against frozen original source and executable gold controls. It contains **zero human judgments**. Thirty-four ambiguous observations remain pending human adjudication. Defaults must remain unchanged.

| Variant | Complete / partial / failed | TP / FP observations | Precision | Defects / possible | Recall | Pending | Observed tokens | Tokens / defect | p95 seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| debate | 26 / 6 / 58 | 35 / 69 | 33.7% | 13 / 60 | 21.7% | 5 | 1,679,857 (lower bound) | 129,220 | 31.04 |
| panel | 90 / 0 / 0 | 164 / 179 | 47.8% | 50 / 60 | 83.3% | 24 | 551,342 | 11,027 | 6.71 |
| senior | 90 / 0 / 0 | 45 / 39 | 53.6% | 42 / 60 | 70.0% | 5 | 138,199 | 3,290 | 5.07 |

The protocol retained all 270 scheduled runs: 206 complete, six partial, 58 failed, zero missing. All incomplete runs are debate: 38 perspective failures, 19 chair failures, one initial failure, and six deadlocks. Engine artifacts include protocol, budget and provider session failures; the top-level labels are stages, not proof that every failure has the same cause.

Clean-run false-positive rate is 93.3% for senior (28/30 eligible runs), 100% for panel (30/30), and 100% for debate (13/13). Most clean emissions describe harmless refactors, praise improvements, speculate about undocumented constraints, or invent missing imports despite full source definitions. The strict scorer counts these emissions as false positives.

Panel detects more labeled defects than senior on this pilot but emits more false observations and costs more tokens. Debate completion and accounting failures dominate its measured result. These findings support building better evidence and protocol handling; they do not establish a generally superior architecture.

## Repeat measurements

| Repeat | Variant | TP / FP | Precision | Detected / possible | Recall | Pending | Observed tokens | p95 seconds |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | debate | 11 / 28 | 28.2% | 4 / 20 | 20.0% | 2 | 575,168 | 29.75 |
| 1 | panel | 51 / 60 | 45.9% | 17 / 20 | 85.0% | 11 | 181,946 | 6.95 |
| 1 | senior | 13 / 12 | 52.0% | 13 / 20 | 65.0% | 3 | 47,598 | 6.30 |
| 2 | debate | 12 / 17 | 41.4% | 4 / 20 | 20.0% | 3 | 574,996 | 31.78 |
| 2 | panel | 59 / 54 | 52.2% | 16 / 20 | 80.0% | 7 | 184,344 | 6.43 |
| 2 | senior | 15 / 15 | 50.0% | 14 / 20 | 70.0% | 1 | 44,664 | 5.07 |
| 3 | debate | 12 / 24 | 33.3% | 5 / 20 | 25.0% | 0 | 529,693 | 29.26 |
| 3 | panel | 54 / 65 | 45.4% | 17 / 20 | 85.0% | 6 | 185,052 | 6.71 |
| 3 | senior | 17 / 12 | 58.6% | 15 / 20 | 75.0% | 1 | 45,937 | 5.04 |

## Limits

- Independent Codex judgment is automated, not human ground truth. Thirty-four ambiguous source observations remain unscored pending human adjudication.
- One provider alias gpt-4.1-nano through LiteLLM; no dated underlying model revision is established. This is not cross-model evidence.
- Thirty small original synthetic Python cases, twenty documented defects and ten clean controls; three stochastic repeats are not ninety independent real repositories.
- All development and holdout cases were included in this initial baseline. These results are not a fresh held-out validation after architecture selection. Do not tune prompts against the holdout findings.
- Executable base/head controls verify author-defined triggers and clean invariants; they do not prove universal absence of additional defects.
- Precision counts emitted source observations, including corroborations; recall counts distinct labeled defects per case/repeat with failed runs remaining in the sixty-defect denominator per variant.
- Debate has fifty-eight failed and six partial runs. Fifty-eight runs lack complete accounting, so observed tokens and tokens per detected defect are lower bounds.
- Clean false-positive rate only includes complete accounted fully judged clean runs. A run with an emitted praise or nondefect observation is scored as a false positive rather than silently discarded.
- The latency percentile uses nearest rank over all scheduled actual runs including failures; failed debate runs are not filtered out.
- No default architecture or persona roster promotion is justified by this provisional pilot.

## Reproduction

Use the locked fixed-venv installation of candidate 899748b. First export `blinded-packet.json` with the production `scrutare.evaluation adjudicate` command; run `adjudicate-baseline.py` to reproduce recorded automated decisions; run production `scrutare.evaluation score` with the frozen `pilot` corpus and `automated-decisions.json`; run `report-baseline.py` for aggregate and repeat summaries. The decision script replays recorded semantic judgments without model calls.

Exact content hashes are in `automated-baseline-report.json`. Raw experiment and snapshot files are unchanged. `pending-human-adjudication.json` holds the blinded claims requiring actual human review. Automated ambiguous records are deliberately omitted from production scorer decisions; they are never relabeled as human.
