# Findings pipeline

`scrutare.findings` provides pure Python functions for parsing findings and
diffs, verifying anchors, finalizing one supplied correction round, and
deduplicating the survivors, then deriving a verdict from configured categories.
Its frozen data classes validate direct construction as well as wire input.
Derivation and serialization perform no I/O or model calls. The separate
`write_verdict` helper persists an artifact in an existing run directory.
Model sessions, collection, and posting remain later stages.
The design authority is [SPEC.md](SPEC.md).

## Input and attribution

Call `parse_finding(data, persona="security")` with a mapping containing only
these fields:

| Field | Accepted value |
| --- | --- |
| `file` | Nonempty repository-relative Git/POSIX path without empty, `.` or `..` components, a leading `/`, or NUL |
| `line` | Positive integer; booleans are rejected |
| `side` | Optional `LEFT` or `RIGHT`; defaults to `RIGHT` |
| `category` | One of `scrutare.config.CATEGORIES`: correctness, security, regression, style, consistency, docs |
| `problem` | Nonempty text describing the observation |
| `reason` | Nonempty text explaining its impact |

`problem` and `reason` must contain non-whitespace text and are preserved exactly.
The nonempty `persona` argument is caller-owned attribution, such as the name of
the session that produced the mapping. A model cannot supply `persona`, severity,
approval, or verdict fields. Unknown fields and malformed values raise
`FindingError` with field diagnostics that omit untrusted values.

## Diff sides and paths

`parse_diff(diff)` accepts unified Git patch bytes or text from the established
UTF-8 captured GitHub diff contract and returns a `frozenset[Anchor]`. Raw bytes
must be UTF-8, and Git-quoted filenames must decode as UTF-8. Malformed or
unsupported encodings raise `FindingError`. Only lines present in hunks are
anchors. `RIGHT` uses new-file line numbers for additions and context; `LEFT` uses old-file line numbers for
deletions and context. The side is part of anchor identity, so an added line
cannot validate a `LEFT` finding merely because its number matches.

Paths compare exactly after Git filename decoding. Use the new path on both
sides of modified or renamed files, and the old path for deleted files. Paths
follow Git/POSIX semantics: backslashes are filename characters, not directory
separators. Spaces, Unicode, and Git-quoted filename characters are retained.
Binary and metadata-only changes contribute no anchors; unchanged files do not
appear in the index. Invalid paths, malformed hunk counts, and unsupported
combined diffs raise `FindingError` rather than accepting uncertain anchors.

## One correction round

`check_anchors(findings, anchors)` captures the immutable index and caller order.
Its `accepted` tuple contains valid findings, and `reanchor_requests` contains
each distinct invalid original once. The caller can obtain replacement anchors
externally and supply `ReanchorCorrection(original, anchor)` values to
`finish_reanchor(check, corrections)`.

A correction changes only the anchor. Category, problem, reason, and persona
remain those of the original. Corrections must name requested originals and
there can be at most one per equal original; corrections for accepted or unknown
findings and repeated corrections raise `FindingError`. Equal duplicate input
occurrences share a correction but retain all their source occurrences.

`finish_reanchor` checks replacements against the captured index and returns a
terminal `VerificationResult`. Missing corrections drop with `missing_correction`;
still-invalid replacements drop with `invalid_correction`. `dropped` entries
carry the original finding and a fixed reason. There is no further request or
internal retry. The caller supplies the single correction round; these functions
do not invoke a model or callback.

## Lossless dedupe and artifact data

Pass `VerificationResult.accepted` to `dedupe_findings`. It groups only exact
anchors plus whitespace-normalized problem text (`" ".join(problem.split())`).
Case, punctuation, and wording remain significant. There is no fuzzy or semantic
merge. Group order follows the first occurrence, the first problem text remains
unchanged, and every input finding remains in the group's `sources` tuple in
input order, including equal duplicates.

`MergedFinding(anchor, problem, sources)` is frozen and validates a nonempty tuple
of findings with matching anchors and normalized problems. Its `categories`
tuple includes every source category in `CATEGORIES` order; `personas` and
`reasons` contain unique exact values in first-seen order. An advisory duplicate
cannot erase a correctness, security, or regression category. Dedupe does not
choose a category winner or compute a verdict.

`to_dict()` returns fresh JSON-compatible artifact data with `file`, `line`,
`side`, `problem`, `categories`, `personas`, `reasons`, and `sources`. Each source
contains its own `file`, `line`, `side`, `category`, `problem`, `reason`, and
caller-owned `persona`. These are output artifact fields, not accepted model
wire fields. Lists and source dictionaries are recreated on every call, so a
caller can prepare a future artifact without mutating the merged finding.

