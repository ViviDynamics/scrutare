# Offer one bounded correction turn for machine-detectable citation defects

Issue #91

## Scope
In (this PR): an opt-in `findings.citation_correction` (default off). When a discovery
session's answer is refused only for machine-describable citation defects, Scrutare
resumes that nare session for one tool-free correction turn that names each defect,
then validates the corrected answer exactly as before. The refusal, the correction
prompt and the outcome are recorded in the session artifacts and audited on replay.
The evaluator passes the setting through to every evidence-v2 arm.

Out: the live smoke and the development-corpus measurement. They follow on the
released wheel, in a separate report package, as for #79 and #83. No qualification
is run, and no default changes.

## Assumptions
- The setting lives under `findings`, next to `evidence`, because citations exist only
  in evidence v2 (the issue's `review.citation_correction` is an example name, and
  there is no `review` section). It requires `findings.evidence: v2`. Off, canonical
  configuration and every artifact keep their bytes.
- Only discovery sessions are offered a correction: the reviewer sessions of the
  initial wave. Assessor, discussion, chair and anchor-correction sessions are
  unchanged.
- Correctable defects, and only these:
  - `line_range_outside_capture` on a captured, nonempty file: the prompt states the
    file's last citeable line (the bound the unchanged validator enforces);
  - a malformed or empty `revision` (not 40 lowercase hex characters) or `sha256` (not
    64): the prompt states the expected form and where the value is recorded.
  A well-formed but wrong revision or hash, an uncaptured path, a reversed range, an
  empty file, a schema failure or any other refusal is not offered a correction.
- Eligibility is decided on the whole answer: a correction is offered only when every
  refusal in it is one of the above. To decide this, the answer is checked once with
  each named defect hypothetically satisfied by the value the validator expects. That
  check is discarded; Scrutare never puts a value into an answer.
- The session must have finished with an answer (`done`), within its budget, and not
  cut off at the output cap. A session with no budget left gets no correction.
- The correction resumes the same nare session (`--resume` the attempt's
  `session.json`, writing `correction-session.json`), with `--tools none`, the same
  budget limit (nare's budget is cumulative, so the turn counts in the session's
  budget) and `--max-turns 1`. With `structured_output`, nare constrains every turn of
  a tool-free run, so the correction answer is schema-constrained there. The output cap
  applies as on every request. The correction gets what is left of the session timeout.
- The correction answer is citation-only, like anchor correction: one entry per named
  citation with its `revision`, `sha256`, `start_line` and `end_line`. The corrected
  answer is the original findings document with exactly those model-supplied values in
  exactly those citations. It goes through the unchanged parser and citation validation.
- Outcomes: `corrected` (the session completes with the corrected findings),
  `refused` (the corrected answer fails validation, and the session fails for that
  refusal as it would today), `no_answer` (nare ended without a correction answer; the
  session fails with its original refusal) and `interrupted` (timeout, cancellation or
  a capture failure, which fail the session as they do today).
- Records: `citation-correction.json` (refusal, defects, prompt, schema, argv) written
  before the correction starts; `correction.stdout.jsonl`, `correction.stderr.txt` and
  `correction-session.json` retained; `result.json` gains
  `citation_correction: {offered, defects, outcome}` on every discovery session when
  the setting is on, and nothing when it is off. Usage stays cumulative in the shared
  ledger and in `result.json`.

## Tasks
- [ ] 1. Configuration: opt-in, boolean, requires evidence v2, recorded only when on,
  evaluator arms inherit it: `tests/test_citation_correction_config.py`.
- [ ] 2. Defect diagnosis, prompt, schema and application, pure functions:
  `tests/test_citation_correction_defects.py`.
- [ ] 3. Session: on/off, a past-end citation corrected, a placeholder hash corrected,
  a correction that fails again, a correction that gives no answer, a non-citation
  failure not offered, shared accounting: `tests/test_citation_correction_session.py`.
- [ ] 4. Replay audits the recorded refusal, prompt and outcome against the retained
  streams: `tests/test_replay_citation_correction.py`.
- [ ] 5. Installed native nare 2026.10.12 proofs through the installed wheel: on, off,
  past-end corrected, placeholder hash corrected, failed again, non-citation failure,
  replay: `tests/test_installed_citation_correction.py`.
- [ ] 6. Documentation: `docs/config.md`, `docs/replay.md`, `docs/session-fanout.md`,
  `docs/evaluation.md`.
