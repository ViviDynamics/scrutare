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
