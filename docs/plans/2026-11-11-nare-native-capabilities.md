# Nare native capabilities

## Goal and design

Run Scrutare against released nare 2026.11.94 and stop leaving nare features
unused. The subprocess boundary, Python 3.10 support, read-only review
(`--tools read` with `--root`), contracts 1 and 2 for their historical releases,
legacy replay and the fail-closed stance on unknown contracts all stay.

Three kinds of change, kept apart:

- Plumbing that leaves every model request byte-identical is the default:
  the pin, capability discovery from `nare contract` data, explicit compaction
  values, and file input for the system and task prompts.
- Anything that changes request bytes or adds model requests is an opt-in rail
  field whose default reproduces today's invocation exactly. This is the repo
  rule until the #51 promotion gate.
- Read gates and forked sessions are assessed below and not implemented.

No live model call, credential, holdout content or deployment is part of this
work. Every proof runs offline against the installed 2026.11.94 console.

### What the release range changes

nare `2026.11.80..2026.11.94` adds `--compaction` (`elide` or `summary`),
`--compaction-trigger` and `--compaction-target`, caches model discovery per
process, and tightens the summary gate. The defaults are `elide`, `0.8` and
`0.6`, which are the values 2026.11.80 hard-coded, and in `elide` mode every
request is the one nare sent before the option existed. Scrutare already drops
every `NARE_*` variable from the child environment, so no ambient value reaches
a session. The three values are still passed explicitly when the runtime
advertises the flags, for the reason contract 3 pins its recovery controls: a
later nare default must not change a review without a Scrutare change.

## Task 1: Pin 2026.11.94

Files: Dockerfile, CI and release workflows, smoke scripts, guides, installed
test expectations, tests/test_nare_runtime_pin.py.
Interface: every installation uses release 2026.11.94 at commit
66fd6459262798839c15ebaed37bd2f0c55daf46 in a separate Python 3.14 environment.

- [ ] Move the pin test to the new release and commit, with 2026.11.80 as the
  previous pin that must not remain.
- [ ] Update every pin location and the installed version expectations.
- [ ] Run the installed proofs against the 2026.11.94 build.

## Task 2: Capabilities from contract data

Files: engine/session_models.py, engine/nare_session.py, engine/fanout.py,
evaluation/runner.py, capability and session tests.
Interface: `NareCapability.flags` is the advertised `nare run` option map
(`NareFlag(name, takes_value, choices)`), or `None` for contracts 1 and 2.
`require_rail_options(models, capability)` refuses a wave before any session.

- [ ] Add failing tests: contract 3 inspection reads `flags`; a contract 3
  object with no well-formed `flags`, or without a flag Scrutare always sends,
  fails inspection; `--stream` follows the advertised flag on contract 3 and
  the release number on contracts 1 and 2; an option that is not advertised is
  refused before any session.
