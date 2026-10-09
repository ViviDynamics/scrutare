# Architecture comparisons and measured promotion

Issue #51; initial baseline also supplies measured evidence for #43.

## Scope
In: actual frozen non-posting comparisons, reproducible scoring artifacts, opt-in
architecture variants, explicit cost/latency and uncertainty reporting, and a
code-enforced promotion gate.
Out: fabricated human adjudication, silent default changes and deployment #18.

## Assumptions
- The completed 270-run pilot baseline is provisional: 34 ambiguous observations
  await real human judgment; one aliased model and small synthetic corpus are limited.
- Baseline covered the original holdout. Any architecture tuned after seeing its
  outcomes requires a new unseen held-out corpus before promotion.
- Freeze each comparison rail, settings, schedule and equal total token ceilings
  before the first job; never discard failed or incompletely accounted cells.
- Proposed promotion requires recall +5 percentage points with precision loss
  at most 2 points, or precision +5 with recall loss at most 2; no new critical
  security miss; tokens and p95 latency within 20% of baseline. Human-reviewed
  ambiguous matches and adequate uncertainty evidence remain required.
- A gate that is inconclusive leaves current defaults unchanged.

## Tasks
- [x] 1. Publish actual baseline evidence and independent automated decisions;
  reproduction uses production scorer and frozen capture/label hashes.
- [x] 2. Add explicit procedure comparisons: current4, revised4 and revised5
  under equal total ceilings; schedule/profile snapshot regression tests.
- [ ] 3. Extend opt-in comparisons for context, evidence assessment, tool evidence,
  focused debate and routing after their dependent implementations ship.
- [ ] 4. Run development comparisons; publish all usage, failures, latency and
  provisional precision/recall without claiming incomplete accuracy.
- [ ] 5. Freeze and independently verify new held-out cases; enforce promotion
  policy in tests and publish pass/fail/inconclusive evidence.

## Implementation evidence
- Added opt-in current4/revised4/revised5 comparisons; defaults unchanged.
- TDD: 16 initial failing feature tests, then 47 focused checks passed; CLI gate and
  offline/unequal-denominator guards each observed red before implementation.
- Promotion gate implementation is ready; actual development comparisons, unseen
  holdout, architecture 44–50 comparisons, and independent human review remain open.
