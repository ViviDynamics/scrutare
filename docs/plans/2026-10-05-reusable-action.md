# Reusable GitHub Action implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development task by task. The coordinator owns integration and independent review.

**Issue:** #14
**Goal:** Give a caller a pinned, approximately ten-line workflow that reviews its PR through the released CLI, posts as GitHub Actions, and retains evidence on failure.
**Architecture:** A root composite Action fetches only trusted base configuration, then invokes the versioned container once through a small standard-library adapter. Engine execution, accounting, verdict derivation and posting remain in the existing CLI.
**Tech stack:** Python 3.10-compatible adapter, Bash, Docker, GitHub-hosted Ubuntu 24.04 x64, separate image nare 2026.10.4.
**Spec:** [docs/SPEC.md](../SPEC.md), sections 5, 8, 10, 11 and 12.
**State:** Both implementation tasks and independent task reviews are complete. Whole-branch review, full verification, publication and literal hosted acceptance remain pending. New secret installation remains separately held. Hosted COMMENT acceptance requires no approval-policy change.

## Scope and decisions

Ship `action.yaml`, its adapter, meaningful unit and installed-container proof, and `docs/pipeline.md`. Reconcile stale first-release wording. Use the next coherent release, `2026.10.1`, for source, wheel, CLI, image and the Action-containing tag. The existing `2026.10.0` tag remains immutable. The remote `2026.10.1` tag was absent at planning time; recheck before publication.

The recommended caller uses `pull_request_target` with `contents: read` and `pull-requests: write`. The Action checks out the event's base repository and exact base SHA into its own directory with credential persistence, submodules and LFS disabled. It executes no caller or PR scripts and mounts only copied config and a separate evidence directory. Support same-repository `pull_request` only with an explicit fork refusal. Do not promise arbitrary enterprise, Dependabot, self-hosted, ARM, Windows, macOS or job-container compatibility.

Use the automatic `github.token` as `GH_TOKEN`; the M1 identity is GitHub Actions, normally `github-actions[bot]`. Existing provider credentials are supplied by the caller as `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` in the Action-step environment. Forward only those names and `GH_TOKEN`, never values in argv, output files or logs. Do not create a token, select a PAT identity, install a secret, change approval settings or touch App/cluster resources under this plan without separately explicit authorization.

This credential and privileged-event design requires approval under the user's original instruction to stop for anything touching credentials. Earlier local LiteLLM acceptance does not implicitly install unattended workflow secrets. Literal hosted acceptance remains required; any missing secret or repository setting must be presented concretely when the implementation is ready.

### Observed approval policy and authorization boundary

Read-only API checks show both Scrutare and ViviDynamics currently return `can_approve_pull_request_reviews: false`. No permission change was attempted. GitHub documents repository configuration and inherited organization defaults; do not infer from these two booleans alone whether a repository override is permitted. The proposed approval covers this credential workflow design, use of the existing LiteLLM credential for hosted acceptance, and enabling approval reviews for Scrutare only if the organization permits that repository change. Installing a new Actions secret or changing organization policy requires a separately concrete approval when ready. No org-wide change is implicit.

Release publication and literal hosted acceptance must follow the implementation merge because the release guard requires main ancestry. If GitHub auto-closes #14 on that merge, reopen it and keep the board in review until publication and acceptance succeed. Keep the serial queue on #14. Do not claim M1 completion from the implementation merge alone.

## Global Constraints

- Every model call goes through nare. The Action invokes Scrutare, never nare or the engine directly.
- The panel does not retry initial sessions. The wrapper invokes one CLI review and never retries uncertain posting.
- Token thresholds apply after turns; actual usage and overshoot remain unclipped and are not a hard spending ceiling.
- The force-push result stays bound to the CLI's captured SHA, not the event head.
- Configuration remains the existing YAML surface. No image override, arbitrary command, executable, model, provider URL, budget or identity input.
- Raw captures/config bytes, bare findings arrays and canonical schema-1 Verdict bytes retain their formats.
- App identity and garden-cluster work remain M4. No em dashes in published prose or commits.

