# Sealed original holdout v1

Forty purpose-written cases: eight in each of correctness, security, testing,
operations and consumer behavior. Each domain has five labeled regressions and
three guarded clean controls. All cases are holdout-only, disjoint from the pilot.
Guarded input restrictions are public, identical contracts in both captured source
versions, enforced by unchanged runtime checks: built-in integer coordinates and
positive width, built-in list fixtures/queues, and finite monotonic times. They
remain visible in the unified patch context as well as repository context. Clean
labels never rely solely on evaluator-private restrictions to dismiss valid changes.
Public IDs are opaque (`sealed-h01` through `sealed-h40`) so runtime paths do not
encode defect names or categories.
Source code and labels are original contributions under Elastic License 2.0.
No external PRs or copied datasets are represented here.

The base and head revisions are real deterministic **local synthetic Git commit
objects**, generated with a fixed author and timestamp. The repository identity
`sealed-fixtures/local` and PR metadata are synthetic. They do not assert hosted
GitHub authenticity or remote delivery. `build.py` rebuilds original fixture data;
the locked development Ruff normalizes import layout before Git objects are made.
Git generates every exact patch and revision. `verify.py` independently reconstructs
the commit pair and patch from the stored source bytes, then checks and applies
every patch to the base tree and compares the result byte-for-byte to the head.

Only `diff.patch`, `files.json` and `metadata.json` live in each public capture.
Labels, controls, catalog, verifier and full base/head source directories live
outside the capture. Repository-context selection may expose only captured source
files, never evaluator labels or controls. Unchanged contracts and callers support
cross-file cases. Operations paths include `src/concurrency/` and `src/migrations/`
to exercise the deterministic specialist router.

`freeze-sha256.json` seals all fixture/code/document bytes and a canonical inventory
digest. `mechanical-report.json` records deterministic trusted base/head controls
and reconstructed commit identities: buggy base controls succeed and head controls
fail; clean controls succeed on both revisions. Controls use original fixture code
in isolated temporary directories, standard-library SQLite or deterministic local
state, without threads or network calls. This verifier is specifically for these
checked-in trusted fixtures and must never execute untrusted PR/corpus code.

Run `uv run --extra dev python evaluations/holdout-v1/verify.py` to reproduce the
report. Every source must compile and pass repository Ruff rules. No model sessions
or actual human judgments were run while creating the sealed corpus. These controls
establish the labeled behaviors and specific clean invariants, not model accuracy
or universal absence of defects. Model-unseen means no local provider exposure yet;
it does not establish absence from a provider's global training data.

Runner development and tuning must use independent fixtures or development cases.
This holdout is frozen before any architecture comparison, and must not be reused
for tuning after its first measured evaluation. Human adjudication, fresh evidence
comparisons and promotion decisions remain on #43/#51.
