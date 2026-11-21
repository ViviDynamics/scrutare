# In-session citation check

## Goal and design

Run Scrutare's citation validation inside the nare session (#130), so a
machine-detectable citation defect (a line past the end of a file, a placeholder
hash, a malformed revision) is corrected by one more turn of the same session,
with the transcript still cached and the read tool still offered, instead of
failing the session or costing a second resumed process. Part of #137.

The [design note](2026-11-11-nare-native-capabilities.md#design-note-read-gates-and-forked-sessions-not-implemented)
named what nare lacked. nare has since shipped it:

- `--check-command` and `--check-timeout` (nare #296, 2026.11.102): an argv, run
  without a shell, that reads `{"answer": ..., "reads": [...]}` on stdin and prints a
  JSON list of errors. Its errors go into the same in-session correction as schema
  errors, under `--max-corrections`.
- `--cited-read-map` (nare #297, 2026.11.103): maps a cited value to the
  root-relative paths whose read satisfies `--require-cited-reads`.
- 2026.11.107 (nare #313): a domain correction restores the approved tools, so the
  model can read the file again before it answers.

The rules of the earlier plans stay: the subprocess boundary, capabilities read
from `nare contract`, new behavior opt-in, and a default-configuration review
sends the same argv and request bytes as before.

### The checker

`python -I -B -m scrutare.findings.citation_check --digest D ROOT`, on Scrutare's own
interpreter (`sys.executable`), since Scrutare cannot import nare. It reads nare's
payload, runs the post-session findings parser and citation validator on the
answer against the capture manifest in `ROOT`, and prints nare's error list.

- Same verdict by construction: it passes an answer (`[]`) exactly when the
  post-session parser (`session_output._findings`, which runs `parse_finding` and
  `validate_evidence`) accepts it. When the parser refuses, the checker lists every
  refused finding and citation, each with a location, a fixed message and fixed
  guidance, so one correction can fix them all. The first listed error is the
  refusal the post-session path would have raised. A shared table of cases proves
  both paths agree.
- No model text in the messages: they name indexes, the refusal reason, and
  integers and artifact names Scrutare measured, never a cited path or value.
- No credentials needed: nare withholds them, and the checker reads only files.
- `-I` ignores `PYTHON*` variables and the current directory (nare runs the
  command in `--root`), and `-B` writes no bytecode. `D` is the SHA-256 of every
  Python source file of the `scrutare` package; the checker recomputes it from the
  package it imported and refuses to give a verdict (exit 3) if it differs, so the
  recorded digest is the code that ran.
- A capture it cannot read is not the model's fault: it exits non-zero, and nare
  ends the run with `check raised CheckCommandFailed`, a failed session.

### Configuration

```yaml
findings:
  evidence: v2
  citation_check: session     # default post_session: today's path, byte for byte
  max_corrections: 2          # optional, 1 to 3; default 2 with session
  cited_reads: false          # optional; true adds --require-cited-reads and the map
```

- `citation_check: session` requires evidence v2 and a runtime that advertises
  `check_command` (and the two flags). It applies to discovery sessions only, the
  sessions the citation correction turn covers. Assessors and discussion keep their
  post-session validation.
- It is refused together with `citation_correction` or `protocol_correction`. The
  check covers every refusal the correction turn covers, and more, in the same
  session with tools. A session that spends its corrections ends with no answer, so
  a second, resumed correction would have nothing to correct.
- `max_corrections` is nare's budget for the session, shared between schema, read
  and check corrections, and is sent only on sessions that carry the check (every
  other session keeps the pinned 1). The default of 2 keeps the schema correction a
  session had before and adds one for citations. nare makes a correction free when
  the answer has fewer distinct errors than every earlier failing answer, so with 2
  or more a model that keeps improving can be corrected more often than the budget.
  Each correction is a model turn inside `--budget-tokens` and `--max-turns`, so a
  higher budget costs tokens and latency. 3 is the cap until a screen says more
  helps. A separate allowance per kind is not needed for this design and is not
  filed on nare.
- `cited_reads: true` (requires `session` and a runtime that advertises
  `cited_reads.map`) holds an answer until each cited file was read with `read`. It
  sends `--require-cited-reads /findings/*/evidence/citations/*/path` and
  `--cited-read-map` with a map built from the capture manifest: each captured
  logical path to the artifact names that capture it (head and base). The map is
  side-blind, so reading either side's capture satisfies a citation; the check
  binds the side. This is a stricter rule than post-session validation (a citation
  copied from `repository-context.json` without reading the file passes today), so
  it is a separate opt-in.

### Records

- `invocation.json` gains `citation_check`: the checker argv, `checker_sha256`,
  `check_timeout_seconds`, `max_corrections`, and, with cited reads, the map's
  SHA-256. argv carries the flags exactly as sent. The map is the attempt's own
  `cited-read-map.json`, outside the read root.
- `result.json` gains `citation_check: {outcome, corrections, refusal}`. `outcome`
  is `passed`, `corrected`, `refused`, `reads_required`, `no_verdict`, or null when
  no answer reached the check. `corrections` lists each correction nare made
  (`kind`, `charged`, and for a check correction the checker's error codes; schema
  messages are not kept, since one can name model text). `refusal` holds the codes
  of a spent budget's last refusal.
- A session that spends its budget on a check error fails as the post-session
  path fails: `citation_validation` with the first reason (and
  `citation-validation.json`), or `protocol` for a non-citation refusal. A check
  with no verdict and a spent read hold fail as `protocol`. A schema violation
  that is not the checker's is classified as before.
- Replay rebuilds the argv, the checker digest's shape, the map and the
  corrections from the retained streams and inputs, and accepts the configured
  correction budget in the recovery policy audit. It does not rerun the checker on
  a session's refused answers; the accepted answer passes post-session validation
  as always.

## Task 1: Capability and configuration

Files: engine/session_models.py, engine/nare_session.py, config.py, config and
capability tests.
Interface: `NareCapability.check_command` and `NareCapability.cited_read_map`;
`FindingSettings.citation_check`, `.max_corrections`, `.cited_reads`;
`require_findings_options(findings, capability)`.

- [x] Failing tests: off by default and absent from the record; recorded and
  re-parsed when chosen; invalid values, missing v2, combinations with the
  correction turns refused; capability read from contract data; a runtime without
  the entry or flags refused before any session.
- [x] Implement and call the refusal where rail options are refused (fan-out and
  the evaluator) and again in the session.

## Task 2: The checker

Files: findings/citation_check.py, tests/test_citation_check.py.
Interface: `check_answer(answer, root) -> list[dict]`, `check_argv(root)`,
`package_digest()`, `cited_read_map(root)`, `main(argv)`.

- [x] Failing tests: one shared table of citation cases (the existing ones from
  test_structured_evidence.py and test_citation_line_counts.py, plus malformed and
  multi-citation answers) run through both the post-session parser and the
  checker, with the same verdict and the same first reason; the command's stdin
  and stdout protocol; digest refusal; no model text in any message.

## Task 3: Session argv, records and classification

Files: engine/nare_session.py, engine/session_output.py, session tests, the
offline nare worker.
Interface: discovery sessions with the check send `--check-command`,
`--check-timeout 30`, `--max-corrections N`, and with cited reads the two read
flags.

- [x] Failing tests: default argv unchanged; argv and records with the check;
  a spent budget on a check error classified as the post-session refusal.
- [x] The offline nare worker lets a run start exactly the `--check-command` argv
  it was given, and nothing else.
- [x] Offline installed test with the real 2026.11.107 console and a scripted
  model: a citation past the end of the file is corrected in the same session, in
  one process, with no resumed transcript; and the replay is clean.

## Task 4: Replay

Files: replay/audit.py, replay tests.

- [x] Bind argv, digest shape, map and corrections to config and streams; accept
  the configured budget in the recovery audit; refuse records without the opt-in.

## Task 5: Documentation and verification

- [x] docs/config.md section and field table, docs/replay.md.
- [x] Targeted tests on Python 3.10 and 3.14 with the installed 2026.11.107
  console, `ruff check`, strict `mypy`. Linux CI runs the full lanes.

## How it will be measured (not part of this change)

A development-corpus screen on the self-hosted rail (#92), run twice with the
same frozen inputs: once with the default `post_session`, once with
`citation_check: session` and `max_corrections: 2`. Report per arm the sessions
refused for `citation_validation` or `protocol`, the check corrections per
session from `result.json` (charged and free), sessions that spent the budget,
and tokens and wall time per session. Promote the setting, or raise the budget,
only on that evidence and under the #51 gate. No live model run is part of this
change.

## What stays unchanged

With `citation_check` unset (or `post_session`) the canonical configuration, the
argv, every request byte and every artifact are what they were. The citation
correction and protocol correction turns are untouched. Assessors, discussion,
chair and reanchor sessions never carry the check. Post-session validation still
runs on every answer, including one the check passed.

## Review focus

The checker must agree with the post-session parser on every answer, and must
never put model text in a message. A runtime that does not advertise the check
must be refused before any session. A session that spends its budget must fail
as the post-session path fails. The default argv must not change.