## Verdict derivation and persistence

Call `derive_verdict(merged, config.verdict)` with deduplicated, verified
survivors and a `VerdictSettings` policy. This API accepts verified survivors;
it does not verify anchors or reanchor findings itself. It snapshots the input
in a frozen `Verdict`, retaining every merged group and source in caller order.
Models cannot supply a verdict or classification policy.

Any source category in `blocking_categories` makes the verdict
`changes_requested`, with rule `any_blocking_finding`. Otherwise the verdict is
`approve`, with rule `no_blocking_findings`, including when no findings survive.
Advisory-only groups remain in the artifact and never block. A merged advisory
source cannot erase a blocking source.

By default, correctness, security, and regression block; style, consistency,
and docs are advisory. The configured blocking and advisory categories must
partition exactly `scrutare.config.CATEGORIES`, without overlap or missing
categories. `VerdictSettings` validates this for programmatic construction as
well as loaded configuration. Reclassification follows the supplied policy,
so the same verified evidence can produce a different verdict. The
[replay CLI](replay.md) recomputes from captured source evidence and policy.

`Verdict.to_dict()` returns fresh artifact data with these fields:

| Field | Value |
| --- | --- |
| `schema_version` | `1` |
| `verdict` | `approve` or `changes_requested` |
| `rule` | `no_blocking_findings` or `any_blocking_finding` |
| `config` | The effective `blocking_categories` and `advisory_categories` lists |
| `findings` | Every merged group's existing artifact fields, plus `blocking` and `blocking_categories` |

Each group's `blocking_categories` contains its source categories that block
under the effective policy. Categories use `CATEGORIES` order; source order
and all persona, category, reason, original problem, anchor, and side evidence
are preserved. `Verdict.to_bytes()` serializes the same data as UTF-8 JSON with
sorted keys, two-space indentation, and a trailing newline.

`write_verdict(run_dir: Path, verdict: Verdict) -> Path` writes those canonical
bytes to `run_dir / "verdict.json"`. It requires an existing run directory and
validates and encodes the typed verdict before starting filesystem operations.
A complete temporary sibling replaces the destination atomically. Repeat
writes are byte-identical; changed evidence or policy replaces the old artifact.
The helper leaves unrelated run artifacts in place and performs no network,
subprocess, or model calls. It does not create a new run.

Invalid input, encoding errors, missing directories, and filesystem failures
raise `VerdictArtifactError`, a `ValueError` with safe, actionable diagnostics
that omit finding text, paths, and underlying error details. Failed writes or
replacements preserve any prior `verdict.json` and remove the temporary sibling.
If filesystem permissions also prevent temporary-file cleanup, the error
identifies that cleanup failure.

## Runnable composition

From a checkout with development dependencies installed, run the following
shell block. The example supplies two corrections as caller data, demonstrates
both terminal drop reasons, then merges only the verified survivors. It derives
and persists a verdict in a caller-owned temporary run, then reclassifies the
same evidence and replaces the artifact:

