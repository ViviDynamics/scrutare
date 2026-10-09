# Measured context development comparison

Read [the provisional report](automated-comparison-report.md). All 180 scheduled
real model runs, including failures and partial output, are retained. Decisions
are automated; zero human judgments are claimed. Ambiguous observations remain
pending actual human review. No default promotion follows.

Reproduce aggregate scoring from the repository root:

```sh
python -m scrutare.evaluation score \
  evaluations/reports/2026-10-08-context/experiment.json \
  --corpus evaluations/pilot \
  --decisions evaluations/reports/2026-10-08-context/automated-decisions.json \
  --output /tmp/scrutare-context-score.json
```

`published-sha256.json` records hashes of the exact retained artifacts copied from
the measured job. The snapshot binds original capture and label hashes. Absolute
paths are historical local provenance, not credentials or required portable paths.
Private native session captures remain locally retained and are not bundled; full
transport audits require those captures. The report includes matching methods,
source-observation precision, defect-opportunity recall, duplicate rates, unique
detections, repeated runs, failures and accounting gaps. Token measurements with
incomplete accounting are lower bounds.

One aliased gpt-4.1-nano model and a small synthetic development corpus limit
generalization. All original heldout cases were seen in the initial baseline.
Neither comparison supplies unseen validation or completed human adjudication.
Overlapping job execution and uncontrolled proxy load limit latency comparisons.

The paired baseline files restrict the original baseline to the identical 20
development cases and 39 defect opportunities per variant. They preserve original
observations and stable-ID judgments; the original 30-case aggregate is not used.
