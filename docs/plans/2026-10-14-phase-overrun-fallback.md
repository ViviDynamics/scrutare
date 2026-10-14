# Discussion rounds void the review when reviewers exhaust their reused discovery grant

Issue #74 (refs #47, #49, #51). Contract: the maintainer decision of 2026-10-14 on #74.

## Scope

In:

- An opt-in discussion allowance, `findings.discussion.tokens`, carved out of
  `budgets.review_max_tokens` on the shared ledger before discovery is split, exactly as the
  existing assessor reservation (`findings.assessment.tokens`) already is. When set, every
  discussion-round session (reviewer positions and reassessments) is admitted from that pool
  instead of reusing discovery and assessor grants, so the assessor reservation covers the
  initial assessment alone.
- An opt-in overrun policy per phase, `findings.assessment.overrun` and
  `findings.discussion.overrun` (`void`, the default, or `fallback`). With `fallback`, a phase
  whose admitted sessions end past their grant (`partial`, `budget`, accounting complete) falls
  back to the last complete stage instead of voiding the review:
  - assessor overrun keeps the unassessed anchor-validated findings (panel, debate, focused
    debate, and iterative through its panel);
  - discussion overrun (positions or reassessment) keeps the pre-round assessment.
- The fallback verdict carries a `degraded` record (phase, round, allowance, grant, actual
  usage, overrun, fallback stage). It is never an approval: blocking findings still request
  changes, unresolved potentially blocking findings still escalate under the existing rule, and
  otherwise the verdict escalates under the new rule `phase_overrun_fallback`. Status is
  `partial`.
- Replay reconstructs the fallback verdict from the retained artifacts without running models
  and refuses a fallback the captured policy or evidence does not support.
- Posting renders the degraded record; escalation recovery recognises the new rule.
- Docs: config, debate, findings/failure paths, escalation, SPEC.

Out:

- Any change to defaults or to existing configurations' artifacts (byte-identical when the new
  keys are absent).
- Fallback on anything other than a measured budget overrun: refused admission, failed or
  malformed sessions, incomplete or unknown accounting all still void the review.
- Re-reading avoidance for position sessions (third option on #74): not chosen.
- Measurement: no paid model runs; follow-up measurement is paused on #43/#51.
- The reusable Action's success validator, which already accepts only one escalation rule.

## Assumptions

- The assessor allowance the decision names already exists as `findings.assessment.tokens`;
  it is reserved on the ledger before discovery. The new separation is that, with a discussion
  allowance, reassessments no longer draw on it.
- Concurrent position sessions split the discussion pool's unreserved remainder evenly
  (remainder to the earlier persona); a reassessment receives the whole remainder.
- Discussion-phase usage is accounted to the phase, not to the persona's discovery allocation;
  the ledger total stays the exact sum of every session.
- Overrun is measured per session as `overshoot_tokens`; the record sums them for the step.

## Tasks

- [ ] 1. Config keys parse, validate and are omitted from canonical config when default:
      failing config tests.
- [ ] 2. `Degradation` on `Verdict`: never approve, rule precedence, canonical bytes unchanged
      without it, saved-verdict parsing: failing verdict and replay-artifact tests.
- [ ] 3. Ledger phase allowances: carved before the ordinary split, atomic even split for
      concurrent sessions, phase usage separate, snapshot unchanged without phases: failing
      budget tests.
- [ ] 4. Assessor overrun fallback in panel with replay validation and tamper refusal:
      failing panel tests from the voided `tools` cell shape.
- [ ] 5. Assessor overrun fallback in debate and focused debate: failing tests.
- [ ] 6. Discussion position overrun fallback with replay: failing tests from the three failed
      focused cells (four reviewers, 17,500 grants, about 11,000 discovery use).
- [ ] 7. Reassessment overrun fallback with replay: failing test.
- [ ] 8. Discussion allowance admission and replay: failing tests.
- [ ] 9. Posting payload and escalation replay for degraded verdicts: failing tests.
- [ ] 10. Evaluation snapshot mirrors the phase allowance: failing test.
- [ ] 11. Installed native nare proofs for both fallbacks: failing installed tests.
- [ ] 12. Byte-identical proof against `origin/main` for default configurations; docs.
