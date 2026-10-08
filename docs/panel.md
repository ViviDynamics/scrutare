# Programmatic panel reviews

Panel is the default implemented review strategy. The Python engine runs one
independent concurrent wave of personas, verifies findings against the filtered
diff, offers one anchor correction opportunity per affected persona, dedupes
surviving findings and derives the verdict in code. Python callers choose when
to post. The [review CLI](cli.md) runs capture, panel execution and posting, then persists its result and artifact manifest before
printing success.

```python
import asyncio
from pathlib import Path

from scrutare.config import load_config
from scrutare.engine.session_models import NareRuntime
from scrutare.engine.strategy import run_review

config = load_config(Path("scrutare.yaml"))
result = asyncio.run(run_review(
    Path("/absolute/path/to/fresh-captured-run"),
    config,
    runtime=NareRuntime(
        executable=Path("/absolute/path/to/nare-environment/bin/nare"),
        max_turns=50,
        timeout_seconds=600.0,
    ),
))
print(result.status, result.usage.total)
if result.verdict is not None:
    print(result.verdict.verdict)
```

`run_review(run_dir, config, *, runtime)` is asynchronous and returns
`scrutare.engine.panel.PanelResult`. Its fields are `status` (`complete`,
`partial`, or `failed`), optional `verdict`, the initial `FanOutResult` snapshot,
ordered typed `corrections`, optional terminal `verification`, final `usage`,
`accounting_complete`, and `run_dir`. The supplied config must match the captured
snapshots. Runtime inspection and preparation errors can raise before a result
exists; unsafe artifact ownership and final publication errors also raise.

Every model call goes through the external nare executable. Nare 2026.10.0 or
newer with contract 1 is required. See [session-fanout.md](session-fanout.md) for
installation, read-only tools, environment isolation and runtime limits. The
lower-level `fan_out` and `run_persona_session` APIs remain available for candidate
findings without final review artifacts.

[Iterative review](iterative-review.md) persists findings across pushes, and
[debate review](debate.md) arbitrates bounded discussions with a chair.
Panel performs exactly one convergence pass regardless of positive
`rounds.max`. It does not construct exhaustion or escalation. Blocking findings,
failed execution and budget stops do not trigger more rounds.

## Coverage, corrections and verdicts

Each initial persona must be admitted, have complete reported accounting, and
provide a validated findings document. A partial document counts, including an
explicitly empty `findings` array. Missing output cannot stand for an empty
review. A failed or unadmitted initial persona, uncertain accounting, or missing
validated initial document fails the panel with evidence and no verdict. No
correction starts after fatal initial coverage. Initial sessions are not retried.

Code checks every candidate anchor against the validated filtered diff. It groups
invalid originals by their source persona, preserving configured persona and
finding order. Equal originals share a deterministic request ID; distinct
originals retain distinct IDs. At most one fresh correction invocation per
affected persona receives those requests. It keeps the exact persona system,
resolved provider/model/base URL and three-file read root. It can return only
request ID, file, line and side. Models cannot change text, category or persona,
introduce findings, or declare the verdict.

Omitted, invalid or excluded anchors are dropped after that opportunity. Denied
correction admission or a known budget-stopped correction without a document
leaves the original unresolved and drops it. Valid partial correction documents
count, including an explicitly empty `corrections` array. Failed, protocol-invalid
or uncertain correction execution fails the panel without a verdict.

Code dedupes survivors in original order, retaining every source occurrence,
including equal duplicates and conflicting categories. Any configured blocking
category requests changes; otherwise the verdict is `approve`. Partial coverage
or dropped anchors is recorded separately in `panel.json`; an approval from
survivors does not imply complete coverage.

## One shared reported allowance

Fair ordered persona allocations and the live `ReviewBudgetLedger` survive both
phases. Corrections receive only remaining persona and review allowance, with a
zero baseline and canonical `persona/attempt-0002` identity. An initial 60 tokens
against a 100-token allocation leaves at most 40 for correction. Correction use
of 30 brings the final total to 90; the immutable initial snapshot remains 60.

Nare enforces cumulative reported thresholds after turns. A 40-token correction
can report three 15-token tool turns and stop at 45, retaining any validated
partial document and recording 5 tokens of overshoot. Reported totals count
disjoint input, output, cache-read and cache-write counters once, without clipping.
Initial and correction `done` responses above their allocation are partial even
with exit 0. Successful `done` exactly at threshold can remain complete while
closing further admission. Exhausted or uncertain accounting closes later
admission; already admitted work can finish and overshoot. This is no hard
spending ceiling or complete provider billing claim. No new YAML budget or retry
settings are introduced.

## Artifacts and offline replay

Use a fresh captured run for each execution. Panel refuses prior execution or
posting evidence, including symlinks and nonregular entries, before preparation.
Artifact writes use exclusive ownership, no-follow paths and private permissions;
earlier evidence is never overwritten.

- `sessions/<persona>/attempt-0001/` retains the initial schema, invocation,
  JSONL/stderr, nare session when available and engine result.
- `attempt-0002/` retains admitted correction schema, purpose and original request
  bindings, invocation, JSONL/stderr, session and typed result. Denied corrections
  retain a `not_started` result without launching a process.
- `fanout.json` is the immutable initial wave snapshot.
- `panel.json` records schema/application versions, captured head, strategy,
  one convergence pass, status/reason, input and config digests, initial and
  correction evidence, verification/drop reasons and the final ledger.
- `findings.json` is the bare ordered array of `MergedFinding.to_dict()` values.
- `verdict.json` contains exact `Verdict.to_bytes()` schema 1 bytes, without
  exhaustion. Failed classified panels publish diagnostic panel evidence and
  withhold final findings and verdict.

Publication installs findings and panel evidence before verdict last. Installation
is per artifact, not a multi-file transaction: an error can leave partial evidence
without a verdict. A new captured run is required to try again. Producer evidence
is compatible with the existing Python posting API; `run_review` does not post or
request human reviewers.

```sh
scrutare replay /absolute/path/to/completed-run
```

Replay recomputes the verdict from saved findings and policy without a model or
network. An unposted successful panel can establish saved byte identity while
the posted target remains absent. An ingestion-only or failed panel lacks final
verdict evidence. Local unsigned artifacts prove recorded consistency, not remote
delivery or resistance to a coherent history rewrite. See [replay.md](replay.md)
for exit codes and the trust boundary.