```sh
uv run --locked --extra dev python - <<'PY'
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from scrutare.config import VerdictSettings
from scrutare.findings import (
    Anchor,
    ReanchorCorrection,
    check_anchors,
    dedupe_findings,
    derive_verdict,
    finish_reanchor,
    parse_diff,
    parse_finding,
    write_verdict,
)

diff = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,2 @@
 def run():
-    return 0
+    return 1
"""
anchors = parse_diff(diff)
wire = {
    "file": "src/main.py",
    "line": 2,
    "category": "style",
    "problem": "Wrong result",
    "reason": "The return value is misleading",
}
advisory = parse_finding(wire, persona="junior-dev")
needs_correction = parse_finding(
    {**wire, "line": 99, "category": "correctness", "problem": "Wrong\tresult",
     "reason": "Existing callers require zero"},
    persona="senior-dev",
)
missing = parse_finding({**wire, "file": "unseen.py"}, persona="security")
still_invalid = parse_finding({**wire, "line": 100}, persona="devops")
check = check_anchors([advisory, needs_correction, missing, still_invalid], anchors)
assert len(check.reanchor_requests) == 3
result = finish_reanchor(check, [
    ReanchorCorrection(needs_correction, Anchor("src/main.py", 2)),
    ReanchorCorrection(still_invalid, Anchor("src/main.py", 101)),
])
assert tuple(drop.reason for drop in result.dropped) == (
    "missing_correction", "invalid_correction",
)
merged = dedupe_findings(result.accepted)
assert len(merged) == 1
assert merged[0].categories == ("correctness", "style")
assert merged[0].personas == ("junior-dev", "senior-dev")
assert len(merged[0].sources) == 2
artifact = json.loads(json.dumps(merged[0].to_dict()))
assert artifact["sources"][1]["problem"] == "Wrong\tresult"
print(f"accepted={len(result.accepted)} dropped={len(result.dropped)} merged={len(merged)}")
verdict = derive_verdict(merged, VerdictSettings())
assert verdict.verdict == "changes_requested"
assert verdict.rule == "any_blocking_finding"
reclassified = derive_verdict(merged, VerdictSettings(
    blocking_categories=("security", "regression"),
    advisory_categories=("correctness", "style", "consistency", "docs"),
))
assert reclassified.verdict == "approve"
assert reclassified.rule == "no_blocking_findings"
assert reclassified.findings == verdict.findings
with TemporaryDirectory() as directory:
    run_dir = Path(directory)  # An existing directory supplied by the caller.
    (run_dir / "diff.patch").write_text(diff, encoding="utf-8")
    destination = write_verdict(run_dir, verdict)
    assert destination.read_bytes() == verdict.to_bytes()
    assert write_verdict(run_dir, verdict).read_bytes() == verdict.to_bytes()
    write_verdict(run_dir, reclassified)
    saved = json.loads(destination.read_bytes())
    assert destination.read_bytes() == reclassified.to_bytes()
    assert saved["findings"][0]["personas"] == ["junior-dev", "senior-dev"]
    assert len(saved["findings"][0]["sources"]) == 2
    assert (run_dir / "diff.patch").read_text(encoding="utf-8") == diff
    assert sorted(path.name for path in run_dir.iterdir()) == ["diff.patch", "verdict.json"]
    print(f"artifact={destination.name} verdict={saved['verdict']} rule={saved['rule']}")
PY
```

Expected output:

```text
accepted=2 dropped=2 merged=1
artifact=verdict.json verdict=approve rule=no_blocking_findings
```

## Opt-in evidence version 2

Set `context.enabled: true` and `findings.evidence: v2` to require structured
model assertions (`trigger`, `preconditions`, `expected`, `observed`, `impact`)
and nonempty context citations in every initial finding. Legacy is the default;
its historical wire and artifact fields remain unchanged. Legacy observations
have no evidence assessment or retroactively assigned candidate ID.

Each citation supplies `side: base|head`, the captured 40-digit commit `revision`,
a logical repository `path`, inclusive positive `start_line`/`end_line`, and the
SHA256 of the entire captured UTF-8 file. The code checks these against the
prepared repository manifest and exact retained bytes. A successful check is
serialized as `validation: valid`, with `validation_reason: null`. This label
checks citation identity and range, **not the truth of the model's assertion**.
A missing, omitted, wrong-revision, wrong-hash or out-of-range citation fails the
session and cannot feed the verdict. Raw nare stdout/session evidence and a
`citation-validation.json` refusal remain available privately.

`Finding.evidence` contains a frozen `EvidenceV2` with frozen `Citation` values;
`candidate_id` is assigned by the caller from the capture/session occurrence.
The model cannot supply either validation labels or initial candidate IDs.
Reanchoring changes only the diff comment anchor. Dedupe retains every source,
evidence record and ID. V2 debate output selects immutable IDs and categories;
it cannot rewrite citations or assertions. Iterative history schema 2 preserves
IDs and evidence, including distinct equal observations. A revision change
requires fresh citations and conservative re-review; if usable current citations
cannot be obtained, their original citations and blocking claims stay in the
history pool with `dependency_status: stale`. The review remains partial and
publishes no current verdict while another bounded attempt is available. At the
round limit, code produces an escalated verdict from current validated sources;
historical stale sources remain in `iterative.json`, outside current inline
findings. A complete reassessment can refresh or explicitly withdraw them.
Neither incomplete reassessment nor exhaustion silently approves the PR.

V2 verdicts use schema 2, including empty verdicts. Replay selects the evidence
contract from captured configuration, rechecks citations against captured bytes,
and rejects mixed legacy/v2 sources and incompatible verdict versions. Posting
renders quoted assertions and citations in the existing escaped comment body;
it adds no GitHub API fields. These checks establish integration and mechanical
validity, not measured improvements in review quality.
