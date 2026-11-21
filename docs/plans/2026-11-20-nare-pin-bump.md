# Nare pin: one source and an automatic bump pull request

## Goal and design

nare ships often, and Scrutare's pin moved only when someone noticed (#129). The
runtime behind every review should not drift, and a release that breaks Scrutare
should be found the day it ships. This is part of making self-hosted inference a
production rail (#137).

Three pieces:

1. **One source of truth.** `nare.pin` at the repository root holds the release tag
   and commit as `NARE_RELEASE=` and `NARE_COMMIT=` lines. A plain `KEY=value` file
   was chosen over a `[tool.scrutare.nare]` table in `pyproject.toml` because every
   reader can consume it with no parser: the Dockerfile and workflows source it with
   `.`, and Python reads it in five lines. `tomllib` is Python 3.11+, so a TOML table
   would need `tomli` on the supported 3.10, and `pyproject.toml` is package metadata
   that the Docker build context deliberately excludes. The Dockerfile copies
   `nare.pin` into its builder stage rather than taking a build argument, so every
   `docker build` (release, the Action container tests, a local build) gets the
   pinned release without a caller passing it, and the image keeps its single
   `SCRUTARE_WHEEL` argument.
2. **A bump tool.** `scripts/bump-nare.py` (strict types, in the mypy command) has
   `status` and `bump`. `bump` verifies the target tag's commit with
   `git ls-remote` (peeling annotated tags), rewrites `nare.pin`, the literal
   versions in the user documents and the previous pin in the pin test, and prints
   the release notes in the range. Given both releases' `nare contract` output it
   lists the contract version, flags and entries added, removed or changed, and
   every changed default. Release notes drop nare's checksum block, qualify `#N` as
   `ViviDynamics/nare#N` (otherwise it links a Scrutare issue) and break `@`
   mentions so a bump notifies nobody.
3. **A scheduled workflow.** `nare-bump.yml` runs daily and on dispatch. When the pin
   differs from nare's latest release it installs both releases, records their
   `nare contract`, runs the bump, and opens or refreshes one pull request from the
   fixed branch `automation/nare-bump`. A contract version change is called out and
   labeled `nare-contract-change`. It never merges.

`GITHUB_TOKEN` pushes and pull requests start no other workflow. Of the two ways
out (dispatch CI from the bump workflow, or require a maintainer token secret), the
workflow dispatches CI and the disclosure guard itself, as Auto-tag already
dispatches Release. That costs one extra permission, `actions: write`, next to
`contents: write` and `pull-requests: write`, and needs no long-lived secret that
could push as a person. CI and the disclosure guard gain a `workflow_dispatch`
trigger; the guard takes the pull request number as an input.

Policy, recorded in `docs/releases.md`: a nare defect found by a bump is filed on
ViviDynamics/nare and fixed there. Scrutare does not carry workarounds.

## Task 1: One pin file

Files: `nare.pin`, `Dockerfile`, `.dockerignore`, `.github/workflows/ci.yml`,
`.github/workflows/release.yml`, `scripts/smoke-container.sh`, `tests/nare_pin.py`,
installed tests, `tests/test_action_container.py`, `tests/test_release_packaging.py`,
`tests/test_nare_runtime_pin.py`.

- [x] `nare.pin` with the current pin, 2026.11.107 at
  4baaeb8527478401cbd7bf54b74f48c286d8a093.
- [x] The Dockerfile copies and sources it; `.dockerignore` admits it; the Action
  container test copies it into its build context.
- [x] Both workflows' install steps source it; no workflow names the release.
- [x] `smoke-container.sh` passes the pinned release into its in-image check.
- [x] Installed tests read the expected version from `tests/nare_pin.py`; their
  docstrings no longer name a release.
- [x] The pin test reads the pin file, asserts every reader uses it, and that no
  test besides the pin test names the release.

## Task 2: Bump tool

Files: `scripts/bump-nare.py`, `tests/test_bump_nare.py`, `.agents/test-commands.md`,
README.

- [x] Tests first, against fake release JSON and a local git remote: status, the
  no-op when current, a moved tag refused, dry run writes nothing, a real bump
  (annotated tag peeled, documents and previous pin moved, unrelated versions
  kept), older, unpublished, malformed and missing targets refused, a document
  that stops naming the pin stops the bump, additive and version-changing contract
  diffs, and the script and tests reading the pin alike.
- [x] The script, typed under `mypy --strict` on 3.10 and 3.14.
- [x] The mypy command in `.agents/test-commands.md`, README, CI and release.

## Task 3: Workflow and documents

Files: `.github/workflows/nare-bump.yml`, `.github/workflows/ci.yml`,
`.github/workflows/disclosure-guard.yml`, `docs/releases.md`,
`docs/session-fanout.md`.

- [x] Daily and dispatch triggers, `permissions: {}` at the top and the three job
  permissions, one fixed branch, open or refresh one pull request, label on a
  contract version change, no merge, dispatch CI and the guard.
- [x] A run with nothing new leaves the branch and pull request alone; a branch
  with a maintainer's commit is never overwritten.
- [x] A test of the workflow's shape, and actionlint.
- [x] `docs/releases.md`: the pin file, the tool, the workflow and the nare defect
  policy.

## What stays unchanged

- Every installation still uses nare 2026.11.107 at the same commit, so no review,
  argv, request byte or replay record changes.
- Fixtures and comments that describe what an older nare wrote keep their version.
- Plans keep the versions they were written against; the bump tool rewrites only
  the user documents it lists.
- The contract number Scrutare expects (3) stays in code and tests; a release that
  changes it is labeled and needs a person.
