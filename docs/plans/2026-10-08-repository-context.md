# Supply bounded immutable repository context

Issue #44

## Scope
In: opt-in exact base/head regular text, explicit related paths, deterministic bounds and
omissions, shared verified read roots, replay audit, iterative dependency invalidation.
Out: automatic language-specific caller discovery; paid comparative evaluation awaits
#43's evaluator and an explicit model evaluation job. No accuracy claim accompanies shipping.

## Assumptions
- Default diff-only artifacts remain byte-compatible.
- Context rules are independent of github.paths; findings remain in selected diff hunks.
- Changed identities precede sorted explicit related paths under whole-file size limits.
- All exposed context is conservatively recorded as each finding's supporting dependency.
- GitHub object reads use validated exact SHAs and captured repository identities, including forks.
- Symlinks and submodules are omitted, executable text is copied without execute permissions.

## Tasks
- [x] 1. Configuration and Git object transport: failing opt-in/unsafe SHA/identity tests.
- [x] 2. Bounded context capture: cross-file fixture, explicit omissions and fork routing tests.
- [x] 3. Shared prepared roots: immutable bytes, tampering/symlink and prompt tests.
- [x] 4. Iterative/replay integration: unchanged hunk supporting-file changes and audit tests.
- [x] 5. Installed runtime integration and documentation: real nare reads outside checkout.

## Validation
Run targeted tests after each red/green cycle, then lint and strict types. Parent handles
independent review, both mandatory full Python/native Docker lanes, and shipping gates.

## Independent review corrections
- Nested credential directories use the same denial policy in capture and preparation.
- Same-head context identity is checked regardless of fresh human contests.
- Partial dependency reassessment persists stale status and retries within the round bound;
  exhaustion escalates without dropping carried blocking findings or claiming completion.
