# Failure paths implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development task by task. The coordinator owns integration and independent review.

**Issue:** #12
**Goal:** Prove that incomplete persona coverage cannot escape the installed CLI as a clean review, while retaining intentional valid partial success.
**Architecture:** Keep the delivered capture, nare executor, panel, posting and replay implementations. Extend the installed-wheel boundary proof through the existing offline vendor factory and gh executable fixtures. No new runtime behavior or failure schema is planned.
**Tech stack:** Python 3.10/3.14, pytest, uv, pinned external nare 2026.10.4.
**Spec:** [docs/SPEC.md](../SPEC.md), sections 5, 8 and 11.

## Scope and approved interpretation

The user explicitly approved following SPEC: no initial retries; #12 proves CLI failure propagation; #14 wires and verifies the reusable Action. A failed or unavailable required initial persona is fatal. A validated partial document counts, including an explicit empty findings array. Missing output and uncertain accounting cannot imply approval. Prior issues already delivered these behaviors; this change strengthens their installed boundary proof and operator guidance.

## Global Constraints

- The panel does not retry initial sessions.
- Actual installed CLI tests replace only the vendor factory with an offline transport, retaining the real parser, loop, tools, schema, accounting and persistence.
- A nare budget stop or reported usage strictly above the invocation allocation marks the persona partial, even if nare reports done with exit 0.
- Missing or corrupt accounting fails safely and closes admission; it cannot imply an empty review.
- A force-push during a run: the run reviewed the captured head SHA and says so.
- Raw captures and configuration bytes, bare findings arrays, and canonical schema-1 Verdict bytes retain their formats.
- No new config surface, runtime retry loop, recovery API, model calls outside nare, credentials, App identity or cluster operations.
- Do not copy private provider error text into normalized public diagnostics. Assert its retention in the existing private capture files.
- No em dashes in published prose, docs or commit messages.

## Review Focus

- A clean sibling cannot conceal another persona's provider failure: Task 1 mixed-persona installed regression.
- Earlier valid output cannot survive a later provider failure as usable coverage: Task 1 real subsequent factory failure.
- Explicit empty partial output remains eligible, and actual overshoot is not clipped: Task 1 partial-empty installed case and ledger reconciliation.
- A live head change cannot silently rebind a successful review: Task 2 successful force-push capture binding.
- A shell caller must observe failure and skip its continuation: Task 2 installed module under bash -e.

## Tasks

- [x] Task 1: Installed failure and partial evidence.
- [x] Task 2: Captured-head delivery, caller propagation and failure guidance.

### Task 1: Installed failure and partial evidence

**Files:** Modify `tests/test_installed_review.py`; modify `tests/helpers/nare_offline_worker.py` only for per-reply transport error support; keep existing scenario/system selection. Production source and configuration are unchanged.
**Consumes:** Existing `wheel_cli`, `offline_runtime`, `tool`, `text`, factory scenario selection and installed evidence capture.
**Produces:** Installed cases proving attributed mixed failure, provider failure after a valid document, and explicit empty partial approval. Preserve existing test fixture APIs for Task 2; report any added reusable setup interface.

- [x] Step 1: Add mixed initial coverage with two personas and a two-process barrier: one valid explicit empty document and one provider error. Assert exit 1, empty stdout, no POST, no findings/verdict/success result or posting journal/payload, retained clean sibling document, named failed persona/provider reason, raw private error sentinel, exactly one attempt per persona, no correction/retry, incomplete accounting and no active reservations.
- [x] Step 2: Add a real factory-scripted failure on the provider call after a valid empty findings document plus read tool call. Assert earlier evidence survives, but uncertain accounting and provider failure prevent verdict/posting. Use per-reply `{error: <sentinel>}` in the offline transport, not rewritten nare artifacts or an engine mock. Run the new regression before adding fixture support, retain the observed failure and identify it honestly as proof-harness support rather than a production safety defect.
- [x] Step 3: Implement the minimal per-reply factory error behavior; retain existing global-error scenarios and all guards. Add explicit partial-empty installed success next to partial blocking: allocation/limit 1, actual usage 15, overshoot 14, panel partial, findings empty, code approve, one APPROVE POST with no inline comments and persisted result bytes.
- [x] Step 4: Strengthen existing missing/failed/partial installed rows to reconcile outcome, allocation, invocation limit, actual usage, overshoot and ledger confidence; prove absent posting artifacts on execution failure. Keep success replay/manifest/byte checks and actual factory-only boundaries.
- [x] Step 5: Run `uv run --python 3.14 --locked --extra dev pytest tests/test_installed_review.py tests/test_nare_cli_integration.py`, with the pinned external executable configured. Run Ruff and strict types. Retain commands, outputs, input hashes and fixture failures without claiming unobserved REDs. Commit and write the task report. Coordinator independently reviews before Task 2.

### Task 2: Captured-head delivery, caller propagation and failure guidance

**Files:** Modify `tests/helpers/installed_gh.py`, `tests/test_installed_review.py`, `docs/cli.md`; create `docs/failure-paths.md`.
**Consumes:** Existing installed setup and Task 1 cases/helpers. CLI exit/result, panel eligibility and posting schemas remain unchanged.
**Produces:** Successful changed-live-head proof, real shell/module failure propagation and linked operator failure guide. Actual reusable Action remains #14.

- [x] Step 1: Add a force-push fixture case: ingestion sees head A (`a` repeated 40), pre-write lifecycle sees open head B (`b` repeated 40), accepted receipt binds the posted payload. Before adding the gh transition, require evidence that both heads were observed; retain the meaningful missing-transition RED.
- [x] Step 2: Implement the minimal gh metadata transition and observation evidence. Assert one POST and exit 0; captured metadata, payload commit_id, review body Head SHA, receipt and result stay at A; fresh lifecycle read proves B. No live API or engine replacement.
- [x] Step 3: Run the installed module through `bash -e` over an existing actual provider failure scenario, followed by a continuation marker. Assert shell exit 1, missing marker, empty stdout, retained run and zero POSTs. Keep direct console rows. This proves caller propagation without creating or claiming a reusable Action.
- [x] Step 4: Write a concise guide mapping execution failure, validated partial success, missing output, uncertain accounting, closure, force-push, corrections and posting uncertainty to artifacts and exits. Initial retries are absent; correction and existing posting recovery are separate. Raw provider errors remain private captures; budgets are after-turn thresholds without a hard spending ceiling. Link from CLI guidance and explicitly leave Action wiring/verification to #14.
- [x] Step 5: Run the focused installed, native nare and CLI suites, Ruff and strict types; retain native evidence and hashes, commit and report. Coordinator reviews, marks the plan complete, then runs final whole-branch review and both full six-gate Python lanes on the committed head before push.

## Verification and delivery

Baseline at 618b7be: Python 3.14, actual pinned nare 2026.10.4, 2650 tests with zero failures/errors/skips. Each task has an independent spec/quality gate; the final reviewer sees the full branch and ledger. Issue-wide review fix budget is two rounds, retries/rebases two each, initially zero spent. Both Python lanes must pass all six configured gates; CI must be nonempty and green on the exact pushed SHA. Open and link one PR, rewrite its summary with final results, squash merge, verify issue/board closure, archive evidence and clean this issue's worktree.
