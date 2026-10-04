# Replay Audit Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: use subagent-driven-development task by task, with independent task and final reviews. Steps use checkbox syntax.

Issue #13. Goal: `scrutare replay <dir>` recomputes a stored verdict offline and reports byte identity and named finding/rule differences.
Architecture: reconstruct source evidence and captured policy, call the existing pure verdict deriver, and compare canonical bytes with the saved artifact and independently validated original posting digest. Local delivery records establish recorded provenance only. Tech stack: Python 3.10+, existing findings/config types, standard-library JSON and PyYAML. Spec: docs/SPEC.md sections 6, 7 and 9.

## Scope

In: strict artifact readers, normal and escalated reconstruction, named semantic/encoding differences, saved and posted digest comparisons, offline CLI, docs and production-artifact integration proof.
Out: model calls, network, session/strategy execution, anchor correction, path-filter re-review, changes to posting schemas or transport behavior, credentials, App identity and deployment.

## Assumptions

- User explicitly approved reading validated `verdict.json.exhaustion` for escalated replay on 2026-10-04. Recorded verdict and rule are never deciding inputs. Exhaustion is a recorded assertion, not proof of actual execution.
- `config.json` is the authoritative resolved snapshot. All current producers write it; no ambient config or embedded verdict policy fallback. Optional YAML is a redundant diagnostic, not an authority that prevents changed-input recomputation.
- Artifact-only directories can compare against saved bytes without claiming posted confirmation. Posting records, when present, are validated separately; a valid original posted digest stays independent of changed findings/config/saved bytes.

## Global Constraints

- SPEC is authority. The verdict is never model-declared. Any blocking finding requests changes, none approves; genuine recorded bound exhaustion yields escalated and never approves.
- No model and no network. Replay makes no writes, locks, subprocesses, lifecycle refreshes, client construction or ambient repository resolution.
- Preserve existing schema_version 1 canonical verdict bytes, ordered groups and every ordered source occurrence. Compatibility follows artifact format, not producer/current package version.
- Every model call goes through nare; replay makes none and does not work around nare#41.
- No new YAML knobs, suppression markers or weakened quality gates. Python 3.10 and 3.14 all six project gates before each task commit. No em dashes in authored prose or commit messages.
- Coordinator writes plan/ledger and owns reviews. Workers implement all production changes, never dispatch children, and work serially in this worktree.

## Review Focus

1. Changed source categories with stale redundant group summaries must recompute and name inconsistencies (Task 1/2).
2. Changed findings plus rewritten saved verdict must not hide a mismatch with the original posted digest (Task 3).
3. Missing or contradictory exhaustion must never fall back to an approval (Task 1/2).
4. Artifact-only and later-version bundles must work without gh, auth, metadata, diff or usable model rails (Task 2/4).
5. Uncertain review or incomplete reviewer-request delivery must remain distinct from verdict byte identity (Task 3/4).

## Shared result contract

Create `src/scrutare/replay/` with focused `models.py`, `artifacts.py`, `differences.py`, `audit.py`, `posting.py` and `__init__.py` only as their owning tasks need them. No general artifact framework.
Frozen DTOs in models.py: `AuditIssue(code: str, path: str, severity: Literal['difference','incomplete','invalid'])`; `ReplayDifference(path: str, kind: Literal['added','removed','changed','reordered','encoding'], before: object, after: object, finding: str | None = None)`; `CapturedPolicy(settings: VerdictSettings, strategy: Strategy | None, round_limit: int | None, document: Mapping[str, object])`; `SavedVerdict(raw: bytes, document: Mapping[str, object], exhaustion: Exhaustion | None)`; `PostingAudit(status: str, verdict_sha256: str | None, reviewer_request_status: str, issues: tuple[AuditIssue,...])`; `ReplayResult(verdict: Verdict | None, saved_identical: bool | None, posted_identical: bool | None, saved_sha256: str | None, posted_sha256: str | None, posting: PostingAudit, differences: tuple[ReplayDifference,...], issues: tuple[AuditIssue,...])`.
`ReplayResult.to_dict()` returns fresh deterministic JSON data with `schema_version: 1`, `status` identical/different/incomplete, `verdict`, `rule`, `recomputed_sha256`, `saved_verdict` {byte_identical,sha256}, `posted_verdict` {byte_identical,sha256,status}, `exhaustion_basis` none/recorded_assertion/unverified_recorded_assertion, `reviewer_request_status`, `differences`, `issues`. Candidate and original/saved comparisons remain separate. Exhaustion is recorded_assertion only when original posted digest matches the saved exhaustion-bearing bytes; otherwise unverified_recorded_assertion. No assertion proves genuine execution.
`ReplayResult.exit_code`: 2 if candidate/required baseline unavailable or any invalid/incomplete issue; else 1 if any false available comparison, named difference or difference issue; else 0. Artifact-only saved identity can be 0 with posted_identical null. Valid nonposted review state is an incomplete issue; merely pending request stage is informational through reviewer_request_status, not an incomplete verdict comparison. Comparison success must have an available target.
`ReplayError(ValueError)` has safe artifact/field diagnostics without untrusted values.

