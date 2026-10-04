# Findings pipeline: anchors, one re-anchor round, dedupe

Issue #4

## Scope

In: Immutable validated findings, a side-aware unified diff anchor index, one explicit correction round, dropping unresolved anchors, and deterministic lossless dedupe.
Out: Nare collection and re-anchor model calls, verdict derivation, posting, CLI review execution. These are later stages; #3 is blocked by nare#41, but this pure pipeline is independent.

## Assumptions

- A re-anchor request is returned as data; the caller supplies one correction round. The pipeline performs no network or model calls.
- Add optional LEFT/RIGHT side information with RIGHT as the default, so removed lines can be represented without guessing which side a line refers to.
- Dedupe uses exact anchor plus whitespace-normalized problem text. It preserves every source category, reason, and persona for later code-owned classification, avoiding loss of a blocking category when another perspective supplies an advisory duplicate.

## Global Constraints

- SPEC sections 2, 5, 6, and 8 bind. Categories are exactly config.CATEGORIES. No model-declared approval or severity fields are accepted.
- Only lines actually present within unified diff hunks are valid. RIGHT includes additions/context; LEFT includes deletions/context. Binary changes and unchanged files have no anchors.
- Paths are repository-relative, and comparisons are exact after Git patch filename decoding. A modified or renamed file uses its new path on both sides; deleted files use their old path.
- Plain malformed findings, booleans as line numbers, unknown categories, invalid sides, unsafe paths, and extra wire fields fail with safe actionable field diagnostics.
- Malformed or unsupported textual diff structure fails closed, rather than accepting anchors whose line counts do not match hunk headers. Binary and metadata-only changes may contribute no anchors.
- Pure functions have no I/O, subprocesses, model imports, clocks, randomness, or mutable global state. Preserve deterministic input order and immutable outputs. No config surface changes or em dashes.

## Tasks

### Task 1: Finding contract and side-aware diff anchors

**Files:** src/scrutare/findings/__init__.py, models.py, anchors.py, tests/test_findings_models.py, tests/test_diff_anchors.py.
**Interfaces:** frozen Anchor(file: str, line: int, side: Literal["LEFT", "RIGHT"] = "RIGHT"); frozen Finding(anchor: Anchor, category: Category, problem: str, reason: str, persona: str); FindingError(ValueError); parse_finding(data: Mapping[str, object], *, persona: str) -> Finding; parse_diff(diff: bytes | str) -> frozenset[Anchor].

- [x] Write failing tests for immutable validated finding types, valid categories, explicit or default side, safe wire parsing with caller-owned persona attribution, extra fields and invalid values. The wire fields are file, line, optional side, category, problem, reason; persona is never model-owned.
- [x] Parse GitHub-style unified diffs into anchors, including multiple files/hunks, context/additions/deletions, zero-count ranges, no-newline markers, CRLF, new/deleted files, renames, spaces, and Git-quoted UTF-8/escaped filenames. Test LEFT and RIGHT independently.
- [x] Verify hunk counts and reject malformed headers/truncated/overlong hunks, invalid paths, and unsupported combined diffs. Ignore valid binary/metadata-only sections as unanchorable. Do not infer anchors from filenames or whole-file contents.
- [x] Keep category authority in config.CATEGORIES without duplicating it. Validate direct constructors as well as parse_finding so downstream consumers cannot receive invalid typed objects accidentally. No findings output authority fields.
- [x] Run focused red/green, full tests, Ruff, strict mypy, wheel, installed smoke on both Python versions. Commit task files and report evidence.

### Task 2: Pure verification and one correction round

**Files:** src/scrutare/findings/verification.py, __init__.py, tests/test_anchor_verification.py.
**Interfaces:** frozen AnchorCheck stores captured anchor index, accepted findings and reanchor_requests; check_anchors(findings: Iterable[Finding], anchors: frozenset[Anchor]) -> AnchorCheck. Frozen ReanchorCorrection(original: Finding, anchor: Anchor). finish_reanchor(check: AnchorCheck, corrections: Iterable[ReanchorCorrection] = ()) -> VerificationResult with accepted findings and dropped entries/reasons.

- [x] Write failing tests for missing files/lines and side mismatches producing requests, valid anchors surviving, one valid supplied correction surviving with original category/problem/reason/persona unchanged, and missing or still-invalid corrections being dropped.
- [x] Corrections may only name a requested original and change its anchor. Reject corrections for already accepted/unrequested findings and repeated corrections for one original. Identical duplicate originals need only one request/correction and must not create an unbounded retry opportunity.
- [x] Capture the immutable original anchor index in AnchorCheck so correction cannot accidentally validate against a different diff. Finalization returns a terminal result with no further re-anchor requests. No callback or model calls inside either function.
- [x] Preserve caller iteration order deterministically and return auditable dropped reasons without unsafe source values. Run focused red/green and full gates both versions, then commit and report.

### Task 3: Lossless dedupe and pipeline documentation

**Files:** src/scrutare/findings/dedupe.py, __init__.py, tests/test_findings_dedupe.py, docs/findings.md, README.md.
**Interfaces:** frozen MergedFinding(anchor, problem, sources: tuple[Finding, ...]) exposes categories in CATEGORIES order, personas and reasons in first-seen order; dedupe_findings(findings: Iterable[Finding]) -> tuple[MergedFinding, ...].

- [x] Write failing tests that same anchor and normalized problem across perspectives collapse while all categories/reasons/personas survive. Different file/line/side/problem remain distinct; category conflicts cannot erase a blocking classification. Preserve first problem text and group order, support generators and empty input, and ensure immutable results.
- [x] Use deterministic whitespace normalization only, with no semantic/model or fuzzy merging. Preserve source evidence rather than choosing a winning category or reason. Expose a deterministic to_dict representation for later artifacts with validated fields, categories, attribution, reasons and sources.
- [x] Document wire input fields, side semantics, supplied correction round, terminal dropping, and pure verify-then-dedupe composition with a runnable example. Explain model/session orchestration remains outside this issue. Link README to the guide.
- [x] Execute documentation examples, focused red/green tests and all existing gates on both Python versions. Commit and run committed-head ship-issue preflight for both versions; controller handles independent review, PR, CI, clean branch review, updated summary, squash merge, and closure readback.
