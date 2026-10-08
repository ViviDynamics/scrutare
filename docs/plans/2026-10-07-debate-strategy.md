# Debate strategy (M3)

Issue #17

## Scope
In: real perspective and chair sessions; verified selection and advisory downgrade; shared token accounting; bounded deadlock escalation and replay.
Out: live provider spending tests; App integration owned by #19.

## Assumptions
- Each completed debate round contains a shared-pool perspective wave and a senior developer chair decision.
- Chair uses a distinct ledger identity, default model rail, and the same per-persona quota.
- A chair may select original findings and downgrade to configured advisory categories; it cannot invent anchors, problems, reasons, categories or a verdict.
- Invalid execution or unavailable decisions fail safely. A valid explicit unresolved decision consumes a round; only a completed round bound escalates.

## Tasks
- [ ] 1. Typed chair decisions and shared-pool prompts: failing tests reject inventions and upgrades.
- [ ] 2. Round orchestration and budgets: failing tests prove chair accounting, acceptance and exhaustion.
- [ ] 3. Dispatch and replay integration: failing tests prove service strategy support and byte-identical replay.
- [ ] 4. Installed nare proof, full Python 3.10/3.14 gates and independent review.