### Task 1: Strict artifact readers and source reconstruction

Files: create replay/models.py, artifacts.py, minimal __init__.py; tests/test_replay_artifacts.py.
Interfaces: `read_artifact(path: Path) -> bytes`; `decode_artifact(data: bytes, *, artifact: str) -> object`; `parse_findings(data: object) -> tuple[tuple[MergedFinding,...], tuple[AuditIssue,...]]`; `parse_policy(data: object) -> CapturedPolicy`; `parse_saved_verdict(data: bytes) -> SavedVerdict`; `validate_exhaustion(saved: SavedVerdict, policy: CapturedPolicy) -> Exhaustion | None`. Implement base DTOs/error now; ReplayResult serialization is Task 2.

- [x] RED: reconstruct bare ordered merged arrays from `MergedFinding.to_dict()` with mixed categories, duplicate sources, Unicode, LEFT/RIGHT, empty arrays and direct merged display whitespace differing from first source. Preserve every original text and group/source order; do not globally flatten/dedupe groups.
- [x] RED: source-owned categories/personas/reasons reconstruct even if redundant arrays are stale. Report `findings_summary_disagrees` at each mismatched field with severity difference. Require established group/source keys and types; reject unknown fields, invalid source anchors/categories, empty sources, bool line, mismatched normalized problems. Stored persona is caller-owned artifact attribution passed separately to parse_finding.
- [x] RED: strict UTF-8 JSON rejects duplicate keys, NaN/Infinity, escaped invalid Unicode, deep nesting safely, nonregular files and symlinks including dangling ones. Error names only known artifact/field paths, no raw values.
- [x] RED: policy projection requires both complete category arrays and validates with VerdictSettings. Ignore unrelated future config fields/model usability; strategy/round max optional for normal replay, but if present must have known strategy and positive actual int. Keep raw document for hash diagnostics.
- [x] RED: saved baseline schema_version exactly actual int 1 with established normal or conditional exhaustion shape. Retain raw bytes and baseline output fields for semantic comparison, even if their recorded classification differs from current policy. Unknown schema/shape is safe error. Explicit exhaustion exactly four fields constructs Exhaustion, requires literal false/exact positive bound and matches captured strategy/round limit through validate_exhaustion. Escalated status/rule without exhaustion is error, never an approval fallback. Do not infer exhaustion from those fields.
- [x] Implement focused readers/types; no orchestration, model, network or writes. Malformed values never become candidate evidence.
- [x] Focused RED/GREEN, self-review, six gates on both Python versions, commit and report exact commands/output. Coordinator independently reviews this task before Task 2.

### Task 2: Pure recomputation, named differences and artifact-only audit

Files: create replay/differences.py, audit.py; finish models.py result serialization; export replay_run/ReplayError in __init__.py; tests/test_replay.py and test_replay_differences.py.
Interfaces: consume Task 1 signatures. Produce `diff_verdicts(before: Mapping[str, object], after: Mapping[str, object]) -> tuple[ReplayDifference,...]`; `replay_run(run_dir: Path) -> ReplayResult`. Before Task 3 posting audit defaults to absent with no posted comparison; artifact-only behavior is fully useful.

- [x] RED: normal approve/changes_requested and conditional escalated replay produce exact original canonical bytes via derive_verdict, including advisory/empty escalation. Changed source category or configured partition recomputes independently of saved output and names deciding finding/source path plus old/new rule/verdict. Never source finding classifications from baseline.
- [x] RED: diff every semantic serialized field: policy, source category/reason/persona/problem, anchor, group/source ordering, blocking booleans/categories, rule/verdict/exhaustion. Deterministic paths and finding identity (anchor plus problem); all array ordering changes visible. Pure index-based detailed differences are acceptable with identity annotation; avoid heuristic rename guessing. Equal JSON but different raw bytes yields `verdict.encoding` difference.
- [x] RED: mandatory findings/config failures raise ReplayError. Missing/malformed baseline produces an incomplete report with candidate where safely possible; never approve if unavailable baseline could conceal exhaustion. A baseline read/parse failure may leave candidate null rather than guessing ordinary behavior. Exhaustion/config conflict is incomplete with no candidate.
- [x] RED: no posting/metadata/diff/YAML needed for compatible minimal bundles; producer version differences do not alter bytes. Missing policy fields never use today's defaults. to_dict deep-fresh, deterministic and JSON-safe; repeated replay leaves directory byte-identical.
- [x] Optional config.yaml: compare explicitly supplied relevant policy/strategy/bound fields only; omitted YAML fields never apply today's defaults against historical resolved JSON. Inspect relevant captured policy/strategy/bound with safe YAML parsing or existing parser, report `config_snapshots_disagree` on relevant mismatch and `config_snapshot_unreadable`/unsupported as incomplete when comparison impossible. Do not suppress config.json recomputation. Unrelated future rails do not break compatible config.json. No whole-pipeline or new review claim when paths/personas/models/budgets differ.
- [x] Implement orchestration and result/exit precedence from shared contract. Failure report preserves available comparisons/candidate. Pure verdict/differences are independently testable; no posting clients or helpers that write.
- [x] Focused RED/GREEN, six gates both versions, self-review, commit/report. No live captured producer fixtures required yet.

