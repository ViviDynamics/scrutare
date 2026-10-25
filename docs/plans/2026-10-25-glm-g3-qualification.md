# Re-qualify spark/glm-5.3-flash with line counts and citation correction

Issue #96; refs #51, #91, #85.

## Scope

Publish one new, prospectively frozen 36-cell qualification, schedule g3, using the latest released wheel and c1's profile plus line counts and citation correction. Include reproducible comparisons to c1 and s2, correction outcomes, causes, spend verification and concurrent load. No holdout, promotion, defaults, deployed App or infrastructure changes. No c2 or other re-test without maintainer approval.

## Assumptions

- Preserve every c1 gate criterion and the pass-rule string byte for byte.
- Citation evidence still invalid after correction fails the session and cell.
- Repetition is a model failure, not an eligible transient retry; retain every outcome.
- Launch without an idle window under the maintainer's 2026-10-25 instruction and 2026-10-22 c1 precedent. Sample load throughout by client and key.
- Only the LAN LiteLLM Spark alias may be called; verify zero fallbacks and paid calls after smoke and qualification.
- Use release 2026.10.36 (latest at preparation), nare 2026.10.12 and the unchanged pilot inputs. Download and hash the actual published wheel.

## Tasks

- [x] 1. Copy and adapt private c1 scripts; assert unchanged gate criteria and corpus bytes; freeze hashes on #51 before launch.
- [x] 2. Smoke the released wheel; assert both settings in prepared inputs and provenance, and verify spend.
- [ ] 3. Run g3 as scrutare-glm-g3.service with periodic load evidence; retain every attempt.
- [ ] 4. Export the report and offline reproducer; verify hashes, gate decisions, cause totals, correction accounting and spend/load evidence. Add meaningful mutation checks for the report verifier.
- [ ] 5. Commit, run both Python 3.10 and 3.14 preflight lanes under systemd, independent review, quality guard, push, green CI and merge.
- [ ] 6. Verify #96 closed and #51 open; post result on #51. Remove temporary token and worktree.
