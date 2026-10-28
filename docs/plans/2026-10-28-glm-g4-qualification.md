# GLM protocol correction qualification g4

Issue #99. Refs #51 and #98. Maintainer instruction dated 2026-10-28.

## Scope
In: one released-wheel activation/provenance smoke, one prospectively frozen g4,
all retained outcomes, reproducible publication with gate table versus g3/c1/s2,
correction types/outcomes and usage, spend/fallback verification and concurrent load.
Out: re-test without new explicit approval, holdout, defaults/promotion, App,
infrastructure or deployment changes. Publish even when the gate fails; leave #51 open.

## Assumptions
- g3 is the frozen profile: all24 criteria and pass rule stay byte-identical.
- Source/released wheel and protocol correction are the only behavior changes.
- line_counts and citation correction stay on; protocol correction takes precedence.
- No idle window is required under the maintainer's2026-10-25 and2026-10-28 instructions.
- Continue in the existing `99-glm-g4` report worktree after Part1 merged.
- The maintainer waived only smoke completion on 2026-10-28; post the amendment before launch.
  Keep the failed timeout and its in-flight usage visible; preserve every frozen file.
- Frozen scripts/files and all published predecessor packages remain immutable.

## Tasks
- [x] 1. Prepare actual released wheel/corpus/config/gate and adapted scripts; assert g3 identity.
- [x] 2. Freeze hashes and post #51 BEFORE launch; verify one released-wheel activation smoke,
      provenance and Spark spend rows with no fallbacks. Smoke completion waived by the
      maintainer in the pre-launch #51 amendment; no second smoke.
- [x] 3. Launch scrutare-glm-g4.service once, record preceding-hour/concurrent/final traffic,
      preserve every attempt and mechanical qualification; run no re-test.
- [x] 4. Export redacted public package with original hashes and independent offline reproducer.
      TDD: mutation tests reject changed freeze/profile/session/correction/spend evidence;
      corrected request costs and multiple defect types remain accounted once per session.
- [ ] 5. Independent report review; full Python3.10/3.14 native Docker preflight under systemd,
      clean/justified quality guard, exact-head green CI, squash merge/issue closure verification.
- [ ] 6. Post result on #51, verify it remains open, verify release, remove temporary token.

## Review focus
Check freeze posted before evaluator launch, no idle-window waiver invented after launch,
all36 cell identities/first-attempt gate, correction type incidence versus defect occurrences,
all native streams and cumulative costs, zero paid/fallback requests, trailing-slash Spark
normalization, concurrent load observation limits, sanitized personal aliases/emails, no
historical/frozen edits, no holdout/default/deployment changes, exact-head preflight/CI.

## Measured result

g4 exited with all 36 first attempts retained: 33 complete, 2 failed and 1 invalid.
Gate items: 33/36 complete/accounted, 6/6 aggregate and 4/6 assessed. Two requests
hit the 20,000 output cap; one assessor exceeded its 50,000 grant by 8,769 tokens.
No corrections, transient reruns or re-test. All 529 evaluator requests exactly
reconcile with 2,514,190 tokens; zero paid calls/fallbacks, US$0 external.
Twelve load samples, zero read errors. The report records the pre-launch smoke
completion waiver and preserves every frozen file. Offline reproduction, 27 report
tests and report Ruff pass. Shipping checks/receipts are recorded in the PR and
private ship-issue checkpoint after this document is committed.
