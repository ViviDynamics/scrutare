# Iterative strategy

Issue #15

## Scope
In: real budgeted sessions against new or contested patch hunks, durable per-PR findings dispositions, and a round bound shared across pushes.
Out: distributed remote history storage; callers retain the runs directory across invocations.

## Assumptions
- Repository plus PR number identifies one history; configuration changes fail closed rather than reset the bound.
- Each fresh push with reviewable work consumes one round, reserved before execution. Same-head retries reuse terminal history.
- A missing finding on changed evidence is fixed; a missing finding on unchanged contested evidence is withdrawn; surviving findings are upheld.
- Captured inline discussion contests the matching finding. Discussion remains outside the model read root.

## Tasks
- [x] 1. Persist validated pool snapshots and serialize concurrent runs: tests prove corrupt/link state refusal and round reservations survive failure.
- [x] 2. Project only new/contested hunks into real panel execution: tests inspect runtime descriptors across pushes.
- [x] 3. Reconcile dispositions, retain unchanged findings, and escalate shared bound: multi-push tests prove fixed/upheld/withdrawn and no extra sessions.
- [x] 4. Prove installed CLI strategy execution and run full two-version preflight.
