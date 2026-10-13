# Original development corpus v2

Twelve purpose-written development cases that make specialist routing and evidence
disputes measurable. The original pilot's generic paths activate no built-in
specialist, and its findings were almost always undisputed, so neither feature
could be observed there. Every case here is in the `development` split. None is a
holdout case, and nothing here was derived from `evaluations/holdout-v1`, which
was not opened while these cases were written.

| Group | Cases | Purpose |
| --- | --- | --- |
| Covered routing, defect | `dev2-01`, `dev2-02`, `dev2-04`, `dev2-05`, `dev2-06` | A changed path matches a built-in rule and the defect is in that specialist's area. |
| Covered routing, clean | `dev2-03`, `dev2-07`, `dev2-08` | A changed path matches a rule; the change is correct but invites a specific wrong allegation. |
| Missed routing, defect | `dev2-09`, `dev2-10`, `dev2-11` | A concurrency, data-integrity or performance defect on a path no built-in rule matches. |
| Unrouted, clean | `dev2-12` | A generic path; correct change that invites a specific wrong allegation. |

Eight cases carry one labeled defect each and four are clean. Identifiers are
opaque so runtime paths do not name a defect, a category or a clean control.

## What activates a specialist

Only the changed paths in each `files.json` and the product's built-in rules.
`performance-concurrency` matches basenames containing `async`, `thread`, `queue`,
`cache`, `concurr` or `perf` and the directories `concurrency/` and `performance/`.
`data-integrity-migrations` matches `migrations/` or `migration/` directories,
`*.sql`, and basenames containing `schema`, `transaction`, `database` or
`integrity`. Labels record the expected activation for checking, in
`routing_expectation`; the router never reads labels, and tests prove the
expectation from `files.json` alone. Do not set `routing.force` from a label.

## Plausible but wrong allegations

The four clean cases each contain a change a careful reviewer could misread: an
unlocked buffer whose single owner holds the lock, an f-string that interpolates
only a module constant, a retried insert that is idempotent by primary key, and a
removed zero check that an unchanged constructor makes unreachable. The fact that
refutes each allegation is public: it is in the changed file in both revisions,
or in an unchanged file listed in `related-paths.json`. No clean label depends on
a restriction only the evaluator knows. Labels list these under
`tempting_allegations`. Whether a reviewer raises one, and whether assessment or
discussion then disputes it, is left entirely to the run. Nothing forces a
dispute, and a run with no dispute is a valid observation.

## Layout and isolation

`captures/ID` holds only `diff.patch`, `files.json` and `metadata.json`.
`sources/ID/base` and `sources/ID/head` hold the complete trees for repository
context. `labels/`, `controls/`, `build.py` and `verify.py` are evaluator-only and
never enter a model read root. To give reviewers the unchanged supporting files,
pass the paths in `related-paths.json` as `context.related_paths`; the same list
applies to every arm.

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
describes. Controls are deterministic: concurrency cases use an injected lock that
performs the competing call at the moment of entry, with no threads, timing or
network. `mechanical-report.json` records the result and `freeze-sha256.json`
seals every fixture byte; the verifier refuses to execute if the bytes differ.

```sh
uv run --extra dev python evaluations/development-v2/verify.py
```

Run `build.py` only to change the catalog; it rewrites every derived file.

## Limits

These are small synthetic examples. Controls establish the labeled behaviour and
the specific clean invariants, not the absence of every other defect. Twelve
cases cannot support a general accuracy or routing claim. Labels are
author-defined hypotheses until a person adjudicates findings against them.
Because these cases are used for development measurements, they can never serve
as unseen validation.
