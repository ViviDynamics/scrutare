# Captured review evaluation

This opt-in evaluator calls the same production `run_review` strategy through
external nare. It uses local captures and never enters GitHub capture or posting.
The original pilot under `evaluations/pilot` contains 20 defect cases and 10 clean
controls across five domains; ten cases (seven defects, three clean) are held out.
It is a pilot, not statistical evidence for a generally superior reviewer roster.

Run an explicit paid model job from a locked installation:

```sh
python -m scrutare.evaluation run --corpus evaluations/pilot --split development \
  --config evaluation.yaml --output /absolute/new-experiment \
  --nare-executable /absolute/installed/bin/nare --concurrency 3
```

Use a dated model identifier and fixed provider settings in the ordinary review
configuration. Model overrides are refused for comparisons. The evaluator freezes
configuration, persona prompts, nare version/contract, runtime bounds, corpus hashes,
and the expected run schedule before execution. Capture bytes are frozen before
the first runtime await, and snapshot hashes and every queued repeat use the same
frozen data. Later edits to the original corpus cannot silently alter a repeat. Each of three repeats compares
senior-dev alone, the current four personas in panel, and those personas in debate.
The total review token ceiling is identical. Senior gets the entire ceiling; panel
gets one quarter per persona; debate gets one fifth, including its chair. These are
admission limits; actual reported overshoot is retained in engine artifacts. Output
includes actual usage (including cache counters), latency, status and accounting
confidence for every run. Interrupted jobs recover observed counters from individual
session outcomes even when cancellation prevents a panel aggregate from being written.
Aggregate and session counters are compared as cumulative evidence, never added
together; incomplete accounting remains an explicit lower bound. Engine evidence and artifact manifests remain replayable.

Only diff, files, and captured metadata are copied to run artifacts. Production input
preparation restricts nare's read tool to the filtered `review-inputs` directory.
Gold labels and base/head fixture trees are evaluator-only files outside that root.
The cross-file pilot intentionally includes dependencies absent from initial diff
context, exposing the current system's limitations rather than hiding them.

After execution export a blinded packet:

```sh
python -m scrutare.evaluation adjudicate /absolute/new-experiment/experiment.json \
  --output /absolute/packet.json
```

A human compares each packet finding with the case's trigger, impact, evidence,
match criteria, and fix/counterexample. Packet finding identities bind exact original
contents and ordinal to the run while withholding variant, model, repeat and persona.
Reviewers should work from packets before receiving the experiment mapping. For
ambiguous allegations, independently check the reproduction and record the decision
and rationale; do not use an LLM as sole ground truth. A decision JSON list contains:

```json
[{"finding_id":"sha256 from packet","defect_id":"case-specific defect id or null",
  "reviewer":"reviewer identity","method":"human","ambiguous":false,
  "rationale":"verified trigger or counterexample"}]
```

For clear criteria an automated decision may set `method` to `automated`; this
provenance is retained in metrics and cannot substitute for human adjudication when
`ambiguous` is true. `null` means a judged false positive. An absent decision remains pending, never
silently false or true. Labels are author-defined pilot hypotheses until independent
human verification; no human adjudication is fabricated by the runner.

```sh
python -m scrutare.evaluation score /absolute/new-experiment/experiment.json \
  --corpus evaluations/pilot --decisions /absolute/decisions.json \
  --output /absolute/metrics.json
```

Scoring exposes raw TP/FP counts, judgment method counts, pending judgments and refuses stale finding identities or changed
label snapshots. Precision counts judged emitted source observations. Defect recall
counts distinct labelled defects per case/repeat, with failed/missing runs remaining
in its denominator. Duplicate rate is redundant matched source observations divided
by judged findings. Unique detections count defects reported by exactly one persona
per run. Tokens per true positive divides all observed tokens, including failures,
by distinct detected defects. Unknown accounting makes these observed tokens a lower
bound, visible through accounting failure counts. Clean-PR false-positive rate uses
only complete, accounted, fully adjudicated clean runs; partial or missing output is
never a clean approval. These denominator choices and repeat weighting must accompany
any reported comparison. No eligible denominator produces a null metric.

Concurrency is bounded (default one, at most 32 independent runs). `experiment.json`
is atomically refreshed per completion with schedule-ordered missing placeholders
and an explicit incomplete status until every cell finishes.

Interrupted experiments can be scored from `runs.jsonl` instead of `experiment.json`;
the frozen schedule supplies explicit missing records. `--split holdout` must be a
fresh frozen job after development decisions; do not tune prompts from holdout results.

