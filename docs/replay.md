# Offline replay and audit

`scrutare replay <dir>` recomputes a verdict from captured artifacts and reports
whether its canonical bytes match the saved verdict and the locally recorded
posted verdict. It requires no `gh`, authentication, model, network, repository
checkout or current-directory configuration. It reads the supplied directory
without writing artifacts, taking locks, refreshing PR lifecycle, or recovering
delivery. The `review` command still captures inputs only.

```sh
scrutare replay "/path/to/run artifacts"
python -m scrutare replay "/path/to/run artifacts"
```

The Python API exposes the same result:

```python
from pathlib import Path
from scrutare.replay import ReplayError, replay_run

result = replay_run(Path("/path/to/run artifacts"))
print(result.to_dict())
print(result.exit_code)
```

Malformed required findings or policy raise `ReplayError` with safe artifact or
field diagnostics. The CLI prints that diagnostic to stderr and exits 2 without
a traceback. When an audit result is available, including an incomplete result,
the CLI prints one deterministic JSON object to stdout and uses its exit code.
Finding text and controls in differences are JSON-escaped. Usage errors exit 2.

## Required evidence and authority

An ingestion-only run has no verdict to replay. A caller of the findings and
posting APIs must first have saved these structural artifacts:

| Artifact | Required structure and role |
| --- | --- |
| `findings.json` | Ordered array of merged finding objects, including every ordered source occurrence |
| `config.json` | Captured effective policy, with explicit `verdict.blocking_categories` and `verdict.advisory_categories` |
| `verdict.json` | Saved schema-version-1 verdict, its policy and classified findings, optionally including recorded exhaustion |