## Review Focus

- A fork changes config or supplies shell/output injection text: use trusted base bytes, argument arrays and validated fixed scalar outputs.
- Non-root image ownership hides private session files from upload: run with host UID/GID and test post-container readability without broad chmod.
- CLI failure, including uncertain posting or persistence after delivery, is followed by successful upload: the public Action must remain failed with no invented verdict.
- A result supplies an unrelated run path or malformed JSON: refuse success outputs and never upload an arbitrary path.
- A version tag exists before its image is published: announce caller availability only after both remote assets and actual hosted execution are confirmed.

## Shared interface

`src/scrutare/interfaces/action.py` is directly executable by host Python and uses only the standard library. `main(argv: Sequence[str] | None = None) -> int` accepts `prepare` or `review`. It does not depend on an installed host Scrutare package.

`prepare` consumes GitHub event/repository/run/temp/workspace/output environment and internal `SCRUTARE_ACTION_CONFIG` and `SCRUTARE_ACTION_IMAGE`. It validates a positive caller PR, matching base repository, exact base SHA and relative config path. It creates private invocation state and emits fixed outputs `state-file`, `checkout-path`, `repository`, `base-sha`, `evidence-root`, and `artifact-name`. The checkout path is relative to the workspace and uniquely owned; state contains no credentials. Metadata owns the fixed image value.

`review` consumes `SCRUTARE_ACTION_STATE_FILE` and the checked-out trusted config. Reject absolute/traversal/control-character paths and symlink components. Copy the regular config to a distinct read-only mount. Invoke the pinned image with explicit host UID/GID, `HOME=/tmp`, an isolated writable directory mounted at the same host/container absolute path, and one full PR URL. Retain raw stdout/stderr separately from CLI runs. Preserve the CLI exit; exit 0 additionally requires one valid successful JSON document, matching pinned runtime version, a contained run path, fixed scalar verdict/head fields and exact persisted result bytes. Emit `verdict`, `head-sha` and `run-dir` only then. Never scrape stderr for a path or derive success from a saved verdict after nonzero exit.

## Tasks

- [x] Task 1: Action adapter and boundary tests.
- [x] Task 2: Composite packaging, actual container proof and pipeline guide.

### Task 1: Action adapter and boundary tests

**Files:** Create `src/scrutare/interfaces/action.py` and `tests/test_action.py`.
**Consumes:** Existing CLI argv, durable result bytes and exit contract; the shared interface above.
**Produces:** Direct host `prepare`/`review` entrypoints and stable outputs for Task 2. No engine or poster changes.

- [x] Write focused failing adapter tests for event/repository/base validation, unsupported/fork contexts, nested/spaced/Unicode config, traversal/symlink refusal, unique private state, scalar/output injection, safe argv and credential-name forwarding.
- [x] Implement `prepare` and minimal owned-state/config handling. No arbitrary caller checkout, shell interpolation, environment dump or billable defaults.
- [x] Write failing `review` tests for one CLI call, approval and changes-requested exit 0, exits 1/2/130, empty failure stdout, uncertain delivery with no retry, malformed/trailing JSON, wrong version, outside-root run path, and mismatched saved result bytes.
- [x] Implement the subprocess adapter, raw capture, contained output validation and cleanup of its owned process. Keep success and failure evidence distinct; hard runner cancellation cannot guarantee upload.
- [x] Run `uv run --python 3.14 --locked --extra dev pytest tests/test_action.py`, Ruff and strict mypy. Retain exact commands, RED/GREEN outputs, input hashes and committed-blob comparisons. Commit, self-review and write the task report. Coordinator independently reviews before Task 2.

### Task 2: Composite packaging, actual container proof and pipeline guide

