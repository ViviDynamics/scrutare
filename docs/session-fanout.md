# Initial persona session fan-out

The Python API runs one initial concurrent wave against a captured run. The
review CLI currently performs ingestion only. Fan-out returns candidate session
records; callers still need anchor verification, deduplication, convergence,
verdict derivation and posting. It creates neither `findings.json` nor
`verdict.json`, and a complete empty candidate output is not an approval.

```python
import asyncio
from pathlib import Path

from scrutare.config import load_config
from scrutare.engine.fanout import fan_out
from scrutare.engine.session_models import NareRuntime

config = load_config(Path("scrutare.yaml"))
result = asyncio.run(fan_out(
    Path("/absolute/path/to/captured-run"),
    config,
    runtime=NareRuntime(
        executable=Path("/absolute/path/to/nare-environment/bin/nare"),
        max_turns=50,
        timeout_seconds=600.0,
    ),
))
for outcome in result.outcomes:
    print(outcome.persona, outcome.status, outcome.usage.total)
```

`NareRuntime` is an operational Python argument, with positive finite timeout
and turn limits. It adds no YAML fields. Scrutare requires Python 3.10 or newer;
install nare 2026.10.0 or newer separately with the interpreter required by its
release. For the pinned integration runtime, clone the public
[ViviDynamics/nare release](https://github.com/ViviDynamics/nare/tree/2026.10.0),
verify commit `899e1eb3851dcce4a4ab907f76384c49f4aca2fa`, and run
`uv sync --project /path/to/nare-checkout --python 3.14 --locked --no-dev`.
Point `NareRuntime.executable` at that checkout's `.venv/bin/nare`. Nare is
an external process, without a Scrutare production import or dependency.
Inspection checks its version and contract once per wave before any session
launch. All model calls go through nare.

The supplied configuration must agree with the capture's canonical and raw
configuration snapshots. Preparation validates the captured target and head,
then copies only selected diff sections, minimal file records and safe context
into `review-inputs/`. Existing copies must match the same bytes. Persona
resolution preserves configured order and exact built-in or inline systems.
Each invocation consumes the descriptor's prompt and read/root arguments
verbatim, with a separate system and its resolved provider, model and base URL.
An empty effective selection stays empty. The read root never expands to the
raw capture, discussion artifacts, configuration or sibling session outputs.

The engine removes ambient nare controls and unrelated authentication from
child environments, forwarding only the selected provider's credential when
needed for an authorized live run. A configured null base URL stays null even
with ambient `NARE_BASE_URL`. Each child has separate working, home and temporary
directories. Nare offers only `read`; its dispatch rejects fabricated write,
edit, bash and ask calls and refuses traversal, outside absolute paths and
symlink escapes. These tool rails are not an operating-system sandbox. Use a
trusted nare executable and an appropriate OS boundary for adversarial runtimes.
The offline proof additionally guards network, DNS, secondary processes and
writes outside the invocation artifacts, and forwards no real credentials.

## Admission and accounting

The ledger divides the smaller of the review allowance and the total persona
allowances fairly in configured order. All initial leases are reserved
synchronously before the first await. Only admitted sessions become concurrent
`asyncio` tasks. Zero allocations create explicit `not_started` records with
reason `review_budget` and no child process. Results preserve configured order
regardless of completion order. A failed persona retains its evidence without
preventing siblings from finishing.

Token totals count disjoint reported input, output, cache-read and cache-write
counters once. Actual use is never clipped. For example, a 25-token invocation
with two reported 15-token read turns stops at 30, retains the latest complete
schema-valid findings, records 5 tokens of overshoot and admits no third call.
A `done` response using 15 tokens under a limit of 1 is partial even though
nare exits 0. A successful `done` exactly at 15 under a limit of 15 may remain
complete, with further admission closed. The response output flag
`--max-tokens` does not impose a cumulative allowance.

Leases govern admission, and nare enforces thresholds after completed turns.
An already admitted invocation may finish internal turns while sibling use
exhausts the review allowance. Concurrent actual use can exceed the review
allowance; artifacts preserve that overshoot. There is no strict spending
ceiling or claim of complete provider billing. Exhausted or uncertain allowance
prevents future persona, retry, re-anchor or round admissions. Accounting
uncertainty remains distinct from known zero use and from validated empty
findings.

`ReviewBudgetLedger` supports cumulative high-water observations for future
strategies. Observing 30 and then 45 tokens on the same session charges only
15 more; repeated settlement does not double-charge. Fresh retries start from
zero and receive only remaining allowance. This release ships no resume
invocation adapter, retry loop or later rounds. The session decoder validates
fresh invocations; the supplemental real CLI resume test checks persisted
cumulative evidence directly against ledger baselines.

## Artifacts and confidence

A wave reserves a fresh private `sessions/` root exclusively. Existing wave
roots or `fanout.json`, including symlinks, fail without overwrite. An interrupted
or failed setup may leave evidence in the reserved root. Create a new capture
for a new wave; fan-out never silently reuses an old attempt.

Each `sessions/<persona>/attempt-0001/` contains the schema, invocation manifest,
`stdout.jsonl`, `stderr.txt`, nare's session document when available, and an
engine `result.json`. Engine records are owner-only, atomic and exclusive.
The final owner-only `fanout.json` binds schema and application versions, the
validated captured head, nare version/contract, ordered exact systems and model
rails, copied-input SHA256 digests, ordered outcomes and the ledger snapshot.
Provider keys are never included in these manifests.

Outcomes distinguish `complete`, `partial`, `failed` and `not_started`, with
caller-owned persona attribution. `output_available` distinguishes explicit
validated empty findings from unavailable output. Latest complete schema-valid
output survives later invalid text; earlier documents are not implicitly
aggregated. Findings remain candidates until code verifies their anchors.
The aggregate records failure, partial coverage, actual usage, review exhaustion
and overshoot. The ledger and per-session records expose accounting confidence.
Malformed or inconsistent protocol evidence fails and closes admission rather
than inventing an empty success.

Timeouts and caller cancellation terminate and reap owned process groups.
Cancellation waits for cleanup before propagating and does not publish a
completed wave manifest. Captures and prepared input bytes remain unchanged.
Provider failures retain logs and session evidence with uncertain accounting.
Nare 2026.10.0 records a denied fabricated ask call as a blocked terminal
session; the engine treats blocked sessions as failed without deriving a verdict.

## Offline installed CLI proof

Set `SCRUTARE_TEST_NARE_EXECUTABLE` to the absolute installed console path for
nare 2026.10.0, then run:

```sh
uv run --python 3.10 --locked --extra dev pytest tests/test_nare_cli_integration.py
uv run --python 3.14 --locked --extra dev pytest tests/test_nare_cli_integration.py
```

The helper uses that console script's separate Python 3.14 interpreter and
replaces only `nare.cli.make_transport` with a deterministic `FakeProvider`.
Parser, loop, policy/dispatch, schema validation, token accounting, JSONL and
persistence remain real. The proof verifies overlapping processes, isolated
contexts, exact systems/rails, filtered and empty input boundaries, tool denials,
after-turn budget stops, terminal overshoot, disjoint cache counts, cumulative
resume evidence, remaining retry grants, provider failure and process cleanup.
No network, credential or model access is needed.

Absent runtime configuration permits an explicit local skip. A configured
missing, nonexecutable or incompatible runtime fails. CI installs the pinned
public release outside the checkout and project virtual environment using its
own locked dependencies, requires the executable setting, and runs the proof
in both existing Python jobs. Both jobs retain environment, test, lint, strict
type, wheel and installed CLI gates. A skip is not proof that this boundary works.
