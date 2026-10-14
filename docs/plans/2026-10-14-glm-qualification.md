# Qualify the self-hosted spark/glm-5.3-flash

Issue #51

## Scope
In: one frozen 36-cell qualification schedule of `spark/glm-5.3-flash` through the
unchanged evaluator, on the profile that qualified `gpt-6.1-sol`; the no-paid-fallback
and model-limit evidence gathered before the first call; publication of every
schedule (interrupted, withdrawn and judged) with public reproduction.
Out: product default changes, prompt or schema changes, the holdout, human judgments,
calls to any other model, any access to the serving host or cluster.

## Assumptions
- Change only what the model forces: the alias, and the per-session timeout
  (120 s to 600 s) after a neutral load probe measured 15 to 19 output tokens/s per
  stream at the schedule's 12-stream peak. Concurrency stays 3 (engine `max_num_seqs` 16).
- Use current `main`. When #76 merged mid-run, the replacement schedule was frozen on
  it with its opt-ins absent.
- A cell that needed an upstream-5xx rerun counts as an accounting failure.
- At most one further frozen schedule, and only for a failure a documented profile change
  addresses. An interrupted schedule is not judged and does not use that allowance.
- The pilot keyword rules were merged before any call of this model, so the automated
  decisions here are prospective.

## Tasks
- [x] 1. Confirm no router fallback can reach a paid model; record limits and a streaming tool-call probe.
- [x] 2. Freeze source, wheel, inputs, schedule and gate; post hashes on #51 before launch.
- [x] 3. Run; retain the interrupted g1; diagnose its invalid cell; re-freeze and run g2 detached.
- [x] 4. Publish with reproduce.py and a package test that refuses changed evidence.
