# Explicit inspection procedures and testing perspective

Issue #45. Approved backlog: issue #43–#51 research-informed review architecture.

## Scope

Ship opt-in `inspection.procedures: v1` with explicit procedures for the four
existing perspectives and a selectable `testing-verification` perspective.
Preserve baseline prompts, the original four default reviewers, inline two-field
persona definitions, independent initial sessions, and code-derived verdicts.
Capture effective system/task hashes and procedure provenance. Comparative live
quality measurements follow the #43 evaluator; keep the feature opt-in.

## Assumptions

- `baseline` remains the procedure default and is omitted from canonical config
  output so old captures still validate against their stored configuration.
- New fields default at the end of ReviewConfig to preserve positional callers.
- Inline overrides remain exactly caller supplied regardless of procedure profile.
- The debate chair uses the chosen senior-dev procedure, with its separate ledger.
- Procedure identification is provenance, never a model confidence or verdict.

## Tasks

- [x] 1. Test selectable testing reviewer and unchanged default roster.
- [x] 2. Test strict profile configuration, roundtrip, and baseline compatibility.
- [x] 3. Test distinct procedure resolution, exact inline preservation, and guardrails.
- [x] 4. Test production fanout/chair profile propagation and retained prompt hashes.
- [ ] 5. Document procedures and equal-budget evaluation arms; run both required lanes.
