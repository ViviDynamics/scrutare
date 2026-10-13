# Expanded sealed holdout corpus

Issues #43 and #51. M-sized data extension to the existing corpus interface.

## Scope
In: forty original holdout-only examples, five domains with five defects and three
guarded clean controls each; exact Git-generated capture patches, local synthetic
commit identities, source inventories and independently executable controls.
Out: provider calls, human adjudication, training-set independence claims, quality
promotion, execution of captured external code, and closing #43/#51.

## Assumptions
- New IDs never overlap the original pilot. The development runner must not inspect
  these contents for tuning; model-unseen means no local provider runs yet.
- Git identities describe deterministic local fixture commits, not hosted PRs.
- Labels, verifier and control scripts remain outside all model input roots.
- Every source compiles and passes Ruff. Defects are semantic regressions.
- Deterministic controls use only original trusted fixtures and standard-library
  local behavior; clean examples prove specific invariants, not universal absence.
- Carry issue43 counters: retries0, rebases0, review fix rounds1.

## Tasks
- [x] 1. Red corpus contract tests; generate forty explicit cases with labels,
  unchanged supporting contracts and public three-artifact captures.
- [x] 2. Red mechanical verification tests; independently execute base/head gold
  controls, validate Git identities and exact patch application, freeze SHA inventory.
- [x] 3. Verify full corpus area, source lint and compile; document licensing,
  sealed boundary and mechanical report; commit for fresh independent review.

## Verification
The initial contract tests failed because the corpus/verifier did not exist.
Additional red checks rejected descriptive IDs and missing exact-patch application
counts before those behaviors were added. Final data is forty opaque IDs, 25 bugs,
15 controls, thirteen unchanged supporting-file cases and eighty trusted control
executions. Each of forty generated patches also applies exactly to its base tree.
The evaluation/corpus area passes 42 checks on both Python 3.10 and 3.14; full Ruff
and strict mypy pass. A production repository-context projection exercises the
real loader/snapshot/preparation path with an explicitly offline intercepted engine,
retaining unchanged source bytes and excluding all evaluator artifacts. No native
or paid model job is represented by that unit proof. Root owns final native/full
shipping gates and independent claim review before provider evaluation.