**Files:** Create `action.yaml`, `tests/test_action_packaging.py`, `tests/test_action_container.py`, `docs/pipeline.md`, and narrowly scoped acceptance fixtures/harness. Modify `src/scrutare/__init__.py`, `docs/cli.md`, `docs/releases.md`; modify CI/preflight only to run the new mandatory proof. Keep the existing release system.
**Consumes:** Task 1 command/output contract. Existing Dockerfile, released CLI and factory-only nare/GitHub test boundaries.
**Produces:** Fixed-version composite Action, installed-container proof, documented caller and a reviewable hosted acceptance workflow.

- [x] Write failing metadata/version tests. Pin internal checkout to `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` and uploader to `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`, both official v7.0.1 commits verified at planning time. No unsafe PR checkout permission. Use base repository/SHA, no credential persistence, no submodules/LFS. Pin runtime to `ghcr.io/vividynamics/scrutare:2026.10.1`.
- [x] Wire prepare, trusted checkout, review and a narrow always-running uploader with hidden files enabled, unique artifact name, finite documented retention, and no overwrite. Upload only this invocation's diagnostics/runs, excluding config-source checkout, host home and authentication files. A failed review remains failed after successful upload; upload failure also fails the Action. Test the actual outcome, not metadata strings alone.
- [x] Author version 2026.10.1 only in the existing package version source. Verify source/wheel/CLI/image pin agreement. Build the candidate image from the actual candidate wheel, not caller source or a replacement engine.
- [x] Run the same adapter against the actual installed candidate image with networking disabled and no live credentials. Replace only registry acquisition and the existing gh/vendor-factory boundaries in test-only harnesses. Cover approval, blocking, a correction, failed initial output and uncertain posting; assert real nare/session/accounting/canonical/result evidence, spaced mounts, explicit UID/GID, hidden-file archive completeness, and no continuation after failure. No public mock/image/executable inputs, engine overlays, fabricated JSONL or skipped required cases.
- [x] Document the complete input/env/output contract, trusted-base config prerequisites, bot identity and required repository approval policy, after-turn costs, artifact visibility/private provider detail, cancellation limits and posting recovery. Reconcile published 2026.10.0 facts and truthfully mark 2026.10.1 availability until published. Include an exactly ten-line caller pinned to 2026.10.1; internal checkout avoids a caller checkout step. Explain longer concurrency guidance without canceling in-flight posting.
- [x] Prepare actual hosted `uses:` success/failure acceptance, including a named authorized caller PR, pinned published runtime, remote bot review receipt, captured SHA and downloaded artifact hashes. Do not substitute a script invocation or an offline factory for literal posting/identity evidence. If an authorized caller secret/setting is missing, stop with the concrete ready workflow and missing operation. A temporary verification PR, if needed, is evidence for this issue rather than another implementation issue.
- [x] Run focused adapter/packaging/container and affected release suites with real Docker/nare, Ruff and strict types. Retain commands/output/input digests, commit and report. Independent task review is complete. Coordinator whole-branch review and both mandatory full six-gate lanes follow before pushing.

Hosted acceptance uses the supported `github.post_mode: comment` configuration. It will establish real automatic bot COMMENT identity/delivery and artifact outcomes, not remote APPROVE permission. Native cases prove approval and blocking behavior; the public guide documents the approval setting required by default review mode. This acceptance needs no repository or organization approval-policy mutation. The dedicated branch is `14-action-acceptance`, and new caller/acceptance runner pins are `ubuntu-24.04`.

## Verification and delivery

Fresh base9286b6b baseline: Python 3.14, actual nare2026.10.4, 2,655 tests with zero failures/errors/skips in221.89seconds. Current budgets: review fixes1/2, retries0/2, rebases0/2. Each task has a fresh worker and independent spec/quality reviewer; final review is whole-branch. Every changed quality gate must be justified by the Action proof scope, never weakened.

Open/link one implementation PR, watch nonempty green exact-head CI, and update the summary with observed evidence. Squash only after required gates. Keep issue14 incomplete until its Action-containing release, public wheel/image and literal hosted caller acceptance are verified. Verify issue/board closure, archive all costed rulings/evidence before owned worktree cleanup, then stop for M1 completion as originally instructed.
