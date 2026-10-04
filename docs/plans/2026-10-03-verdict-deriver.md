# Verdict deriver: code-derived verdict

Issue #5

## Scope

In: A pure verdict function over verified, deduped findings and the configured category partition; deterministic lossless verdict serialization; atomic verdict.json persistence and developer documentation.
Out: Session collection, strategy execution, posting, escalation, and the replay CLI. Their tickets consume this API. Replay-compatible bytes are delivered here; the command belongs to #13.

## Assumptions

- The findings pipeline supplies MergedFinding values after anchor verification. A group blocks when any source category is configured as blocking.
- Verdict status and rule are computed properties of immutable evidence and policy, never caller or model declarations.
- Canonical JSON has schema_version 1, no clock or random fields, ordered evidence, and a complete category policy. Existing run metadata supplies the producer version.

## Global Constraints

- SPEC sections 2, 5, 6, 9, and 11 bind. Exactly the config.CATEGORIES vocabulary is partitioned into blocking and advisory categories. Advisory-only findings never block.
- Any blocking finding yields changes_requested; otherwise approve, including the empty set. Models own neither status nor classification policy.
- Preserve every merged finding and every source observation, including persona, category, reason, original problem, anchor and side. Maintain caller finding/source order.
- Pure derivation and serialization perform no network, subprocess, model, filesystem, clock or random operations. Immutable typed results and fresh JSON-compatible dictionaries.
- Reuse category validation and authority. Validate programmatic settings as well as YAML input without duplicating the partition rule or weakening existing diagnostics.
- No config surface additions, suppressions, or em dashes in published prose. Match existing Python style and strict typing. Use TDD and verify all six project gates on Python 3.10 and 3.14 before committing task work.

## Tasks

### Task 1: Pure verdict and canonical serialization

**Files:** src/scrutare/findings/verdict.py, findings/__init__.py, config.py, tests/test_verdict.py, relevant config tests.
**Interfaces:** derive_verdict(findings: Iterable[MergedFinding], config: VerdictSettings) -> Verdict. Frozen Verdict stores findings as a tuple and config as VerdictSettings; verdict and rule properties derive from the evidence. to_dict() returns fresh artifact data; to_bytes() returns deterministic UTF-8 JSON, sorted keys, indent 2, trailing newline. Invalid typed input raises FindingError; invalid category policy raises ConfigError.

- [x] RED: tests prove empty and advisory-only sets approve, each blocking category requests changes, mixed input and mixed-category duplicate groups cannot lose blocking evidence, and reclassification follows config rather than default category names.
- [x] Validate Verdict direct construction and derive inputs. Centralize the complete disjoint fixed-category partition rule in VerdictSettings validation, with immutable tuples, duplicate and unknown-category rejection, preserving YAML field diagnostics. Canonicalize category order to CATEGORIES.
- [x] Artifact keys: schema_version (1), verdict (approve or changes_requested), rule (any_blocking_finding or no_blocking_findings), config (blocking_categories and advisory_categories arrays), findings (each MergedFinding.to_dict(), extended with blocking bool and blocking_categories array). Every finding remains counted and attributed; arrays follow authority/input order. No arbitrary status input.
- [x] Test lossless source serialization, repeated byte equality, independently derived equal inputs, caller list mutation isolation, fresh to_dict mutation isolation, and invalid policies/types. No timestamps or package-version-dependent fields.
- [x] Focused red/green, then full tests, Ruff, strict mypy, wheel and installed smoke on both supported CI Python versions. Commit and report exact evidence.

### Task 2: Atomic persistence and documented pipeline example

**Files:** src/scrutare/findings/artifacts.py, findings/__init__.py, tests/test_verdict_artifacts.py, docs/findings.md.
**Interfaces:** write_verdict(run_dir: Path, verdict: Verdict) -> Path; VerdictArtifactError for safe actionable persistence failures. Consume Task 1 Verdict.to_bytes() without reimplementing serialization.

- [x] RED: writing into an existing run directory produces verdict.json with exact canonical bytes, every persona/source and rule; repeat writes are byte-identical and a changed verdict replaces the old artifact.
- [x] Use an atomic temporary sibling plus replacement. Missing directory or write/replace failures raise safe diagnostics, clean temporary files, and preserve prior verdict and unrelated artifacts. Do not create a new run or network/subprocess calls. Validate the typed verdict before starting writes.
- [x] Document derivation and persistence alongside the existing findings pipeline, config reclassification, rule names and artifact shape. Clearly state that this API accepts verified survivors and replay CLI comes in #13. Include a runnable example and execute it against the implementation.
- [x] Focused red/green plus full tests, Ruff, strict mypy, wheel and installed smoke on both supported CI Python versions. Commit and report exact evidence.
