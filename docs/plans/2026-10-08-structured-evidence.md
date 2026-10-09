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
- [ ] 4. Safe evidence rendering, documentation and focused verification.
