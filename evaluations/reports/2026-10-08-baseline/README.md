# Initial measured baseline

Actual non-posting model runs, candidate `899748bed1bddbaf41fe919d38d31dafc87e34b0`
(merged by PR #54), original pilot captures and production scoring.

Read [the provisional report](automated-baseline-report.md). Its judgment methods
are **automated**, with zero human judgments and 34 ambiguous observations left
pending. No defaults were promoted. All 270 scheduled runs are retained.

Reproduce scoring from the repository root:

```sh
python -m scrutare.evaluation score \
  evaluations/reports/2026-10-08-baseline/experiment.json \
  --corpus evaluations/pilot \
  --decisions evaluations/reports/2026-10-08-baseline/automated-decisions.json \
  --output /tmp/scrutare-baseline-score.json
```

Use this directory's `experiment.json`, `snapshot.json` and
`automated-decisions.json` with `evaluations/pilot` through the documented score
CLI. The snapshot pins original capture and label hashes. Absolute run/runtime
paths are historical provenance, not required credentials or portable paths.
The private native session captures are retained locally and are not bundled;
these published JSON files retain actual result observations, run failures and
usage rather than mocked outputs. Full native transport audit requires those
private captures.

The original heldout cases were included in the initial baseline. This report
cannot serve as unseen validation for changes tuned against its outcomes.
