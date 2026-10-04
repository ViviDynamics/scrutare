# Path filters implementation plan

Issue #9. Spec: docs/SPEC.md. Use subagent-driven-development per task, then independent whole-branch review and ship-issue.

## Scope

In: Production filtered reviewer-input boundary, integrated into real ingestion and CLI capture; effective-file manifest; shared path/section parsing; public per-persona read-root contract; actual nare CLI offline compatibility evidence; documentation.
Out: Launching live persona sessions, budgets, strategies or full CLI review execution, owned by blocked #3/#10/#7. No post-hoc findings suppression, model calls, credentials/App/deployment work or new YAML settings.

Architecture: Preserve complete raw capture for audit/posting, but create a separate copied review-inputs directory containing only selected diff sections, minimal file records and safe metadata. All persona input descriptors enforce nare's existing --tools read and --root on that directory. Actual CLI capability probe in .agents/state/9-design-audit.md confirms the policy, including path/symlink escapes and disallowed tools.

## Assumptions and rulings

- Component acceptance is independent of live session execution, like #6's poster API. The producer is integrated into capture and the descriptor covers every resolved persona; #3 must consume it for initial and re-anchor sessions. Cost if wrong: add missing caller integration to #3 before claiming full reviews, rather than weaken filters.
- Glob dialect: POSIX, case sensitive; slashless patterns match any basename; slash-containing patterns match whole repository path. * and ? do not cross slash, ** as a complete segment matches zero or more segments. Character classes use fnmatchcase segment semantics. No negation or .gitignore implicit rules. Default *.md excludes nested Markdown too. Cost if wrong: explicitly migrate documented dialect with regression coverage.
- Include matches the current/canonical API filename. Exclude wins for canonical and previous rename/copy names, removing the whole change. Deletions use old filename. Cost if wrong: amend rename selection contract without leaking excluded old-side patches.
- Minimal reviewer view omits all discussion bodies and PR title/body; raw capture retains them. Included source may naturally mention an excluded name; this feature is file visibility, not semantic prose redaction. Cost if wrong: add explicitly scoped context projection in a later change rather than place raw discussion in the read root.

## Global Constraints

- SPEC sections 2,5,8,11 are authority. Fixed existing config only. Excluded file content must be removed before any persona visibility. Empty selection never falls back to full inputs.
- Preserve coherent full raw capture and complete raw file count validation before filtering. No links from reviewer root to raw evidence, checkout, config credentials or session output.
- Nare already confines read using resolved paths; use only read tool, never bash. Do not implement a substitute for missing nare budget capabilities.
- Reuse one Git path parser for anchoring and section membership. parse_diff retains its existing external anchor-set contract. Preserve selected section bytes including line endings/no-newline markers. Ambiguous/inconsistent section-file mappings fail closed.
- Typed frozen descriptors are application contracts, not protection against arbitrary host mutation or deliberate object bypass. No claims of OS shell sandbox or live model execution.
- Python3.10/3.14, existing house style, no em dashes in authored prose, no suppressions/quality weakening. TDD each task, full six project gates both versions before task commit. Coordinator does no production implementation.

## Review Focus

Malformed/spaced/quoted Git paths must not leak excluded sections; rename/copy exclusion covers both identities; binary/mode-only changes remain in effective manifest; direct legacy ingestion remains explicitly raw-only; empty filtered views and tampered reviewer directories fail safely without broadening root. Tests belong to the task implementing each boundary.

## Tasks

### Task 1: Shared diff sections and pure path selection

Files: findings/anchors.py and exports or focused shared parser module; engine/paths.py; tests/test_diff_sections.py and test_path_filters.py; existing anchor tests when needed.
Interfaces: frozen DiffSection(old_path: str|None, new_path: str|None, data: bytes, anchors: frozenset[Anchor]); property file returns new_path or old_path. parse_diff_sections(diff: bytes|str)->tuple[DiffSection,...]; parse_diff(diff) unions section anchors and keeps its current contract. Frozen ChangedFile(filename:str,status:str,previous_filename:str|None=None); parse_changed_files(data:object)->tuple[ChangedFile,...]; select_changed_files(files:tuple[ChangedFile,...], paths:PathSettings)->tuple[ChangedFile,...]. File records use repository-relative validated paths; reuse existing path validation, do not duplicate its rules. Validate nonempty known GitHub statuses added/removed/modified/renamed/copied/changed/unchanged, optional previous name, duplicate filenames, typed direct constructors and settings before work.