- [ ] Carry flags on the capability. Gate `--stream`, `--structured-output`
  (flag, mode among its choices, provider among the entry's providers),
  `--proxy-num-retries` and the compaction values on the advertised data.
- [ ] Keep the release-number rules untouched for contracts 1 and 2.

Every contract 3 release advertises `flags` (the key shipped in 2026.11.10,
contract 3 in 2026.11.33), so its absence is a malformed contract object, not
an older runtime.

## Task 3: Prompts out of argv

Files: engine/nare_session.py, replay/audit.py, tests/helpers, session, replay
and installed tests, docs/session-fanout.md.
Interface: where the runtime advertises `--system-file` and `--prompt-file`
(and the `system_input` and `prompt_input` entries), the attempt directory
holds owner-only `system-prompt.txt` and `task-prompt.txt`, and a correction
turn `<kind>-correction.prompt.txt`. `invocation.json` keeps
`system_prompt_sha256`, `task_prompt_sha256` and the argv actually used.

- [ ] Add failing tests: file route chosen only when advertised; files are
  exclusive, owner-only and hold the exact UTF-8 bytes; argv carries no prompt
  text; the installed console sends the same request as the argv route.
- [ ] Write the files through the pinned attempt descriptor and pass the paths.
- [ ] Correction resume: keep giving the system prompt, through the same file.
  nare resends the recorded prompt when no system option is given, but the
  record is the redacted copy, so a persona prompt holding anything that looks
  like a credential would be resent as `[redacted]`. Passing the option keeps
  the bytes of the first run.
- [ ] Replay reads the prompt from the recorded file and checks its digest;
  bundles recorded with argv prompts replay as before.

## Task 4: Opt-in rail options

Files: config.py, engine/nare_session.py, engine/session_output.py,
replay/audit.py, docs/config.md, docs/replay.md, config, session, replay and
installed tests.
Interface: `ModelRail` gains `request_retries`, `request_retry_seconds`,
`cache_breakpoints` and `effort`; `structured_output` is accepted on the
anthropic rail. Each is off by default, recorded only when some rail sets it,
and refused before any session when the runtime does not advertise it.

- [ ] `request_retries`: failing tests first for config, argv, records and
  replay. Then prove accounting offline with the installed console and a
  scripted HTTP boundary that fails transiently before it answers: the result
  usage equals the sum of the cost events, the ledger equals the result, and
  the retry count is recorded. Ship only if that holds.
- [ ] `cache_breakpoints` on the openai rail.
- [ ] `effort`, with the levels the contract lists.
- [ ] `structured_output: json_schema` on the anthropic rail. nare withholds the
  tools on the constrained turn with an empty tool list while earlier tool
  blocks stay in the transcript. Prove the request shape offline and document
  that a live Anthropic endpoint has not confirmed it.
- [ ] Document each in docs/config.md with its default and its records.

Retry accounting, as the contract states it: a failed attempt adds no turn, no
`cost` event and no usage, and the resent request carries the same messages.
Scrutare sums `cost` events and reconciles them with the result, so a resend
cannot double count. What nare cannot report is a provider charge for an
attempt that was abandoned mid-response. That is the same limit Scrutare
documents today for an interrupted call, so a session that retried records how
many resends it made, and the guide says reported usage is a lower bound there.

## Task 5: Dollar cost in artifacts

Files: engine/session_models.py, engine/session_output.py, engine/nare_session.py,
engine/budgets.py, the strategy results, engine/review.py, artifact tests, guides.
Interface: `SessionCost(usd, source)` with source `provider_reported` or
`unknown`. Each session outcome carries one, the ledger sums them, and the
ledger snapshot, `iterative.json` and the review receipt record the review's.

- [ ] Failing tests for per-session and per-review cost, including unknown.
- [ ] Decode nare's `cost` with its `estimated` flag. An amount is
  `provider_reported` only when nare says none of it was calculated.
- [ ] Sum in the ledger over sessions that used tokens; one unknown session
  makes the review unknown, and unknown is never zero.

nare can also calculate a cost from prices a gateway lists. That figure is
recorded as unknown here: #116 reserves `estimated` for a frozen price table
that replay can re-derive, and nare's discovered prices are neither frozen nor
recorded. A streamed openai session reports no cost of its own, so such reviews
stay unknown until the price table exists.

Out of scope here: a pricing table, a report command and a dollar cap (#116).

## Task 6: Verification

- [ ] Full suite on Python 3.10 and 3.14 with `SCRUTARE_TEST_NARE_EXECUTABLE`
  at the 2026.11.94 build, `ruff check`, strict `mypy`. Compare failures with
  the same suite on the unchanged base commit.
- [ ] Disclosure guard when the private term list is present.

## Design note: read gates and forked sessions (not implemented)

### Could read requirements replace citation validation and its correction?

What nare offers: `--require-read PATH` and `--require-cited-reads POINTER`
hold a final answer until the named or cited paths were read with `read`, as a
correction charged to `--max-corrections`, with tools still offered. With a
requirement set the result gains `reads` (path, whole-file `sha256`, size), and
every successful read leaves an `evidence` record with the digest and the line
range returned.

What Scrutare checks today, after the session: each citation's `side` and
`revision` against the capture manifest, its logical `path` against a captured
entry, its `sha256` against the manifest and the artifact's bytes, and
`end_line` against the file's line count. A refusal fails the session, or, with
the opt-in correction, costs one resumed tool-free turn.

Assessment: not a replacement, and only partly a complement.

- The path spaces differ. A citation names a logical repository path plus a
  side. A read names a read-root-relative artifact, and the manifest maps one
  to the other. `--require-cited-reads '/findings/*/evidence/citations/*/path'`
  would compare logical paths with artifact names, so it would hold answers
  that are correct and ask the model to remove citations it cannot read. The
  pointer language has no way to apply a mapping.
- The checks differ. A read gate proves a file was opened. It does not check
  the revision, the cited digest or the line bound, which are the refusals the
  correction turn exists for (`line_range_outside_capture`, malformed revision
  or digest). The read `evidence` records could supply the digest and the lines
  shown, so Scrutare could tighten validation to "cited lines were actually
  displayed", but that is a new and stricter rule, not a cheaper one.
- The cost model differs in nare's favour. A nare hold happens inside the
  session, with the transcript in the prompt cache and tools offered. Scrutare's
  correction is a second process that resumes the session. For the defects that
  a read gate can express, the in-session hold saves a process start and keeps
  the cache prefix.

What it would save: nothing for the defects Scrutare corrects today. A real
saving needs nare to accept caller checks in-session.

What blocks it: (1) the logical-path to artifact-name mapping; fixed either by
capturing artifacts under their logical paths per side, which changes the read
root layout and every prompt (request bytes, so opt-in and gated), or by nare
accepting a path map for cited reads; (2) nare has no hook for a caller-defined
check, so revision, digest and line-bound defects still need Scrutare's own
turn; (3) `--max-corrections 1` is shared between schema and read corrections,
so a read hold would spend the one schema correction a session has.

A smaller step that is available now: `--require-read diff.patch` and
`files.json` would guarantee the reviewer opened the diff. It adds a possible
model turn, so it would be opt-in.

### Would a primed session forked per persona share a prompt-cache prefix?

The idea (#114): run one session that reads the shared diff and context, then
`--resume` it with `--fork` once per persona, so every persona's request starts
with the same cached prefix.

Assessment: the prefix is not shared as things stand, for two reasons the
contract states.

- A caching backend orders the prefix as tools, system prompt, messages. Each
  persona has its own `--system`, so the prefix diverges at the system prompt,
  before any shared message. Only the tool definitions could be shared, and
  that prefix is small.
- A fork that changes the system prompt drops the signed thinking from the new
  file, so on the anthropic rail the primed turns would also differ in content
  from the cached ones.

To share the messages, the persona would have to move out of the system prompt:
one common system prompt, shared priming turns, then the persona's instructions
as the forked follow-up prompt. That keeps signed thinking, and the prefix up to
the fork is identical across personas. It also changes what every persona is
told and where, so it is a new review design that needs the quality gate, not a
transport change.

What it would save: per extra persona, the input tokens of the shared reads
(the diff and context artifacts, read once in the priming session and served
from cache afterwards), at the cache-read price on backends that cache. The
priming session itself is one more session with its own turns.

What blocks it: (1) per-persona system prompts, as above; (2) personas would
share one model rail, since a cache prefix is per model, while rails are
configurable per persona today; (3) the budget ledger grants each persona its
own allowance from zero, and a fork starts with the primed session's cumulative
usage, so admission and accounting need a shared-prefix rule; (4) the priming
session decides which files are read, so personas lose independent discovery,
which is a quality question for the gate; (5) forks must start after the priming
session ends, so the wave is no longer fully concurrent.

## Review focus

Unknown contracts and malformed contract data must fail closed. A requested
option the runtime does not advertise must be refused before any session. With
every new field unset, argv differs from the previous release only by the
compaction values and the two input files, and the request bytes do not differ
at all. File input must carry the exact prompt bytes and stay owner-only. A
resent request must not be counted twice, and unknown cost must never be
recorded as zero. Legacy bundles must replay unchanged.
