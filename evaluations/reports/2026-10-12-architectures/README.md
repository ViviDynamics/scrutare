# Architecture development measurements

Read `automated-architecture-report.md` for the provisional measured results. This
publication retains all 420 scheduled development cells, including failures and
incomplete accounting. Judgments are automated; genuinely ambiguous observations
remain pending actual human adjudication. Zero human judgments and no default
promotion are claimed.

The measured source was `5fae2b2cb8160a391eb1eff003d21f1666a6a1f1`, preceding
#49 integration. This local source commit was never pushed. Reconstruct its exact
tracked source from the remote-known base and retained patch:

```sh
git clone https://github.com/ViviDynamics/scrutare.git scrutare-architecture-source
cd scrutare-architecture-source
git checkout 9e0c38873830137b37724d48785549e72a19289c
git apply --binary --index /absolute/path/to/2026-10-12-architectures/candidate-source.patch
uv sync --python 3.10 --locked --extra dev
```

`run-provenance.json` records the patch/archive/wheel hashes, frozen source/model
rail, schedule and resource limits. `published-sha256.json` binds the published
artifacts. The full native session captures remain private and locally retained;
complete transport auditing requires those captures. Only the source patch is
bundled, while the original source archive and candidate wheel hashes identify
separately retained local artifacts.

Reproduce production scoring from the current repository root without model
calls, after choosing a fresh output path:

```sh
python -m scrutare.evaluation score \
  evaluations/reports/2026-10-12-architectures/experiment.json \
  --corpus evaluations/pilot \
  --decisions evaluations/reports/2026-10-12-architectures/automated-decisions.json \
  --output /tmp/scrutare-architectures-score.json
```

For an explicit model rerun, copy `evaluations/pilot` to a private directory,
replace its manifest with `corpus-manifest.json`, and copy `tool-captures` into
that copy as `tools`. Those envelopes retain actual Ruff 0.16.10 diagnostics and
source hashes, not fabricated static analysis. Use the reconstructed source,
`evaluation.json`, the separately installed nare 2026.10.4 runtime, and
`run --comparison-set architectures --split development --concurrency 3
--max-turns 6 --timeout-seconds 120`. Provision the provider credential externally;
no credential value is included here. Reruns incur model charges and are
nondeterministic. Do not reuse the original holdout as unseen validation.

The aliased gpt-4.1-nano rail's dated backend revision was not established.
The small original synthetic development corpus limits generalization. All 60
routed runs retained the original four reviewers; specialist activation and its
quality effect were not tested. Protocol/schema/citation/budget failures are
execution failures rather than adjudicated clean reviews. Precise captured
identity validation remains intact. These results do not demonstrate that evidence
assessment, focused discussion or specialists improve review quality on a capable
model rail. Missing accounting makes usage a lower bound, and tool collection
runtime is an additional cost beyond measured model-review latency.

Portable adjudication and paired/domain/repeat quality tables reproduce without
private sessions or `.agents` access:

```sh
python evaluations/reports/2026-10-12-architectures/adjudicate-architectures.py \
  --corpus evaluations/pilot --output /tmp/scrutare-architecture-judgments
python evaluations/reports/2026-10-12-architectures/report-architectures.py \
  --corpus evaluations/pilot --output /tmp/scrutare-architecture-tables
```

The adjudication script reproduces the retained automated decisions from recorded
case dispositions and checked source/control hashes; it does not perform fresh
human judgments. Source/control results are bounded behavioral checks rather than
formal proof. The reporting script recomputes quality metrics and imports retained
normalized diagnostic counts; it cannot reconstruct or authenticate private native
session failure events. Original private artifact hashes are retained separately
in `original-publication-sha256.json`. Public metadata removes credential references
and command paths; portable scripts and normalized control metadata have new hashes.

Published experiment directories are relative run identities, and the snapshot's
executable is a version reference. These metadata-only sanitizations preserve
finding IDs, source/input hashes, outcomes and scoring. Original experiment and
snapshot hashes remain in `original-publication-sha256.json`; public artifact
hashes bind the sanitized copies. No public reproduction reads private paths.