### Task 3: Independent local posting provenance and original digest comparisons

Files: create replay/posting.py, minimally integrate audit.py; tests/test_replay_posting.py using production ingestion/post_review/post_escalation with fake transport.
Interfaces: `inspect_posting(run_dir: Path) -> PostingAudit`. Consume Task 1 strict readers, existing pure payload/receipt/state validators where safe, Task 2 public replay_run/result. Do not modify posting transport/orchestrators or schemas. Supporting private helpers in replay/posting.py remain internal.

- [ ] RED: real current producers create offline posted approve/changes_requested and escalated COMMENT/request bundles. Disable subprocess/socket/model paths before replay; candidate bytes exactly reproduce originals, saved+posted booleans true. No credentials, network or actual model runs; fixture scope explicit.
- [ ] RED: edited source/category/config must recompute despite original hashes. Modified findings plus overwritten matching new saved verdict yields saved true, posted false. Formatting-only verdict mutation yields saved false, posted true. Named semantic diffs label saved baseline scope; original digest alone cannot recover unavailable original finding text.
- [ ] RED: inspect immutable original journal intent and original exact payload, not rebuilt candidate. Strict schema/state/attempts/deadline/failure invariants, lowercase SHA256 syntax, explicit target/pr/head/run identity, exact payload hash/canonical shape, marker, event/receipt ID/URL/body/head/state/login. Existing pure validators may be reused, never their orchestration/locks/reconciliation. No ambient PR parsing.
- [ ] RED: missing journal+payload is artifact-only; orphan/malformed/partial records preserve candidate and saved comparison but posted target null with invalid/incomplete issue. Prepared/sending/unknown/rejected never claim posted and get incomplete issue. Missing historical diff/metadata does not prevent digest comparison; if present report mismatched raw diff hash or capture identity as named diagnostics. Config effective canonical hash disagreement is difference, not candidate suppression.
- [ ] RED: optional escalation request pair validates against original posting intent/confirmed receipt and original normalized request targets, not changed current config. Absent both is legitimate lower-level escalated review; pending request stage can coexist with proven posted-verdict bytes. Orphan/malformed/conflicting links invalidate posting provenance. Validate request hash, review receipt hash, request state/receipt/provenance; observed_requested never claims causal ownership. No request resend/reconcile.
- [ ] RED: no integrity issue can promote malformed/nonposted record to positive original-posted proof. Saved exhaustion matching original posted digest changes exhaustion_basis to recorded_assertion; changed assertion remains explicitly unverified even when structurally valid. Neither proves execution.
- [ ] Implement focused inspector and audit integration without rewriting history or current artifacts. Use original immutable intent for cross-stage links. Relevant failure messages safe.
- [ ] Focused RED/GREEN, six gates both versions, self-review, commit/report. Coordinator reviews exact offline producer integration evidence.

### Task 4: Offline CLI and public documentation

Files: interfaces/cli.py, tests/test_replay_cli.py and existing CLI tests as needed; docs/replay.md, README.md, docs/posting.md and escalation.md references where needed; docs/SPEC.md section 9 only for the maintainer-approved recorded-exhaustion clarification.
Interfaces: `scrutare replay <dir>` dispatches to replay_run before accessing review args/config or resolving PR. Emit deterministic one-object JSON stdout using result.to_dict(), return result.exit_code. ReplayError safe stderr and exit 2; usage remains argparse exit 2. Existing review ingestion behavior unchanged.

- [ ] RED: normal and escalated CLI matches Python result, identical exit0, changed input/encoding exit1 with named finding/rule differences, missing/invalid/unsupported input exit2 without traceback. Argument errors, module and console invocation, directory with spaces/untrusted control text safely JSON-escaped.
- [ ] RED: blocked subprocess/socket/model/client/ambient repo lookup paths prove replay offline with no gh/auth/CWD config. Read-only filesystem snapshots before/after identical; unrelated extra artifacts and future producer version accepted. Artifact-only saved identity labels posted unavailable. Request delivery status distinct from verdict byte identity.
- [ ] Implement minimal parser dispatch, update module/main docstrings truthfully. No new full review execution or YAML flags. Record the maintainer-approved exhaustion exception in SPEC section 9 without changing any other scope. Document required structural artifacts, source/policy authority, approved exhaustion exception, saved vs locally recorded posted comparisons, trust boundary, unsupported formats, all exits, no anchor/path/model re-review or execution proof.
- [ ] Update published statements that replay is unimplemented; retain honest live review limitations. No em dashes. Existing installed wheel smoke stays unchanged; controller separately runs installed offline replay proof if necessary.
- [ ] Focused RED/GREEN, six gates both versions, self-review, commit/report. Final whole-branch review after clean task gate; coordinator runs concrete captured-artifact proof, preflight both versions before push, current-head CI and squash merge.
