# Request timing adoption

Issue #135, part of #137. Follows [review latency](2026-11-20-review-latency.md).

## Goal and design

The review latency change recorded every review's wall clock, tokens, retries
and capacity waits, but left time to first token and decode rate `null`:
nare's stream said nothing about when a request was sent or when its first
token arrived. nare added per-request timing in ViviDynamics/nare#315
(released in 2026.11.109, which Scrutare pins since #145). This change records
it.

The rules of the earlier plans stay. Nothing here changes the argv or request
bytes a review sends to nare, nor how a verdict is derived. Timing is a
measurement: replay checks form and consistency against the retained streams,
never equality with a rerun's clock.

No live model call, credential, deployment or live infrastructure is part of
this work. Every proof runs offline, against scripted consoles and the
installed 2026.11.109 console with the offline HTTP harness.

### What nare 2026.11.109 reports

Read at tag 2026.11.109 (`docs/contract.md` "Request timing",
`src/nare/loop.py` `RequestTimingTotals`, and `nare contract`'s
`request_timing` entry):

- Each `cost` event has `detail.timing`: `headers_seconds`,
  `first_token_seconds`, `total_seconds` and `generation_seconds`, each `null`
  where the rail or mode cannot measure it (an unstreamed request has no first
  token, and so no generation window).
- Each invocation's `result` line has `request_timing`, the sums over that
  invocation's requests: `requests`, `timed`, `headers_seconds`,
  `total_seconds`, `first_tokens`, `first_token_seconds`, `generation_seconds`
  and `generation_output_tokens`. A seconds sum is `null` when no request
  measured it, and `generation_output_tokens` counts only requests with a
  generation window, so it is 0 when `generation_seconds` is `null`.
- Unlike `retries`, the sums are per invocation, not per session, and are never
  saved in the session file.
- The fake (`--replay`) transport reports zeros (first token `null`
  unstreamed).

### Design

1. **Read the result lines, not the cost events.** nare already sums each
   invocation; summing the result lines of `stdout.jsonl` and
   `correction.stdout.jsonl` gives the session's total with nare's own
   rounding, and is one value replay can recompute exactly. A session that
   ended without a result line has no block from that invocation; one with no
   block at all records `null`.
2. **Gate on the stream, not on `nare contract`.** The reader is tolerant, like
   `retry_events`: an absent or malformed field is skipped and it never raises.
   A runtime that does not report request timing simply produces `null`, and
   replay recomputes the same value from the same bytes without needing the
   capability record. Gating on the contract entry as well would add a second
   source that could disagree with the stream and buys nothing for a
   read-only measurement.
3. **Record version 2.** Session and review records move to
   `schema_version: 2` and gain `requests`. `first_token_seconds` becomes
   `requests.first_token_seconds / requests.first_tokens` and
   `decode_tokens_per_second` becomes
   `requests.generation_output_tokens / requests.generation_seconds`, each
   rounded to three decimals and `null` with a zero or `null` divisor. Both are
   derived from the block on every read, so a record whose values do not
   follow from its block is malformed. The review record sums its sessions'
   blocks and derives the same two values from that sum, so it is a
   request-weighted mean across sessions, not a mean of session means.
4. **Version 1 stays valid.** A version 1 record (no `requests`, both values
   `null`) validates exactly as before, and replay does not compare it with the
   stream: runs made between #145 and this change have streams that carry
   request timing beside version 1 records, and they must replay unchanged.
5. **Footer.** The summary line gains `, first token 2.1 s, 17.3 output tokens
   per second` only when both values are measured, to one decimal, within the
   footer's character set. With either `null`, the line is byte for byte what
   it was.

## Task 1: Records

Files: engine/timing.py, engine/session_output.py, engine/nare_session.py,
tests/test_timing.py, tests/test_capacity_session.py.

- [x] `RequestTiming`, nare's eight fields strictly validated (counts
  nonnegative, `timed` within `requests`, `first_tokens` within `timed`,
  `first_token_seconds` `null` exactly when `first_tokens` is 0, no generation
  tokens without generation seconds, no seconds without a timed request,
  millisecond seconds), with addition that keeps nulls.
- [x] `request_timing_events` sums every readable result line's block and
  skips everything else.
- [x] `SessionTiming` carries `requests` and its schema version; `_run_session`
  passes the streams' block.
- [x] `review_timing` sums the blocks; `validate_review_timing` accepts both
  versions.
- [x] The scripted nare in `test_capacity_session.py` can report
  `request_timing`.

## Task 2: Replay

Files: replay/audit.py, tests/test_timing.py, docs/replay.md.

- [x] A version 2 session record's `requests` must equal the block recomputed
  from the retained streams (`timing_disagrees`), and its derived values must
  follow from it.
- [x] A version 1 record beside a stream with request timing replays clean.
- [x] Wall clock is still never compared with anything.

## Task 3: Footer

Files: engine/timing.py, tests/test_timing.py.

- [x] Both measured values appended only when both are measured.
- [x] Existing exact-output footer tests: `tests/test_timing_footer.py` and
  `test_footer_line` in `tests/test_timing.py` use sessions without request
  timing, so their lines are unchanged and they were not edited.

## Task 4: Installed proof

Files: tests/test_installed_request_timing.py,
tests/test_installed_request_retries.py.

- [x] Through the real 2026.11.109 console and the offline HTTP harness, a
  streamed session's record carries the result line's block, every answered
  request is timed with a first token, the review record sums it and replay is
  clean.
- [x] A resumed correction session's record is the sum of both result lines.
- [x] Deliberate change: the installed retry proof asserted both values
  `null`; it now checks the record's block is the result line's and counts the
  two answered requests, not the failed attempts.

## Task 5: Tests whose expected records changed

Deliberate changes to exact-record tests, from version 1 to version 2 with a
`null` `requests` block (their scripted sessions report no request timing):

- `tests/test_timing.py::test_a_launched_session_records_its_timing_beside_its_result`
- `tests/test_timing.py::test_review_totals_and_rails` (now also checks the
  version 1 form)
- `tests/test_timing.py::test_a_session_document_must_be_exactly_the_record`
  (the `schema_version: 2` rejection became `1` and `3` on a version 2 record)
- `tests/test_review_run.py::timing_without_sessions`

## Task 6: Verification

- [x] Targeted tests on Python 3.14 and 3.10, with
  `SCRUTARE_TEST_NARE_EXECUTABLE` at the 2026.11.109 build.
- [x] `ruff check` and strict `mypy`.
- [x] Disclosure guard on the pull request body.
- [ ] Linux CI on the pull request.

## Remaining work (not this change)

- [ ] The latency report: median and p90 review time, time to first token and
  decode rate per rail, from real reviews. It needs live runs.
- [ ] Size the session timeout (#147) and the App's deadline from that report.

## What stays unchanged

- The argv and request bytes of every nare invocation, in every configuration.
- `verdict.json`, `findings.json`, session `result.json`, `fanout.json` and the
  other outcome documents.
- The posted summary of any run whose record did not measure both values.
- How version 1 records validate and replay.