- [x] RED: default docs/** and *.md exclude at every depth, nonempty include allowlist, exclusion wins, empty include/exclude, ** zero/many directories, *, ?, classes, Unicode/case, metacharacter escaping via classes and leading ! treated literally, rename/copy/deletion, invalid paths/settings.
- [x] RED: sections retain exact bytes and correct old/new identity for spaced/quoted names, additions/deletions, renames/copies, binary/mode-only/rename-only and no-newline markers. Empty diff allowed; malformed/ambiguous sections rejected. Anchors unchanged for all existing fixtures.
- [x] Refactor existing parser to expose section data/identity without reparsing filename syntax in engine. No naive split on spaces, no deriving effective files solely from anchors. Preserve original input order and immutable fresh data.
- [x] Focused RED/GREEN, six gates both versions, self-review, commit, exact report. No input writer or persona/session launch in this task.

### Task 2: Prepared filtered artifacts integrated into capture

Files: engine/review_inputs.py, ingestion.py, CLI, focused engine exports as needed; tests/test_review_inputs.py, ingestion/CLI tests.
Interfaces: frozen PreparedReviewInputs(root:Path, head_sha:str, effective_files:tuple[str,...]); prepare_review_inputs(run_dir:Path, config:ReviewConfig)->PreparedReviewInputs. Extend ingest_pr keyword-only review_config:ReviewConfig|None=None. CLI passes its already parsed config; when supplied, preparation occurs inside fresh-run cleanup before return. Legacy no-review_config callers retain raw-only behavior and cannot be represented as a prepared root.
Artifacts: effective-files.json schema_version1, head_sha, include/exclude arrays, files ordered canonical names, config_sha256 digest of deterministic normalized ReviewConfig bytes outside the reviewer root. review-inputs/diff.patch selected exact sections; files.json only filename/status (no arbitrary GitHub fields, URLs or previous excluded aliases); context.json repository, pr_number, head_sha, base_sha only. No discussion body, full metadata/config/raw capture inside root.

- [x] RED: real coherent capture produces effective manifest and filtered root before CLI success, raw bytes remain exact, complete raw file count checked first, preparation failure cleans fresh run, legacy capture remains compatible. Update previously unrealistic CLI fixtures to valid Git patches only where prepared path now requires it; never weaken their assertions.
- [x] Validate capture schema/target/head and coherent config snapshots against supplied ReviewConfig with safe local errors before projection, no network or ambient repo inference. Cross-check complete parsed sections against ChangedFile records, current/previous identities as applicable. Every captured file maps one section, including binary/mode-only; reject mismatch/duplicates rather than broadening input.
- [x] Select through Task1 API, persist manifest even empty, copy only selected section bytes/minimal file data. Descriptor root resolves to review-inputs, never run root. First preparation creates fresh directory; repeat only returns if exact expected file set/bytes/manifest still agree. Reject symlinks, extra files, changed bytes/config/head; do not overwrite started view or silently widen selection. Standalone failure removes only artifacts it just created; never deletes preexisting audit data.
- [x] Tests cover hostile arbitrary GitHub fields, omitted raw discussions, empty view, exclusion renames, config mismatch, artifact tampering/symlinks, exact selected anchors and preserved raw evidence. Preparation has no model/nare imports or process launch. API safe errors name the operation without raw untrusted diagnostics.
- [x] Focused RED/GREEN, six gates both versions, self-review, commit and exact report.

### Task 3: All-persona confined input descriptors and compatibility/docs

Files: engine/review_inputs.py or focused engine/persona_inputs.py and exports; tests/test_persona_inputs.py; docs/path-filters.md, README and config reference as needed. Actual compatibility script/evidence remains ignored .agents/state, not a sibling-checkout dependency in CI.
Interfaces: frozen PersonaReviewInput(persona:PersonaDefinition,inputs:PreparedReviewInputs); generated prompt references only visible artifacts. prepare_persona_inputs(inputs:PreparedReviewInputs, entries:Iterable[str|PersonaDefinition])->tuple[PersonaReviewInput,...] resolves actual registry. nare_input_args()->tuple[str,...] supplies positional prompt plus exactly --tools read --root <resolved prepared root>, no caller overrides. Runner later owns --system/model/budget/session flags and must reject conflicting tool/root arguments. Do not launch sessions or invent budget flag.

- [x] RED: all four actual built-ins plus custom resolved persona share filtered root, prompt and fixed read policy; no raw diff/files/discussion sentinel in descriptors or visible artifacts, empty selection stays empty. Typed direct constructors reject arbitrary raw run roots or malformed descriptors using prepared-view validation, while acknowledging no arbitrary-host sandbox.
- [x] Execute actual nare CLI offline compatibility using real production prepare_review_inputs and descriptors, replacing the design probe's handbuilt fixture. Reuse nare FakeProvider seam with no vendor calls: allowed filtered reads pass; raw sibling/absolute reads and symlink escape denied; bash/write denied; schemas onlyread; excluded sentinel absent in all transportrequests. Actual builtin system prompts and custom prompt passed through --system. Record exact commands/outcomes outside CI. Retain full-run-root negative control. No cumulative budget workaround, no live session claim.
- [x] Document glob dialect, rename exclusion, manifest/view format, API and raw-only legacy boundary. Document omitted discussion bodies and source-reference limitation. README/CLI accurately say capture now prepares filtered inputs but live review pipeline remains unfinished. Explicit integration requirement for #3 initial and re-anchor sessions to consume descriptor without widening root/tools; record as follow-up in existing issue body if needed after ship, not new duplicate issue.
- [x] Focused RED/GREEN, six gates both versions, compatibility probe, self-review, commit and exact report.
