# Re-qualify onprem/glm-5.3-flash with schema-constrained output

Issue #78

## Scope
In: one frozen 36-cell schedule (`s1`) of `onprem/glm-5.3-flash` on `g2`'s profile and
gate, with `structured_output: json_schema`, nare 2026.10.12 and the released #78
source (2026.10.26). The no-paid-fallback check before launch and the spend check
after. Publication with public reproduction, and a side-by-side against `g2` and
`gpt-6.1-sol`.
Out: product default changes, promotion of the model, the holdout, human
judgments, calls to any other model, any access to the serving host or cluster,
and the further schedule `s2` (a maintainer decision).

## Assumptions
- Change only the constraint, the nare pin and the source. Every gate criterion is
  byte-identical to `g2`'s, and the timeout stays 600 s.
- The constraint applies only on nare's tool-free schema correction turn
  (maintainer ruling, 2026-10-20).
- Launch detached in a systemd user unit, as `g2` was, after re-reading the router.
  Record concurrent load before and during the run.
- Publish the frozen `preparation.json` byte-identical, keeping its hash posted on #78.

## Tasks
- [x] 1. Freeze the gate, inputs and wheel from the released commit; post hashes on #78 before launch.
- [x] 2. Re-read the router (no fallbacks), record load, and launch `s1` detached.
- [x] 3. Verify spend, then run the unchanged checker and export the sanitized evidence.
- [x] 4. Diagnose every non-complete session by cause, and count constrained turns.
- [x] 5. Publish with reproduce.py and a package test that refuses changed evidence.
