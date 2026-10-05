# CLI and release packaging implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

Issue #7

**Goal:** Run capture, panel execution and confirmed posting through the installed CLI, with version-attributed artifacts and tagged wheel/container releases.

**Architecture:** Compose existing engine and poster APIs. Record version provenance in an exclusive content manifest while preserving canonical artifacts. Build the image from the same verified wheel attached to the release.

**Tech Stack:** Python >=3.10, asyncio, Hatchling, uv, external nare 2026.10.4/contract 1, gh, Docker and GitHub Actions.

**Spec:** docs/SPEC.md. User approved a versioned artifact manifest recording producer version and digests while preserving raw snapshots and exact findings/verdict formats.

## Scope

In: full review CLI, result/provenance envelopes, one package version source, installed-wheel offline pipeline proof, a runnable container, tag-driven release workflow and matching installation documentation.
Out: new YAML keys, session retries, iterative/debate implementation, recovery CLI, App identity, garden deployment and automatic tagging on main merges. Literal live-PR and publication verification require prepared code first and any credential-sensitive authorization under the user's stop rule.

## Global Constraints

- Every model call goes through nare. Actual tests substitute only the vendor transport factory; no live provider calls or credentials in tests.
- Preserve Python 3.10 support and separate nare installation. Selected nare is 2026.10.4, commit 79405f9d2e3db2efe4a3ffda35faaf1680000acd.
- Preserve approved missing-output, valid-partial, after-turn budget, filtered-root and code-owned verdict behavior. Never manufacture escalation from a failure or budget stop.
- Keep raw capture/config bytes, bare findings arrays and exact schema-1 Verdict bytes unchanged. Existing legacy and cross-version replay identity remains valid.
- A manifest records a quiescent local snapshot, not authenticity, provider billing or remote delivery. It does not change replay's canonical deciding evidence.
- Publish a success line only after posting and required result/provenance persistence succeed. Retain evidence and identify a captured run on failures.
- No credential values, stored-secret changes, App identity or deployment work. No release tags, image pushes or live reviews during implementation/local tests.
- No em dashes in authored published prose. One issue and one implementation worker at a time; coordinator integrates and dispatches independent reviews.
- Issue-wide limits: two review fix rounds, two rebases and two verified CI retries. User approval is required to exceed a fix limit.

## Review Focus

- Canonical-byte drift across producer/replayer versions: Task 1 preserves verdict bytes and legacy replay while stamping envelopes.
- Stale, linked or nonregular provenance destinations/content: Task 1 refuses unsafe traversal and never overwrites evidence.
- Failure after confirmed posting: Task 2 reports artifact failure with the retained receipt and never claims no remote write occurred.
- Missing initial output or PR closure before posting: Tasks 2 and 4 require no POST and no success line.
- Release version/wheel/image mismatch or unsafe build context: Task 3 rejects mismatches before publication and Task 4 verifies installed version agreement.

## Tasks

- [x] 1. Producer version and exclusive artifact manifest.
- [x] 2. Full review service and CLI orchestration.
- [x] 3. Container and tagged release packaging.
- [x] 4. Installed-wheel pipeline proof and user documentation.

### Task 1: Producer version and exclusive artifact manifest

**Files:** Create src/scrutare/provenance.py and tests/test_provenance.py; modify src/scrutare/__init__.py, pyproject.toml, uv.lock, replay/models.py and version-related tests; narrowly extend engine/session_artifacts.py and panel_artifacts.py, with stale-evidence tests. Clarify SPEC section 10 with the approved provenance interpretation.

**Interfaces:** `write_artifact_manifest(run_dir: Path) -> Path`, raising safe `ProvenanceError(ValueError)`. Fixed destination artifacts.json. Shape: schema_version=1, scrutare_version, artifacts (sorted array of objects with path, sha256, size_bytes). Paths are relative POSIX file names. The manifest itself is version-stamped, not self-hashed.

