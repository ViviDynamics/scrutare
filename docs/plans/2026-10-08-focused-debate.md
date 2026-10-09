# Focused evidence discussion

Issue #49; dependency base pinned to #47 commit 794fe112.

## Scope
In: opt-in assessed v2 debate over genuinely disputed claims, immutable ID-based
proposals, shared quota independent reassessment, retained minority sources,
conservative trigger/cause grouping, traceable bounded rounds and read-only replay.
Out: confidence voting, automatic quality promotion and paid experiments. Equal
budget quality comparisons belong to the evaluation/report backlog.

## Decisions
- findings.discussion.enabled requires strategy debate and enabled v2 assessment.
- Supported claims without counterevidence bypass discussion. Unresolved claims,
  supported claims with counterevidence, and equivalent claims with conflicting
  independent assessments are disputed. Different triggers/causes stay separate.
- Each perspective proposes retain/refute/unresolved for immutable candidate IDs;
  empty-citation opposition is retained as an invalid transition, never a vote.
  Category downgrades and allegation rewrites are not authorized.
- Only a new independent assessor batch may refute a supported claim. Every batch
  uses the remaining original assessor quota on the same review ledger.
- Prompt bundles contain disputed claims and their supporting/counter references;
  the immutable read root remains the shared captured context, allowing inspection
  of relevant surrounding code without claiming a narrower security boundary.
- Record each round's new references, state transitions, token deltas and monotonic
  latency. Missing/partial execution withholds verdicts; unresolved blocking claims
  escalate after the explicit configured discussion bound.
- Replay checks policy, immutable IDs, citations, quota provenance and transitions;
  it does not establish semantic truth or authenticity.

## Tasks
- [x] 1. Opt-in configuration, conservative source grouping and closed native proposal
  parsing: red configuration/model/descriptor tests, then validated references and
  refused unsupported moves. Default configuration wire stays unchanged.
- [x] 2. Bounded focused execution and read-only replay on the original live ledger:
  red bypass/resolution/bound/native consistency/prior artifact regressions, then
  immutable transition history and safe budget/technical failure behavior.
- [x] 3. Installed wheel/nare integration proof and documentation: actual external
  runtime plus complete legacy/context/assessment matrix; no default promotion or
  paid quality experiments. #51 owns measured acceptance, so #49 remains open.

## Review focus
Undisputed bypass, supported security minority facing uncited opposition,
conflicting evidence and independent refutation, equal anchors/different triggers,
incomplete execution/accounting, quota exhaustion, repeated evidence and bounds,
replay tampering and cancellation evidence. Parent owns fresh review and final
Python 3.10/3.14/native Docker gates; no push occurs before those gates.
