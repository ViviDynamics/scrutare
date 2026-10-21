# Run the final constrained schedule s2 of spark/glm-5.3-flash

Issue #51

## Scope
In: the one further frozen schedule `s1`'s gate allowed, with the budgets and
timeout sized from `s1`'s data. Freeze hashes and every deviation posted on #51
before launch. Publication with public reproduction against `s1`, `g2` and
`gpt-6.1-sol`.
Out: product default changes, promotion, the App and infrastructure, the holdout,
human judgments, calls to any other model, and any further schedule.

## Assumptions
- Prefer larger grants to the overrun fallback. Raise the ceiling, and the gate's
  per-attempt bound with it, so the grants fit.
- Use a session timeout no longer than any upstream limit (LiteLLM, WAF, vLLM, nare).
- Launch in an idle window, unless the maintainer rules otherwise. They did, and
  the waiver is a dated, posted amendment.

## Tasks
- [x] 1. Size grants and timeout from `s1`; read the upstream timeouts; freeze; post on #51.
- [x] 2. Wait for an idle window; record the waiver; re-check fallbacks; launch detached.
- [x] 3. Verify spend; run the unchanged checker; export; diagnose every non-complete session.
- [x] 4. Publish with reproduce.py and a package test that refuses changed evidence.