Each merged finding has `file`, `line`, `side`, `problem`, `categories`,
`personas`, `reasons`, and `sources`. Every source has its own `file`, `line`,
`side`, `category`, `problem`, `reason`, and `persona`. These are the
[`MergedFinding.to_dict()` artifact fields](findings.md#lossless-dedupe-and-artifact-data),
including duplicates. Sources decide classification; redundant categories,
personas and reasons are checked and reported if inconsistent. Replay preserves
group order, source order, original text and anchors instead of deduplicating
the stored groups again.

The captured policy must partition all six categories without overlap,
duplicates or omissions. Replay uses that policy rather than current defaults,
the saved verdict's classification, or model output. Blocking sources derive
`changes_requested` and `any_blocking_finding`; none derive `approve` and
`no_blocking_findings`. An explicit recorded exhaustion assertion overrides
these outcomes as described below. Saved verdict and rule fields remain the
comparison baseline; they never decide the candidate outcome.

`config.yaml` is optional. When present, explicitly supplied verdict categories,
strategy and round bound are checked against `config.json`. Omitted YAML fields
do not introduce current defaults. Unrelated model rails and config fields are
not executed or validated as a live review configuration. Replay does not apply
path filters, reverify diff anchors, rerun re-anchoring, launch model sessions,
or reassess whether a finding is correct.

Known formats must be structurally valid UTF-8 JSON. Duplicate object keys,
nonfinite numbers, malformed fields, unsupported verdict or delivery schema
versions, and required leaf symlinks or nonregular files cannot support a clean
audit. Compatibility follows artifact format, not the producing or installed
package version: a future producer version and unrelated extra files alone do
not fail replay.

## Saved and locally recorded posted targets

`saved_verdict.byte_identical` compares the candidate's canonical UTF-8 JSON
bytes with the exact current `verdict.json` bytes. Canonical serialization uses
sorted keys, two-space indentation and a trailing newline. Equivalent JSON with
different whitespace, Unicode escaping, or a missing newline is a byte
difference, reported as `verdict.encoding` when there is no semantic difference.

`posted_verdict.byte_identical` separately compares the candidate SHA-256 with
the original verdict digest in validated local delivery evidence. Replay
inspects the original `posting.json`, exact `review-payload.json`, and confirmed
receipt for consistent target, captured head, run marker, event, payload hash,
body and receipt identity. It does not rebuild that original payload from
edited findings or configuration. Changing findings and replacing the saved
verdict can yield saved identity while still differing from the posted target.
Formatting only the saved verdict can break saved identity while retaining
posted identity. Detailed finding and rule differences compare the candidate
against the saved baseline; an original digest alone cannot recover original
finding text.

If both delivery artifacts are absent, replay is artifact-only:
`posted_verdict.status` is `absent` and its digest and comparison are null.
Saved identity can still produce exit 0; it does not claim posted identity.
Orphaned, malformed or inconsistent delivery records leave the posted target
unavailable and produce an invalid or incomplete issue. Valid review states
`prepared`, `sending`, `unknown` and `rejected` also lack a confirmed posted
target and produce exit 2. Replay never sends, reconciles or retries a review.

Historical `metadata.json` and raw `diff.patch` are optional for this digest
comparison. Present evidence is checked against original capture identity and
hashes, including the effective canonical config hash. Disagreements are
reported independently of verdict identity. Unreadable optional captures produce
an incomplete issue while retaining any independently validated original
digest. Missing historical captures do not prove that anchors were verified.

For escalation, the optional `escalation.json` and `reviewer-request.json` pair
must agree with the original confirmed COMMENT review, its receipt hash and
normalized original targets. Version 1 records targets in the original
escalation summary header; replay checks that header and request together,
without falling back to edited current config. Both request artifacts absent
is legitimate for the lower-level `post_review` API.

Reviewer requests have their own `reviewer_request_status`: `absent`, `posted`,
`observed_requested`, `skipped`, `prepared`, `sending`, `unknown`, `rejected`,
or `invalid`. `posted` records a successful POST response;
`observed_requested` records a recovery observation without claiming this run
caused the requests; `skipped` records empty targets. Pending request delivery
can coexist with identical confirmed posted verdict bytes and exit 0. An invalid
request pair or cross-stage link invalidates posted provenance. These statuses
describe stored evidence, not current GitHub state. See the
[escalation guide](escalation.md#artifacts-and-restart).

## Recorded exhaustion and the trust boundary

For an escalated replay, `verdict.exhaustion` is a supplemental recorded
assertion with `strategy`, `rounds_completed`, `round_limit`, and `converged`.
It must represent a completed positive bound with `converged: false` and match
captured `config.json` strategy and `rounds.max`. Code independently recomputes
findings and policy, then derives `escalated` with
`rounds_exhausted_without_convergence` conditional on that validated assertion.
An unavailable baseline or conflicting bound produces an incomplete result
with no candidate; replay does not guess ordinary approval.

`exhaustion_basis` is `none` without exhaustion. It is `recorded_assertion` when
validated original posted evidence binds the exact saved exhaustion-bearing
bytes. Otherwise it is `unverified_recorded_assertion`, including artifact-only
captures and changed assertions. Unreadable optional metadata or diff does not
erase that original binding, although the overall audit remains incomplete.

Neither label proves genuine strategy execution or nonconvergence. Receipts and
hashes establish consistency within a local unsigned bundle; a fully coherent
rewrite of its history cannot be detected. Replay does not verify remote
delivery, continued PR state, actual model calls, or the quality of findings.
The approved conditional exhaustion rule is specified in
[SPEC section 9](SPEC.md#9-replay-and-audit).

## Output and exit codes

The result object has `schema_version: 1`, a `status` string, candidate `verdict`
and `rule` strings, `recomputed_sha256`, separate `saved_verdict` and
`posted_verdict` comparison objects, `exhaustion_basis`,
`reviewer_request_status`, `differences`, and `issues`. An unavailable candidate
or comparison uses null. Differences name their `path`, `kind`, `before`,
`after` and, where applicable, the affected `finding`. Issues name their `code`,
`path` and `severity`.

| Exit | Status | Meaning |
| --- | --- | --- |
| 0 | `identical` | Every available comparison matches, saved target is available, and there are no audit issues |
| 1 | `different` | An available comparison differs, or named semantic, encoding or evidence differences exist |
| 2 | `incomplete` | Required baseline or candidate is unavailable, or evidence is incomplete or invalid |

Incomplete or invalid evidence takes priority over known differences. Exit 0
with posted comparison null confirms only the saved target. Exit 0 with a
pending reviewer request confirms verdict identity while request delivery
remains pending. Argument errors and required-input `ReplayError` also use
exit 2, with stderr diagnostics instead of a result object.
