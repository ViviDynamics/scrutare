# Focused discussion development measurements

Read `automated-focused-report.md` for the provisional measured results. All
240 scheduled development cells remain in the denominator, including failures.
Judgments are automated; unresolved matches remain pending actual humans. No
human adjudication or default promotion is claimed.

The frozen source was `5736bc1ce4ad02c1b19acfa9f318d7ae9acfbc50`. Reconstruct its
exact tracked source from a remote-known base and the published binary patch:

```sh
git clone https://github.com/ViviDynamics/scrutare.git scrutare-focused-source
cd scrutare-focused-source
git checkout eb372770b9b1371bbd566ba7b33e69c1a0a1bc51
git apply --binary --index /absolute/path/to/2026-10-12-focused/candidate-source.patch
uv sync --python 3.10 --locked --extra dev
```

`run-provenance.json` identifies the exact source patch/archive/wheel hashes,
configuration and limits; `published-sha256.json` binds retained public artifacts.
Native session captures remain private and locally retained. Complete provider
transport audits require those captures. Only the source patch is published;
archive and wheel hashes identify separately retained local artifacts.

Reproduce production scoring from the current repository root, choosing a fresh
output path and making no model calls:

```sh
python -m scrutare.evaluation score \
  evaluations/reports/2026-10-12-focused/experiment.json \
  --corpus evaluations/pilot \
  --decisions evaluations/reports/2026-10-12-focused/automated-decisions.json \
  --output /tmp/scrutare-focused-score.json
```

An explicitly paid, nondeterministic model rerun uses the reconstructed source,
a private copy of `evaluations/pilot` with `corpus-manifest.json`, the recorded
`evaluation.json`, separately installed nare 2026.10.4, and
`run --comparison-set focused --split development --concurrency 3 --max-turns 6
--timeout-seconds 120`. Supply provider credentials externally; no values are
retained here. The four arms share context/v1 procedures/v2 claims and a 40,000
total ceiling. Assessment reserves 8,000, and focused perspectives reuse their
original discovery grants; no chair or fresh round grants are added.

The aliased gpt-4.1-nano backend revision was not established. Its protocol
reliability and the small synthetic development corpus limit any quality
comparison. These runs do not expose the new held-out corpus. Original holdout
results were previously observed and cannot establish unseen validation.
Compact focused traces retain measured rounds, token deltas, latency, bypasses,
new citation identities and independent disposition changes; missing traces stay
unknown. Mechanically checked references and model dispositions are not semantic
truth. Failed runs must not be read as clean reviews or evidence for promotion.

Public-only decisions and paired/domain/repeat tables reproduce into fresh outputs:

```sh
python evaluations/reports/2026-10-12-focused/adjudicate-focused.py \
  --corpus evaluations/pilot --output /tmp/scrutare-focused-judgments
python evaluations/reports/2026-10-12-focused/report-focused.py \
  --corpus evaluations/pilot --output /tmp/scrutare-focused-tables
```

These scripts recompute quality tables and import normalized diagnosis summaries,
without private sessions or `.agents` reads. Native failure counts remain unsigned
retained summaries and cannot be independently authenticated without private
captures. Public run directories are relative identities and executable metadata
is a nare version reference. Metadata-only transformations preserve stable finding
IDs and scoring; original hashes remain in `original-publication-sha256.json`.
Original private job files remain intact.
