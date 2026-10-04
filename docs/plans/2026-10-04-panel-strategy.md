# Panel strategy implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

Issue #10

**Goal:** Execute one independent persona wave, verify anchors with one correction opportunity, and publish a code-derived panel verdict.

**Architecture:** Retain one live execution context and budget ledger across initial and fresh correction invocations. Typed correction requests permit anchors only. Publish exclusive engine evidence and exact existing replay artifacts without posting.

**Tech Stack:** Python 3.10+, asyncio, external nare 2026.10.0 contract 1, pytest, Ruff, strict mypy.

**Spec:** docs/SPEC.md, especially sections 2, 3, 5, 6, 8, 9 and 11.

## Scope

In: executable panel engine API, real bounded correction calls, deterministic verification/dedupe/verdict, supported strategy dispatch, artifacts, tests and documentation.
Out: full review CLI and releases (#7), configurable retries (#12), iterative (#15), debate (#17), posting changes, App identity and deployment. Preserve ingestion-only CLI behavior.

## Assumptions

- User approved failure with artifacts and no verdict if any configured initial persona was unadmitted or has no validated findings document. Complete or partial validated output counts, including explicit empty output.
- Fresh corrections are verification within panel round one, not an additional independent review or a strategy round. They keep the same persona system, model rail and filtered read root.
- Denied correction after valid initial evidence drops its uncorrected originals. Failed correction execution fails the panel without verdict. Known budget-partial valid correction output remains usable.

## Global Constraints

- Every model call goes through nare. No live model/provider calls, credentials or network in test execution; actual CLI tests substitute only the vendor transport factory.
- No new YAML keys. Preserve fair ordered quotas and the same live ReviewBudgetLedger. Exhausted or uncertain accounting closes later admission immediately; preserve actual usage and overshoot. Already admitted work may finish.
- Models never declare a verdict, attribution or severity. Correction responses cannot change finding text/category/persona or introduce findings.
- Read only the validated filtered three-file review-inputs root, never the raw capture, excluded content or sibling artifacts. Exact persona systems and resolved rails remain unchanged.
- Initial persona and finding order determine final order, never task completion order. Preserve every duplicate occurrence and category conflict through existing verification and dedupe.
- Panel has one convergence pass and no Exhaustion. Failure, missing output, budget exhaustion and blocking findings are not escalation.
- Keep existing public fan_out and run_persona_session compatible. Findings is a bare ordered MergedFinding.to_dict array; verdict uses exact Verdict.to_bytes schema 1. Other metadata belongs in panel.json.
- Reject unsupported strategies before any preparation, runtime inspection or write. Exclusive artifact ownership, safe no-follow paths, private permissions and no overwrite of prior evidence apply throughout.
- No em dashes in authored prose, docs or commit messages. No child agents from workers. Coordinator owns task integration and independent reviews.
- Issue-wide review fix budget is two rounds; CI retries and rebases each two. Stop and surface real scope conflict or nare gap instead of inventing a workaround.

## Review Focus

- Missing output versus valid empty output: task 3 pins the approved eligibility policy.
- Equal evidence with distinct bad anchors: task 1 pins stable request identity; task 3 preserves occurrences.
- Actual uncertainty or quota crossing: task 2 pins shared-ledger admission and active sibling settlement.
- Filtered or tampered inputs: tasks 2 and 3 pin root validation and excluded-anchor rejection.
- Existing artifacts and partial publication: task 3 pins pre-model refusal and exclusive installation, verdict last.

## Tasks

- [x] 1. Typed correction inputs and protocol.
- [ ] 2. Shared execution context and correction adapter.
- [ ] 3. Panel strategy and exclusive final evidence.
- [ ] 4. Actual nare panel proof and user documentation.

### Task 1: Typed correction inputs and protocol

**Files:** Create src/scrutare/engine/reanchor.py; modify persona_inputs.py and session_output.py; create tests/test_reanchor.py; extend tests/test_persona_inputs.py and tests/test_session_output.py. Extend actual integration fixtures only as necessary for the design-time schema proof.

**Interfaces:** ReanchorRequest(request_id: str, original: Finding) is frozen. make_reanchor_requests(originals: tuple[Finding, ...]) -> tuple[ReanchorRequest, ...] deduplicates exactly equal originals in input order and assigns r0001, r0002, etc. reanchor_schema() -> dict[str, object]. parse_reanchor_output(value: object, requests: tuple[ReanchorRequest, ...]) -> tuple[ReanchorCorrection, ...]. PersonaReanchorInput(inputs: PreparedReviewInputs, persona: PersonaDefinition, requests: tuple[ReanchorRequest, ...]) mirrors the fixed prompt/nare_input_args properties of PersonaReviewInput. decode_reanchor_session(stdout: bytes, session_document: bytes | None, *, descriptor: PersonaReanchorInput, exit_code: int, expected_limit: int) returns immutable DecodedReanchorSession carrying the same execution metadata as DecodedSession plus corrections instead of findings.

- [x] Write RED tests for distinct bad anchors with identical text retaining separate IDs; equal originals share one ID; output has only corrections array of required request_id/file/line/side, no extra keys. Unknown/duplicate IDs, booleans as lines, invalid sides and added text/category/persona/verdict fail. Omitted corrections are allowed; anchor existence is terminal verification's responsibility.
- [x] Before production code, prove this schema through the installed actual nare parser/loop offline factory seam. Use /home/jason/Workspace/ViviDynamics/scrutare/.agents/state/nare41-capability/checkout/.venv/bin/nare. Do not introduce unsupported schema features. Record commands and output; a genuine capability gap requires coordinator escalation.
- [x] Implement immutable request/input contracts. Keep original model text as quoted canonical JSON data in the fixed correction prompt, exact system and unchanged root/tools. Never add files to review-inputs.
- [x] Factor common envelope/accounting/saved-output reconciliation privately; both typed decoders validate stream and saved payload semantically and exactly as current findings decoder. Keep existing API behavior, corruption refusal and fresh-only reconciliation intact. No arbitrary public execution callback or fake Finding encoding.
- [x] Run focused reanchor/input/protocol tests, their existing regression suites, Ruff and mypy for touched code. Record RED/GREEN and self-review, then commit one coherent task.

### Task 2: Shared execution context and correction adapter

**Files:** Modify fanout.py, nare_session.py and session_models.py; extend tests/test_fanout.py and tests/test_nare_session.py; add focused correction runtime tests as needed.

**Interfaces:** Preserve async fan_out(run_dir, config, *, runtime) -> FanOutResult. Internal _ExecutionContext owns inputs, descriptors, ledger, capability, runtime, config and run directory. async _prepare_execution(run_dir, config, *, runtime) -> _ExecutionContext; async _run_initial_wave(context) -> FanOutResult. Frozen ReanchorOutcome has execution/accounting metadata compatible with SessionOutcome and corrections: tuple[ReanchorCorrection, ...], never fake findings. async run_reanchor_session(descriptor: PersonaReanchorInput, rail: ModelRail, lease: BudgetLease, *, ledger, artifact_directory, runtime, capability) -> ReanchorOutcome. Correction wave scheduling belongs to task 3.

- [ ] Write RED tests demonstrating a shared context keeps initial usage: initial 60 of allocation 100 leaves at most 40 for a fresh correction. Exact threshold denies correction; overshoot is recorded once; malformed live usage seals later admission before terminal while active siblings still settle.
- [ ] Extract setup/initial-wave behavior without changing public fan_out output, manifest, reservation, cancellation or order. Keep fanout.json an immutable initial snapshot and retain live ledger separately. Inspect runtime once, reserve all initial leases synchronously before concurrency.
- [ ] Factor the common subprocess lifecycle privately for the two typed adapters, preserving private environment/root/tools, exact system and rail, independent pipe drain, immediate uncertainty sealing, timeout/TERM/KILL/reap/repeated cancellation and exclusive evidence writes. Schema/decoder selection is controlled by adapter purpose. Fresh baseline remains zero.
- [ ] Correction attempt uses attempt-0002, distinct fresh session key, and explicit purpose/request binding in its evidence. It does not mutate attempt-0001 or invent retry semantics. Verify prepared root before and after correction.
- [ ] Run focused existing budget/fanout/session/protocol regressions and new adapter tests, Ruff and strict mypy. Record RED/GREEN and self-review, then commit.

### Task 3: Panel strategy and exclusive final evidence

**Files:** Create engine/strategy.py, panel.py and panel_artifacts.py; narrowly extend session_artifacts.py; create tests/test_panel.py and tests/test_panel_artifacts.py; extend replay/poster compatibility tests where useful; clarify docs/SPEC.md section 8 with the user-approved missing-output policy.

**Interfaces:** async run_review(run_dir: Path, config: ReviewConfig, *, runtime: NareRuntime) -> PanelResult; StrategyNotImplementedError for iterative/debate with clear not yet implemented message. Frozen PanelResult carries status complete|partial|failed, optional verdict: Verdict, initial FanOutResult, ordered correction outcomes, verification result when computed, final usage/confidence and run path. Keep mutable context private. Exclusive producer preserves existing bare findings JSON and exact verdict bytes.

- [ ] Write RED tests for unsupported dispatch causing no preparation/model/write; one initial invocation per persona; deterministic configured order; blocking/advisory/conflict dedupe; one convergence pass regardless positive rounds.max; no escalation.
- [ ] Pin eligibility: initial failed, uncertain, not_started or output unavailable fails with artifacts and no verdict. Valid partial document, nonempty or empty, counts. No correction spending after fatal initial result. Retain siblings' evidence.
- [ ] Use shared context, build anchors from validated filtered diff, check_anchors once, group requests by original persona, reserve all correction leases synchronously in configured order and launch only admitted corrections. One correction opportunity per requested original, never another wave. Denied corrections are recorded and dropped; failed correction is fatal; valid partial correction output counts. finish_reanchor preserves source text/attribution and duplicate occurrences; dedupe and derive once.
- [ ] Tests cover successful/missing/invalid/excluded corrections, distinct-ID bindings, duplicates, no new findings, exhausted denial, shared usage totals/overshoot, uncertainty and failed correction. Revalidate input/config binding before publication.
- [ ] Preflight refuses prior sessions/fanout/panel/findings/verdict and existing posting/delivery evidence before models, including symlinks/nonregular paths. Narrowly extend private atomic no-clobber writer to exact engine destinations and exact byte output. Serialize before publishing; install findings and panel evidence before verdict last. Failure never posts and never replaces prior evidence. Test race-safe exclusive refusal and partial publication.
- [ ] panel.json records schema/app version/head/strategy/pass count, effective input/config hashes, per-stage statuses and purposes, requested originals/IDs, corrections/drop reasons and final ledger. Failed panel records diagnostics where safe, no verdict. Avoid claiming a multi-file transaction or execution authenticity.
- [ ] Real producer artifacts pass replay_run saved identity with absent posted target; existing fake-client post_review composition accepts exact artifacts and posted receipt identity. No new GitHub calls or poster flow.
- [ ] Run panel/artifact/replay/poster regression areas, Ruff and mypy. Record RED/GREEN and self-review, then commit.

### Task 4: Actual nare integration proof and documentation

**Files:** Extend tests/test_nare_cli_integration.py and its offline transport fixtures; modify README.md and docs/session-fanout.md; add docs/panel.md if warranted. Existing CI proof installs actual runtime in both Python lanes and must remain effective.

**Interfaces:** Consume run_review and the typed execution contracts from tasks 1-3. Use actual nare console/parser/loop/schema/tools/counters/session persistence with only the vendor transport factory substituted. Correction scenario selection may inspect purpose/prompt/attempt, never mutate persona system or bypass runner.

- [ ] Write RED actual integration scenarios for complete panel plus successful correction, remaining allowance, threshold-denied correction, partial initial output, missing output failure, provider failure and filtered correction tools. Exercise pipeline, not just direct fake adapter calls. Preserve network/credential/write guards.
- [ ] Implement only test-fixture capability needed for those scenarios; fix production defects through coordinator-reviewed scope, not ad hoc fixture weakening. Actual proof must validate session/artifact reconciliation, exact systems/rails/read root, no raw/excluded read, single correction opportunity and no budget reset.
- [ ] README identifies panel as default and the current programmatic engine surface truthfully, with explicit iterative/debate not yet implemented. Document partial versus missing output, one independent wave plus bounded corrections, actual after-turn limits and artifacts/replay. Keep ingestion-only CLI promise truthful; full CLI/release remains #7.
- [ ] Run all six gates in .agents/test-commands.md on both Python 3.10 and 3.14 with explicit SCRUTARE_TEST_NARE_EXECUTABLE pointing to the verified actual runtime. Actual integration proof must run without skips. No overlapping shared-venv runs. Record every command/exit/count and tested source hashes; commit then coordinator confirms committed blobs match evidence.

## Completion and review

Every task gets a separate independent spec/quality review before the next task. Preserve costed coordinator rulings and deferred findings in the SDD ledger. After all tasks, one whole-branch review, bounded fixes/scoped re-review, committed-head preflight, PR, current-head nonempty CI, final summary update and squash merge under ship-issue. Archive evidence before safe worktree removal.
