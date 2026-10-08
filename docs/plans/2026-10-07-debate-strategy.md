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
- [x] 1. Typed chair decisions and shared-pool prompts: failing tests reject inventions and upgrades.
- [x] 2. Round orchestration and budgets: failing tests prove chair accounting, acceptance and exhaustion.
- [x] 3. Dispatch and replay integration: failing tests prove service strategy support and byte-identical replay.
- [x] 4. Installed nare proof and independent review: installed wheel tests prove blocking selection, advisory downgrade, deadlock escalation and byte-identical replay.

## Acceptance gates

Run the complete locked test, Docker, lint, type, wheel and CLI-smoke table on Python 3.10 and 3.14 before pushing. Python 3.10 passed at e268140; this final documentation-only checkpoint precedes the Python 3.14 run.
