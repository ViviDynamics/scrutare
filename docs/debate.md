# Debate reviews

Set `strategy: debate` to run an independent perspective wave, verify its anchors
(including the existing single correction opportunity), then discuss the verified
pool before a senior developer chair selects the final observations.

Each round runs a fresh read-only nare position session per configured persona.
Every perspective sees the complete verified pool. The chair receives that pool
and the persona positions, selects findings, dedupes disputes, and may downgrade
nitpicks to a configured advisory category. Code rejects invented anchors, changed
problem or reason text, category upgrades, and model-declared verdict fields.
Original persona attribution survives selection and downgrades; original evidence
and decisions remain in session captures and `debate.json`.

The chair uses the `senior-dev` model override when present, otherwise the default
rail, and a separate `debate-chair` ledger identity. A configured persona with that
name causes a distinct internal chair identity to be chosen. Its quota is governed
by `budgets.per_persona_tokens`; ordered quotas for all perspectives **and the
chair** share `budgets.review_max_tokens`. Initial review, correction, discussion,
and chair attempts spend against that one live ledger. Actual nare reported usage,
after-turn overshoot, and incomplete accounting retain the same M1 semantics.

A valid chair decision explicitly marks arbitration converged or unresolved.
`rounds.max` bounds completed discussion and chair rounds. Converged selections
feed the existing anchor-verified dedupe and code-derived verdict. At the bound,
an unresolved chair decision produces the existing `escalated` verdict over the
unresolved verified pool, posts a comment, and requests configured human reviewers.
A denied invocation, failed session, invalid decision, or unknown accounting before
the bound fails with artifacts and no verdict; it cannot fabricate completed rounds
or approval. A valid partial decision can still converge or exhaust the real bound.

`scrutare replay <run>` reconstructs the verdict from the final selected findings
and captured policy, including validated recorded exhaustion when present. As with
other runs, local unsigned evidence proves recorded consistency, not authentic
model judgment or remote delivery.

## Focused evidence discussion (opt-in)

For structured reviews, enable the independent assessor and focused discussion:

```yaml
strategy: debate
rounds: {max: 2}
context: {enabled: true}
findings:
  evidence: v2
  assessment: {enabled: true, tokens: 2000}
  discussion: {enabled: true}
```

This mode discusses only unresolved assertions, supported assertions with
counterevidence, and conservatively equivalent assertions with conflicting
assessments. Undisputed supported findings bypass discussion, retaining every
original source and blocking category. Claim grouping compares the normalized
assertion, cause, trigger, preconditions, expected/observed behavior and impact;
different causes or triggers at the same anchor remain separate. The quoted round
bundle contains only disputed claims and assessments. The read-only root still
contains the shared captured context so reviewers can inspect relevant code.

Perspectives propose retain/refute/unresolved with immutable candidate IDs,
reasons and captured citations. Empty-citation opposition is recorded with a
`missing_counterevidence` refusal. No perspective or chair can rewrite allegations,
downgrade categories or remove a supported minority claim by voting. Only another
independent assessment can change a disposition, using the original candidate and
quoted peer evidence. Checked references prove captured identity and line bounds;
model-based support or refutation is not formal semantic proof.

The original assessor allocation is reserved before discovery and shared by its
initial assessment and all reassessments. Perspective rounds spend their existing
quotas. There is no extra ledger, chair allocation or token ceiling. Each completed
round records proposals, refused transitions, independently changed dispositions,
new citation bundles (possibly empty), actual token deltas and elapsed time.
`assessment.json` preserves initial dispositions; numbered
`assessment-round-NNNN.json` files preserve each reassessment. `debate.json`
retains bypassed IDs, all rounds and the final independent dispositions, including
refuted claims excluded from the verdict.

With `findings.discussion.tokens`, positions and reassessments instead draw on a
separate discussion allowance reserved before discovery; the ledger snapshot
records it under `phases` and `configured.phase_allowances`
([configuration](config.md#phase-allowances-and-overrun-fallback-opt-in-unmeasured)).

Persistent disputes reach the configured bound and escalate for human review;
unresolved blocking claims remain visible and cannot approve. Missing or partial
sessions, invalid references, denied quotas and incomplete accounting withhold a
verdict instead of claiming a completed deadlock. With the opt-in
`findings.discussion.overrun: fallback`, a round whose positions or reassessment
end past their grant with complete accounting instead keeps the assessment from
before that round: `debate.json` records `reason: discussion_overrun_fallback`
and a `degraded` record, the verdict is never an approval, and replay validates
the overrun from the retained outcomes. Replay validates recorded
candidate identity, citations, independent transitions, original assessor quota
and the completed bound; it never reruns the models or certifies semantic truth.

This mode leaves defaults unchanged. Equal-budget precision/recall comparisons
against independent aggregation, assessment without discussion, and one/multiple
rounds remain evaluation work tracked by #51; this implementation makes no quality
promotion claim and references #49 without closing its measured acceptance.
