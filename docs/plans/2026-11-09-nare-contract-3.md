# Nare contract 3 integration

## Goal and design

Run Scrutare against released nare 2026.11.80 while retaining the subprocess
boundary, Python 3.10 support, read-only review, legacy replay and disjoint
all-attempt usage accounting. Contract 3 changes redaction rules; callers must
accept it explicitly rather than reinterpret old sessions. Keep contracts 1/2
for their historical releases. Contract 3 first shipped in nare 2026.11.33.

A pin-only update fails capability inspection. Replacing the subprocess adapter
with the Python API would couple interpreter requirements and expand this change.
Extend the existing adapter instead. Explicitly set new-runtime request retries
and cutoff corrections to zero and schema corrections to one, retaining existing
Scrutare recovery policy. New features such as read gates remain separate work.
No model calls, holdout inspection, quality promotion or deployment is included.

## Task 1: Runtime compatibility

Files: engine/nare_session.py, engine/session_output.py and capability/output tests.
Interface: inspect_nare_runtime returns NareCapability; decode_session returns
validated findings and cumulative usage from matching stream/session records.

- [ ] Add failing tests accepting contract 3 from its release boundary, refusing
  mismatched/future contracts, decoding contract 3 and validating additive totals.
- [ ] Run focused tests and confirm failures expose missing contract support.
- [ ] Extend negotiation and decoding, preserving contracts 1/2; explicitly bound
  new runtime controls for initial and resumed correction invocations.
- [ ] Run focused tests and installed correction/cutoff/accounting/replay proofs.

## Task 2: Release pin and full verification

Files: Dockerfile, CI/release workflows, smoke scripts, installed tests and guides.
Interface: every installation uses release 2026.11.80 at commit
4bca6d716cb3c5de37c0cbf9316aa063fafb570e in a separate Python 3.14 environment.

- [ ] Update release pin and installed test expectations, retaining historical
  minimum-version documentation where it describes an older capability.
- [ ] Test installed contract 3 without credentials/network and exercise real
  parser, tools, schema, cumulative corrections, posting and offline replay.
- [ ] Run full tests including native Docker, lint, strict types, wheel and
  installed CLI smoke sequentially on Python 3.10 then Python 3.14.
- [ ] Obtain independent branch review; fix substantive findings with tests.
- [ ] Run disclosure guard with private terms present, push feature branch,
  create and link PR with exact verification results and remaining limitations.

## Review focus

Unknown contracts must fail closed. Legacy artifacts must remain replayable.
Cutoff recovery must not silently add model turns. Resumed correction deltas must
not double-count usage. Native transport tests must remain offline and credential
free. An additive usage.total must agree with the disjoint counters when present.