- [x] Write and observe RED tests for actual byte digests, deterministic nested inventory, empty files, Unicode names, unchanged raw/findings/verdict bytes and legacy/cross-version replay identity.
- [x] Pin explicit inventory exclusions: artifacts.json itself, .posting.lock, and staging files matching .scrutare-*.tmp or .posting-*.tmp. Directories are traversed but not content entries. Reject symlinks and nonregular entries instead of following or opening them, including linked ancestors and FIFOs.
- [x] Test existing destination refusal for regular, linked and nonregular entries, owner-only atomic installation, write races and preserved prior bytes. Hash bounded chunks through no-follow file descriptors; detect ordinary file replacement/change while reading. No broad adversarial history guarantee.
- [x] Extend the existing exact owned run destinations only for artifacts.json and result.json; reuse atomic exclusive installation without permitting arbitrary run-root writes. Add both final names to panel preflight refusal and its four-kind stale-entry regressions so copied final provenance/results cannot cause new model spending. Inventory only after owned execution and posting are quiescent. A failed run snapshot cannot imply successful execution or posting.
- [x] Use __init__.py as the single authored version source, initially 2026.10.0 following the family's unprefixed CalVer. Configure Hatch dynamic version from that file and regenerate the editable lock metadata without upgrading unrelated dependencies. Runtime/wheel/version output must agree.
- [x] Add scrutare_version to replay result envelopes using the executing package version, never to the recomputed Verdict. Existing consumers continue accepting legacy runs without a manifest; no claim that replay validates manifest integrity.
- [x] Run focused provenance/replay/version tests, Ruff and strict mypy, build/install a wheel to verify metadata/version equality, record native exits and tested hashes, then commit. Include the plan with this first task commit.

### Task 2: Full review service and CLI orchestration

**Files:** Create engine/review.py and tests/test_review_run.py; modify interfaces/cli.py and tests/test_cli.py; extend review/posting/replay composition tests where needed.

**Interfaces:** `async review_pr(ref: PullRequestRef, config: ReviewConfig, *, config_bytes: bytes, runs_root: Path, runtime: NareRuntime, capture_client: GitHubClient | None = None, review_client: ReviewClient | None = None) -> ReviewRunResult`. Frozen result exposes `to_dict()` and `to_bytes()`. `ReviewRunError` has a safe message, optional run_dir and a confirmed_posting flag for errors after a confirmed receipt. CLI uses asyncio.run. Preserve existing replay exit meanings.

- [x] Write RED tests for capture -> real panel API -> correct poster -> identical persisted/stdout result, approved/blocking/comment/valid-partial outcomes, and version attribution.
- [x] Ordinary result fields: schema_version=1, scrutare_version, status=posted, run_dir, head_sha, verdict, rule, panel_status, usage, accounting_complete and the complete six-field confirmed review receipt. Genuine escalated delivery adds the confirmed reviewer_request from post_escalation only after both stages complete or targets are explicitly empty.
- [x] Require a real engine Verdict before posting. Failed/missing/uncertain initial output never reaches a poster. Route an actual exhaustion-bearing escalated Verdict to post_escalation; do not create an exhaustion object in this orchestration layer.
- [x] Validate config once before GitHub, pass the same source bytes and normalized object to capture. Reject currently unsupported strategies before capture. Add operational --nare-executable PATH defaulting to nare; reject an unavailable executable by PATH/file presence before capture; retain existing runtime turn/timeout defaults and authoritative engine contract inspection. No new YAML keys or automatic runtime installation.
- [x] Install exclusive root result.json bytes, then the Task 1 manifest after confirmed posting. Output the exact result bytes only after both persist. On execution/delivery failures, record a manifest of remaining evidence when safely possible, preserve the primary safe error and run path, and print no success JSON. Artifact failure after posting explicitly retains confirmed-posting status.
- [x] Test poster rejection/uncertainty, PR closure, post-success persistence failure, failed manifest writing, invalid config/runtime, unsupported strategy and interruption. Do not add retries or recover/reuse an existing run.
- [x] Review exits: 0 for confirmed completed delivery of any verdict, 1 for config/runtime/panel/delivery/artifact failure, 2 for argument usage and 130 for interruption after engine cleanup. Verdict-dependent blocking remains the GitHub review or result data; comment mode never blocks. Replay remains 0/1/2.
- [x] Run focused CLI/service and existing panel/poster/replay regressions, Ruff/mypy, record native exits/hashes and commit.

### Task 3: Container and tagged release packaging

**Files:** Create Dockerfile, .dockerignore, .github/workflows/release.yml, scripts/check-release.py, scripts/smoke-container.sh and tests/test_release_packaging.py; strengthen scripts/smoke-cli.sh. Extend CI/test-command rows only for genuinely required repeatable package checks.

