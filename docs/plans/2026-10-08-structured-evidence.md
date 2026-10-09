# Versioned structured evidence

Issue #46

## Scope
In: opt-in immutable v2 findings, mechanically checked pinned citations, caller IDs,
source-preserving strategy persistence, strict version-aware replay and safe posting.
Out: semantic truth adjudication and empirical quality promotion, pending evaluation.

## Assumptions
- `findings.evidence: v2` requires repository context; omitted configuration is legacy.
- Unsupported citations fail the session and retain raw assertions with a fixed mechanical reason.
- Caller IDs derive from capture session identity and occurrence, never model output.
- Legacy serialization remains byte-compatible; mixed versions are rejected.

## Tasks
- [x] 1. Frozen evidence and strict parsers: malformed fields, revisions, paths and ranges fail.
- [x] 2. Opt-in config/schema and production validation: captured bytes bind citation status.
- [x] 3. Source preservation through reanchor, dedupe, debate, iterative and replay.
- [x] 4. Safe evidence rendering, documentation and focused verification.

## Validation
- Test-first contract failures observed before each implementation task.
- 1,075 focused legacy/lifecycle regression checks passed; 23 dedicated contract checks passed.
- Installed wheel + external nare 2026.10.4: all 9 legacy/v2/invalid cases across
  panel/debate/iterative passed, including distinct equal sources, ID preservation,
  iterative reuse, changed-revision recitation, retained history and offline replay.
- Ruff and strict mypy passed. Parent owns final Python 3.10/3.14/native Docker lanes.
- These are contract/integration checks; comparative quality remains unmeasured.

## Review fixes
- Rebased onto pinned #44 core `bc1b7c9` to retain strict manifest, sensitive-path,
  same-head integrity and stale-dependency safeguards. One completed dependency rebase.
- Two observed red regressions covered changed revisions with partial refresh and
  exhausted rounds. Historical citations now remain in the pool, with no current
  approval; same-head completion can withdraw them and exhaustion escalates.
- 176 lifecycle/context/panel/debate/replay regression checks passed after this fix;
  strict mypy passed. Fresh independent review and final full gates remain pending.
