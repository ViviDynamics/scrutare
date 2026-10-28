# Offer one bounded correction turn for machine-detectable protocol defects

Issue #98. Refs #91, #96, #51. Maintainer brief dated 2026-10-28.

## Scope

In: default-off `findings.protocol_correction`; discovery, assessment and compatible discussion decisions; deterministic field/enum/line/candidate coverage/citation defects; exactly one tool-free resumed turn inside the session grant, timeout and ledger; unchanged authoritative validation; retained refusal/prompt/outcome and replay; scripted and installed nare 2026.10.12 proofs; one Spark-only live smoke; merge and release.

Out: holdout, defaults/promotion, deployments/infrastructure, semantic repair, provider retries, any re-test. g4 is a separate frozen qualification/report issue after release.

## Assumptions and design

A sibling setting preserves citation-only behavior and all disabled canonical artifacts and arguments. When both are enabled the broader protocol policy owns the single correction opportunity; there is never a second citation turn. The model returns the entire answer under the original schema: it can retract an invalid placeholder or an invented candidate itself; the engine never patches, clamps, drops or inserts content. Every component of an answer is inspected for deterministic defects; captured identity mismatches are exact protocol defects; corrupt/inconsistent runtime/input evidence remains non-correctable. Diagnostics use fixed validator text and quoted input identifiers, never model instructions. Original native captures remain untouched and downstream assessment/discussion read the accepted correction only after a successful outcome.

## Tasks

- [x] 1. Config and evaluator propagation. Red: boolean/v2/OpenAI restrictions, canonical off bytes and evidence-arm inheritance. Green: sibling config, omitted when off.
- [x] 2. Exhaustive deterministic defect classification and bounded runtime. Red: g3 line-zero placeholder and unknown fifth assessor ID; missing/duplicate IDs, type/enum, citation, mixed unsupported errors, budget and one-turn limits. Green: classify all fields before offering a full-answer correction; reuse native resume/accounting/timeout lifecycle without altering old paths.
- [x] 3. Replay and downstream consumers. Red: corrected assessor/discussion outputs applied, every outcome replays, tampered prompts/defects/schema/argv/usage/outcome rejected. Green: verify original and correction streams and immutable descriptors; retain provenance.
- [x] 4. Installed nare proofs and documentation. Red: installed line-zero and assessor-ID fixtures with constrained tool-free single resume, off/refused/non-correctable/replay. Green: document setting, artifacts and limits. Run one Spark live smoke and verify spend/fallbacks.
- [ ] 5. Independent review, full Python 3.10 and 3.14 preflight as systemd user units with installed nare/native Docker, quality guard, exact-head CI, merge/closure/release verification.

## Verification and review focus

Use tests/test_protocol_correction*.py and tests/test_installed_protocol_correction.py plus existing citation/assessment/discussion/replay suites. Full commands: .agents/test-commands.md (both lanes sequential, no background ampersand). Independent review must check mixed refusals, bool/int confusion, copied/saved native-output inconsistency, immutable candidate identity, late failure accounting, correction exclusivity, relocated replay and every downstream session-output consumer. Every implementation commit includes the requested Codex co-author trailer.


## Independent review response

Round1 found four Important issues. Regression tests reproduce invalid original done/budget
eligibility, cumulative capture selection for corrected assessor/discussion overruns,
replayed invocation task/pool tampering (including refuted claims), and coordinated
original/correction schema tampering. Fixes validate the original native terminal state
before offering, select the accepted native capture for fallback replay, bind tasks to
hashes and authoritative phase pools, and require canonical descriptor schemas.
One live smoke resumed the actual g3 junior-dev refusal and corrected it on Spark in one
tool-free json_schema turn (2026-10-28 12:08:53Z–12:10:25Z):7058prompt+965output,
matching one LAN spend row,0fallbacks/paid calls and US$0 external. Full preflight/review
and shipment are tracked in the ship-issue checkpoint; no second smoke is required.

Round2 found four further Important boundary cases, reproduced with six failing
regressions: strict JSON primitive identity in schema comparison; prepared-input
revalidation before a protocol offer and before launching its process; resumed
native terminal/budget semantics and partial/overshoot outcome replay; strict
correction constraint-stream validation. These are fixed with unchanged validators
and no additional model call. The two independent review rounds exhaust the planned
review budget; final verification is the regression suite and both full preflight lanes.
