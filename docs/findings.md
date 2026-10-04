# Findings pipeline

`scrutare.findings` provides pure Python functions for parsing findings and
diffs, verifying anchors, finalizing one supplied correction round, and
deduplicating the survivors. Its frozen data classes validate direct construction
as well as wire input. These functions perform no I/O or model calls.
Model sessions, collection, verdict derivation, posting, and artifact persistence
remain later stages. The design authority is [SPEC.md](SPEC.md).

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

## Runnable composition

From a checkout with development dependencies installed, run the following
shell block. The example supplies two corrections as caller data, demonstrates
both terminal drop reasons, then merges only the verified survivors:

```sh
uv run --locked --extra dev python - <<'PY'
import json

from scrutare.findings import (
    Anchor,
    ReanchorCorrection,
    check_anchors,
    dedupe_findings,
    finish_reanchor,
    parse_diff,
    parse_finding,
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
PY
```

Expected output: `accepted=2 dropped=2 merged=1`.
