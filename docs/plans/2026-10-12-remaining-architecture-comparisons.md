# Remaining architecture comparisons

Issue #51

## Scope
In: explicit paired production architecture arms, three repetitions at the same
review ceiling, per-case routed persona and reservation snapshots before runtime
inspection, frozen captured tool inputs, and non-posting offline verification.
Out: automatic default promotion, invented human adjudication, or new paid runs
before the coordinator reviews the complete schedule. Original held-out cases
were already observed; development measurements cannot establish unseen quality.

## Assumptions
- Existing baseline and procedures comparison modes remain compatible.
- Architecture arms disable inherited optional features before selecting components.
- Four fixed reviewers use the same model; assessment reserves 20% of total.
- Tools compare against assessed context/procedures/v2; routing compares against
  context/procedures/v2 without assessment.
- Missing static captures remain explicitly unavailable rather than clean results.
- Focused debate follows merged #49 API and compares matched assessment rails.
- Global shipping budgets carry forward: both permitted rebases used; none remain.

## Tasks
- [x] 1. Add isolated architecture arms: red tests prove flags and equal ceilings.
- [x] 2. Freeze exact case inputs, routed prompts and allocations before await:
  red tests mutate the source during runtime inspection and inspect snapshot.
- [x] 3. Add focused debate comparison once #49 is available, preserving quotas.
- [x] 4. Verify offline installed runtime, document explicit job/promotion limits.