**Interfaces:** `python scripts/check-release.py TAG WHEEL` exits 0 only when an unprefixed YYYY.M.PATCH tag, authored runtime version, wheel METADATA and exact wheel filename agree. Container receives one explicitly selected prebuilt wheel through SCRUTARE_WHEEL. It runs scrutare directly as a non-root user with writable /work.

- [x] Write RED tests for version/tag/wheel disagreement, malformed tags, wheel ambiguity, release trigger/permission/publication order and required runtime/context boundaries. Tests must check meaningful behavior rather than only mirror every YAML line.
- [x] Build the image from the verified Scrutare wheel, not a source checkout. Install pinned external nare 2026.10.4 after exact commit verification and locked dependencies in a separate environment; install gh from a verified public source. A compatible Python 3.14 runtime is allowed without changing Scrutare's >=3.10 floor.
- [x] Exclude .git, .agents, .superpowers, worktrees, .venv, .scrutare, caches, local env/auth files and token files from the context. Never pass credentials as image build arguments or bake them into layers. Runtime callers supply credentials normally; tests supply none.
- [x] Tag-driven workflow checks out and validates the tagged commit on main history, runs required gates before publication, builds exactly one wheel, validates it, builds and smokes the corresponding image, creates the release with its wheel asset, and publishes ghcr.io/vividynamics/scrutare:TAG. Use job-token contents/packages permissions only, no PyPI token or new stored credentials. Do not auto-tag main or require mutable latest aliases.
- [x] Strengthen installed smoke to select exactly one wheel and assert package metadata and console/module version agreement, retaining packaged-persona proof and invalid-input refusal.
- [x] Run a real local Docker build using the reachable default builder. Smoke console/module version and help, gh, selected nare version/contract, non-root identity, packaged personas and writable run directory. Save commands/native output and image identity. Public dependency downloads are allowed; live model calls and remote publication are not.
- [x] Run focused packaging tests, Ruff/mypy and wheel/smoke gates; record evidence and commit. Coordinator will later justify scoped packaging/config changes in the PR quality gate section.

### Task 4: Installed-wheel pipeline proof and user documentation

**Files:** Add tests/test_installed_review.py and narrowly extend offline helpers; extend scripts/smoke-cli.sh if needed; update README.md, docs/panel.md, docs/session-fanout.md and add docs/cli.md and docs/releases.md.

**Interfaces:** Execute the installed wheel outside the checkout through its real console/module entrypoints. Reuse the actual nare worker's sole vendor factory substitution. Fake gh supplies capture/lifecycle and actual POST protocol responses; all production ingestion, execution, poster and replay code remains real.

- [x] Observe RED end-to-end cases for approval/blocking/correction, comment mode, valid partial output, failed/missing initial output without POST, PR closure, unsupported strategy/missing runtime before side effects and uncertain delivery without success. Fake gh records stdin payloads and returns valid included HTTP headers/receipts; assert exactly one correct POST where eligible.
- [x] Verify saved/posted replay identity, exact persisted/stdout result, version agreement, complete manifest digests/exclusions, unchanged canonical bytes, actual systems/rails/root/accounting and zero offline guard/credential observations. Neither the fake GitHub endpoint nor substituted model transport is literal live-PR evidence.
- [x] README installs the pinned GitHub release wheel or pinned GHCR image and documents gh plus separate nare for wheel users. Do not claim PyPI installation or published assets before a release exists. Explain operational executable selection, exits, partial coverage, manifest snapshot/trust limits, canonical replay and unsupported strategies.
- [x] Run all six exact gates on Python 3.10 then 3.14 with selected actual nare, no shared-venv overlap or skips. Retain commands/native outputs, actual pipeline evidence and tested/committed hashes. Repeat the final container smoke if packaged contents changed since Task 3.
- [x] Commit and report any remaining literal live-PR/publication acceptance gate for coordinator handling after a fully reviewable result exists.

## Completion

Independent task reviews precede each next task. Record rulings and costs, then whole-branch review with bounded fixes/scoped re-review. Run committed-head preflight and quality guard, open/link PR, watch nonempty exact-head CI, update its summary and squash-merge only when acceptance and any user-required credential authorization are resolved. Archive evidence before worktree cleanup. Continue #12 after #7, stopping at milestone completion per the user's instruction.
