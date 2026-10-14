# Original development corpus v3

Twelve purpose-written development cases for the comparisons that
`evaluations/development-v2` could not separate. On that corpus every arm found
every labeled defect in every cell it completed, its clean cases drew no wrong allegation in the runs made
so far, and its fixtures pass the Ruff rules the tool arm captures, so that arm
had no diagnostic to show. Every case here is in the `development`
split. None is a holdout case, and nothing here was derived from
`evaluations/holdout-v1`, which was not opened while these cases were written.

| Group | Cases | Purpose |
| --- | --- | --- |
| Ruff names the defect | `dev3-01`, `dev3-02` | A redefinition (`F811`, reported on an added line; the definition it replaces is far above) and a stale name (`F821`, far from the changed lines). |
| Ruff suggests a wrong allegation | `dev3-03` | Clean. Two `F401` unused-import diagnostics on a facade whose imports are its public surface. |
| Ruff is silent on a real defect | `dev3-04` | An off-by-one page offset no selected rule can see. |
| Clean, generic path | `dev3-05` | Plain `==` replaces a constant-time comparison of two public checksums. |
| Clean, routed | `dev3-06`, `dev3-07` | An unlocked read of an immutable snapshot; a backfill committed in batches. |
| Defect behind a reassuring comment, routed | `dev3-08`, `dev3-10` | A cache key and a migration step dropped on a stated assumption that an unchanged file contradicts. |
| Defect in the specialist's area, routed | `dev3-09` | Lock acquisition order lost while adding a balance check. |
| Deciding evidence outside the capture | `dev3-11` (defect), `dev3-12` (clean) | A function stops raising. Whether that matters depends on a caller reviewers are not shown. |

Seven cases carry one labeled defect each and five are clean. Identifiers are
opaque so runtime paths do not name a defect, a category or a clean control.

## Tool evidence

`tool_expectation` in each label lists the diagnostics pinned Ruff 0.16.10 must
report for the head revision under the capture selection `E4,E7,E9,F`, and how
they relate to the label. A test runs the real producer over this corpus and
compares. The corpus manifest declares no `analysis_capture`: capture the
evidence privately for each job with
`scrutare.evaluation.tool_capture.capture_development_ruff` and add the path to
the private manifest copy, as [docs/evaluation.md](../../docs/evaluation.md)
describes. A silent tool is never evidence that a change is correct; `dev3-04`
exists to observe that.

Four fixture files must fail the repository's own lint for this to work.
`pyproject.toml` exempts exactly those files and codes, and a test pins the
exemption to the labels.

## Cases whose deciding evidence is not captured

`dev3-11` and `dev3-12` are a pair. Each changes a function from raising to
returning a value. In `dev3-11` the only caller relied on the exception, so the
change is a defect; in `dev3-12` the only caller never did, so it is clean. The
caller is in the source tree and is deliberately absent from
`related-paths.json`, so no reviewer or assessor can read it. A finding on either
is a reasonable inference that the captured evidence can neither confirm nor
refute. They exist to see whether such a claim is raised, how an assessor rules
on it and whether discussion changes the outcome. Labels list the hidden file
under `evidence_outside_capture`. Exclude both cases from any precision or
false-positive comparison: a "wrong" finding on `dev3-12` is not a reviewer error.

## Plausible but wrong allegations

The other four clean cases each contain a change a careful reviewer could
misread. The fact that refutes each allegation is public: it is in the changed
file in both revisions, or in an unchanged file listed in `related-paths.json`.
For all four the refuting fact is outside the diff hunk: in an unchanged related
file for `dev3-03`, `dev3-05` and `dev3-07`, and in the module docstring of the
changed file for `dev3-06`. Their clean labels therefore hold only in arms that run
with repository context and the related paths. A diff-only arm cannot see the
fact, and the allegation is a reasonable finding there; exclude these cases from
clean false-positive comparisons that include a context-off arm. Their labels carry
`clean_label_requires_context`. Labels list the allegations under `tempting_allegations`. Whether a reviewer
raises one, and whether assessment or discussion then disputes it, is left
entirely to the run. Nothing forces a dispute, and a run with no dispute is a
valid observation. `dev3-07` and `dev3-09` also list `defensible_observations` that need a
person's decision and must not be scored automatically as false positives.

## What activates a specialist

Only the changed paths in each `files.json` and the product's built-in rules, as
in development-v2. `dev3-06`, `dev3-08` and `dev3-09` activate
`performance-concurrency`; `dev3-07` and `dev3-10` activate
`data-integrity-migrations`. Labels record the expectation in
`routing_expectation` for checking; the router never reads labels. Do not set
`routing.force` from a label.

## Layout and isolation

`captures/ID` holds only `diff.patch`, `files.json` and `metadata.json`.
`sources/ID/base` and `sources/ID/head` hold the complete trees for repository
context. `labels/`, `controls/`, `build.py` and `verify.py` are evaluator-only and
never enter a model read root. Pass the paths in `related-paths.json` as
`context.related_paths`; the same list applies to every arm.

Base and head revisions are real deterministic local Git commits made with a
fixed author and timestamp, and Git generates each patch. The repository identity
`development-fixtures/local` and the pull-request numbers are synthetic and claim
no hosted origin. All code and labels are original Elastic-2.0 contributions; no
external pull request or dataset is represented.

## Mechanical checks

`verify.py` recreates each commit pair and patch from the stored trees, applies
the patch to the base and compares the result with the head byte for byte,
compiles every Python source, and runs one executable control per case against
both revisions. A defect control passes on the base and fails on the head. A clean
control passes on both and exercises the scenario the tempting allegation
describes. Controls are deterministic: they use recording or hook locks and an
in-memory SQLite database, with no threads, timing or network. The controls for
`dev3-11` and `dev3-12` run through the hidden caller. `mechanical-report.json`
records the result and `freeze-sha256.json` seals every fixture byte; the verifier
refuses to execute if the bytes differ.

```sh
uv run --extra dev python evaluations/development-v3/verify.py
```

Run `build.py` only to change the catalog; it rewrites every derived file.

## Limits

These are small synthetic examples written by the same author as the labels, with
knowledge of how the earlier corpus behaved on one model. Controls establish the
labeled behaviour and the specific clean invariants, not the absence of every
other defect. Twelve cases cannot support a general accuracy, routing or tool
claim. Labels are author-defined hypotheses until a person adjudicates findings
against them. Because these cases are used for development measurements, they can
never serve as unseen validation.
