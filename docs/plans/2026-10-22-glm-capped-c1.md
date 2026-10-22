# Qualify onprem/glm-5.3-flash under a new gate with a per-request output cap

Issue #82

## Scope
In: the cap's live smoke; a new gate frozen from `s2`'s profile and #51's
criteria plus `max_output_tokens`, sized from `s2`'s retained per-request
completion tokens; schedule `c1`; publication with public reproduction against
`s2`, `s1`, `g2` and `gpt-6.1-sol`.
Out: product default changes, promotion, the App and infrastructure, the holdout,
human judgments, calls to any other model, and the re-test `c2` (maintainer
decision only).

## Assumptions
- The cap must sit above every request of a completed `s2` session with headroom,
  and end a repeat of `s2`'s runaway request inside the unchanged timeout.
- Citation validation is unchanged. A citation past a file's end fails the cell.
- Launch only in a 30-minute idle window, unless the maintainer rules otherwise.
  The maintainer did, after 6 hours, and the waiver is a separate dated, posted
  file, so no frozen file changed.

## Tasks
- [x] 1. Live smoke of the released cap; size the cap from `s2`; freeze; post on #51.
- [x] 2. Wait for an idle window; record the waiver; re-check fallbacks; launch detached.
- [x] 3. Record concurrent load; verify spend; run the unchanged checker; diagnose every non-complete session.
- [x] 4. Publish with reproduce.py and a package test that refuses changed evidence.
