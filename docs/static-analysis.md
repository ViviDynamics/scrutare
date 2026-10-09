# Captured static-analysis evidence

Scrutare can include captured Ruff 0.16.10 diagnostics as optional review evidence.
It does not run Ruff, PR commands, plugins, or application code. A trusted caller
collects tool output separately and supplies a capture explicitly:

```yaml
context:
  enabled: true
analysis:
  enabled: true
```

```sh
scrutare review --pr 12 --analysis-capture /protected/ci-evidence.json
```

Repository configuration cannot specify a local capture path. Supplying one when
analysis is disabled is an error. Existing configurations and prompts retain their
original behavior when analysis is disabled.

The capture envelope is JSON with exactly these fields:

```json
{
  "schema_version": 1,
  "revision": "0123456789012345678901234567890123456789",
  "tool": {"name": "ruff", "version": "0.16.10"},
  "status": "complete",
  "duration_seconds": 0.25,
  "source_root": "/captured/repository",
  "diagnostics": []
}
```

`diagnostics` contains Ruff JSON objects with `code`, `message`, `filename`,
`location` and `end_location` (each position has positive integer `row` and
`column`). Known optional `fix`, `url`, `cell`, and `noqa_row` fields are validated
and remain private. Fixes are never applied. Absolute filenames must lie below the
explicit source root; relative filenames must be safe repository paths.

The revision must equal the immutable captured PR head. Only diagnostics whose
paths and positions bind retained head context enter `static-analysis.json` in the
read root. Sensitive, excluded, and otherwise uncaptured locations are counted as
omitted; their messages remain outside the model root. The normalized evidence
includes the captured text SHA-256, rule, range, source-envelope hash, revision,
status, and separately reported tool runtime.

Raw input is bounded to 1 MiB, at most 1,000 diagnostics, and bounded strings and
positions. Normalized evidence is separately bounded to 1 MiB; exceeding that
bound retains a deterministic prefix and records explicit truncation. Duplicate
JSON keys, nonfinite values, malformed fields, symlinks, escaping paths, unsupported
tool versions, and mismatched revisions are rejected. Validated original bytes are
retained privately at `static-analysis/source.json` with owner-only permissions.

Missing captures are explicitly `unavailable`. Captures may also report `failed`
or `truncated`; their valid diagnostics remain partial evidence. Tool silence is
not proof of correctness. Diagnostics do not automatically become findings, clear
existing findings, or block a verdict. Reviewers consume them under the existing
shared model-token budget. Tool runtime is distinct from model latency and usage.

Capture provenance is caller supplied and unsigned. Hashes establish local
consistency, not that a trusted producer ran a particular tool. Prepared-input
validation recomputes the normalized view; replay checks the same binding offline.
Iterative history binds the entire tool manifest, including status and source hash,
so changed or missing evidence invalidates carryover conservatively.

Evaluation cases can explicitly declare `analysis_capture` as a corpus-relative
JSON path outside capture directories and evaluator labels. The runner freezes
those bytes before asynchronous runtime inspection, records their hash, and feeds
all repeats through the production adapter. Comparative quality benefit remains
unmeasured until the frozen evaluation and promotion gate in #51; defaults remain
unchanged.