Ordinary CI uses `--evidence-kind offline` and deterministic provider substitutions
to verify orchestration and scoring. Offline metrics measure software behavior only.
A real baseline requires explicit model jobs and completed independent human
adjudication. An available provider alias may change its backend; retain the dated
provider model/version evidence alongside snapshots before making reproducibility
claims. This implementation alone makes no model accuracy claim.

Contextual experiments enable the normal `context` configuration and use the same
production capture, preparation and review engine as hosted reviews. Each corpus
case must explicitly declare `context_sources` for its base and optionally head:

```json
{"context_sources": {"base": {"directory": "sources/example/base",
 "repository": "pilot/local", "revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
 "head": {"directory": "sources/example/head", "repository": "pilot/local",
 "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}
```

Declarations must match captured revisions and any existing repository identities.
A declared head identity can fill an absent identity only in derived evaluator
metadata; `metadata.original.json` retains the original bytes and the experiment
snapshot records the transformation and original capture hashes. Without a declared
head, an absent captured head identity remains unavailable. Hosted ingestion never
infers a missing fork identity. Synthetic corpus revision labels bind local source
snapshots and do not establish hosted commit authenticity.

Source directories stay outside the guarded root and cannot contain any corpus
labels or captures. The loader rejects symlinks and special files and caps each
side at 4,096 inventory entries, depth 32, 1 MiB per file and 64 MiB total. All source
bytes freeze before runtime inspection or any model call; the snapshot records
content inventory hashes and real Git blob/tree hashes. Normal context configuration
bounds further limit text retained for reviewers. Only selected immutable artifacts
enter the read root. Diff-only experiments retain their original capture bytes.

The pilot explicitly declares all 30 original source pairs. For cross-file
comparisons, include related paths such as `client.py`, `policy.py`, `schema.py`,
`formatting.py`, and `test_formatting.py`. Missing paths produce recorded omissions
rather than guessed content. These context mechanics are testable offline; their
accuracy effect still requires paired model experiments and adjudication.

## Procedure comparisons and promotion gate

Use `run --comparison-set procedures` to compare three panel arms, each repeated
three times: `current4` uses the original four baseline prompts, `revised4` uses
those names with inspection procedures v1, and `revised5` adds the v1
`testing-verification` reviewer. All arms retain the same model rail and total
review token ceiling. A ceiling of 40,000 allocates 10,000 per reviewer in each
four-person arm and 8,000 in the five-person arm. Without this flag the existing
senior/panel/debate comparison remains unchanged.

Before the first runtime await, the evaluator writes the complete schedule,
configs, effective prompt text and SHA256 hashes per variant, capture and label
hashes. Runtime inspection fills in nare capability fields before reviews begin.
A capability failure can therefore leave a frozen snapshot with null capability
fields and no completed review cells; it cannot be mistaken for a complete job.

The explicit gate accepts scorer metrics and verification declarations:

```sh
python -m scrutare.evaluation gate --input gate-input.json --output gate-result.json
```

The input has `baseline`, `candidate` (one variant's production scorer metrics,
including its latency list) and `evidence`. Evidence must declare
`evidence_kind: model`, `holdout_unseen`, `independent_human_verification`,
`uncertainty_sufficient`, `same_model_rail`, `same_case_repeat_schedule`,
`equal_total_token_ceiling`, `new_critical_security_misses`, and nonempty
`verification_reference` and `holdout_reference`. Boolean guards must be true;
references identify actual independently retained evidence. The gate records
these declarations; it cannot authenticate a human or establish statistical
adequacy merely from a boolean. The caller must independently verify those facts.

Unresolved findings, unknown accounting, incomplete runs, missing/invalid metrics,
offline jobs, mismatched run counts, or absent verification make the result
`inconclusive`. Adequate evidence passes only for recall gain of at least five
percentage points with precision loss at most two, or precision gain of at least
five with recall loss at most two. It also requires zero new critical security
misses and observed total tokens and nearest-rank p95 latency at most 20% above
baseline. A measured violation returns `fail`. The gate never changes defaults.
These thresholds are frozen product choices, not results established by a paper.

The original pilot holdout was included in the initial baseline. Any architecture
selected using those outcomes needs a newly frozen, unseen holdout and adequate
independent uncertainty evidence before promotion. The published baseline under
`evaluations/reports/2026-10-08-baseline` is provisional automated evidence, with
34 observations awaiting real human adjudication. No default promotion follows
from it.
