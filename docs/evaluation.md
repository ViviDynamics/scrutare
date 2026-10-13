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

## Recorded reruns after upstream HTTP 5xx

nare 2026.10.4 does not retry on its OpenAI-compatible rail, and the review engine
deliberately seals a review after any provider failure because that call's billing
is unknown. One upstream overload therefore ends a whole cell. `run
--transient-retries N` (0 to 5, default 0) lets the evaluator rerun such a cell
from the start, in a fresh directory prepared from the same frozen bytes, after
`--transient-retry-delay-seconds`. The product itself never retries.

A cell is eligible only when the attempt did not complete, the evaluator raised no
error, and every native session in it either completed, never started, or failed
with reason `provider` and a retained `chat completion failed with HTTP 5xx`
result. Budget, protocol, citation, timeout, 4xx and unreadable outcomes are never
rerun. A rerun whose prepared input hashes differ from the frozen snapshot is
refused and recorded as `RetryInputsDiffer`.

Nothing is hidden. The first attempt stays at `<case>/<arm>/<repeat>`; reruns go to
`_retries/<case>/<arm>/<repeat>/attempt-NNNN`. Each attempt keeps its own
`evaluation.json`, sessions and artifact manifest. The cell record lists every
attempt with status, usage, latency and the classified HTTP status per failed
session. `usage` is the final attempt's ledger, `retry_usage` sums the earlier
attempts as a lower bound, and `latency_seconds` covers all attempts and waits.
The policy is written to `snapshot.json` before the first call. Report how many
cells needed a rerun beside any completion rate; a cell that completed on its
third attempt is not the same evidence as one that completed on its first.

`run --variants a,b` schedules only the named arms of the comparison set, in the
set's own order, and records the selection in the snapshot. Use it to spend on
one paired comparison instead of the whole set; unknown or repeated names are
refused.

## Development corpus v2

[`evaluations/development-v2`](../evaluations/development-v2/README.md) adds twelve
original development cases. Eight changed-path sets activate the built-in
`performance-concurrency` or `data-integrity-migrations` specialists, three place
a specialist-area defect on a path no rule matches, and four clean changes invite
a specific wrong allegation that public code refutes. Activation comes from
`files.json` and built-in rules only; expectations live in evaluator-only labels
for checking. Pass `related-paths.json` as `context.related_paths`. No dispute is
forced, and these cases can never serve as unseen validation.

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

## Remaining architecture comparisons

`run --comparison-set architectures` schedules seven panel arms, each with the
same model, total review ceiling and three repetitions per case. These components
stay opt-in in ordinary reviews. Inherited enabled components are cleared before
the comparison sets its explicit flags; trusted context selectors and routing
rules remain fixed across the relevant arms.

| Arm | Context | Procedures | Evidence | Assessment | Captured tools | Routing |
| --- | --- | --- | --- | --- | --- | --- |
| `baseline` | off | baseline | legacy | off | off | off |
| `context` | on | baseline | legacy | off | off | off |
| `procedures` | on | v1 | legacy | off | off | off |
| `evidence` | on | v1 | v2 | off | off | off |
| `assessment` | on | v1 | v2 | on | off | off |
| `tools` | on | v1 | v2 | on | on | off |
| `routing` | on | v1 | v2 | off | off | on |

The four fixed reviewers are senior-dev, junior-dev, security and devops. At a
40,000 ceiling, assessment reserves 8,000 for the independent executor and the
four discovery reviewers receive 8,000 each. Unassessed fixed panels receive
10,000 each. Routing uses the production planner and shared ledger: five effective
reviewers receive 8,000 each; six share the same 40,000 rather than increase it.
Case-level snapshots record ordered effective reviewers, exact system prompts and
hashes, executor prompt identity, actual quota allocations, routing activation and
omission reasons, and prepared input hashes **before runtime inspection**. Capture
and source bytes also freeze before that first await. Preparations happen before
the measured model-review latency interval; separately captured tool runtime is
recorded in the tool manifest and must be reported as an additional collection cost.

Compare consecutive component arms for context, procedures, evidence and assessment;
compare tools with assessment, and routing with evidence. The development pilot has
20 cases, yielding 420 review cells. Its generic changed-file paths may leave the
automatic specialists inactive; actual activation counts determine what that arm
measures. Do not force specialists using known defect labels. Development runs and
previously seen original holdout results cannot authorize promotion.

Tool evidence can be explicitly collected from original Elastic-2.0 development
fixtures before the model job:

```python
from pathlib import Path
from scrutare.evaluation.tool_capture import capture_development_ruff

index = capture_development_ruff(
    Path("evaluations/pilot"), Path("private-tool-captures"),
    executable=Path("/absolute/path/to/ruff-0.16.10"),
)
```

This producer copies bounded unlinked head source bytes, uses Ruff's built-in parser
with `--isolated --no-cache --select E4,E7,E9,F`, and never executes fixture code,
repository commands or project configuration. It refuses external provenance and
symlinked inputs. Its child environment contains only a fixed PATH and LANG, with
no provider credentials. Host network isolation is not enforced and is recorded
as such; this trusted fixture collector is not an OS sandbox. The production review
adapter consumes captures only and never invokes Ruff.

The output directory contains one `<case-id>.json` envelope and `index.json`, with
source, capture and raw stdout/stderr hashes, inspected paths, exit status and
complete/failed/unavailable status. Copy these captures inside the private corpus
copy and add the relative `analysis_capture` path to each development manifest
entry before loading it. Captures bind declared head revisions, and the model root
contains only diagnostics whose paths/ranges bind captured context bytes. Diagnostics
are evidence rather than automatic findings; absent captures become unavailable,
and a silent tool is never proof of correctness. Preserve original index provenance
alongside published normalized results. Paid collection/evaluation remains explicit.

## Focused discussion comparisons

`run --comparison-set focused` schedules `aggregate`, `assessed`, `focused1`, and
`focused3`, each repeated three times with the same context, v1 procedures, v2
claim evidence, original four reviewer names, model rail and total ceiling.
`aggregate` is an unassessed panel; `assessed` adds independent assessment;
`focused1` and `focused3` add evidence discussion capped at one or three rounds.
Tool evidence and routing are disabled in all four to isolate the protocol.
At 40,000 tokens, the aggregate reviewers receive 10,000 each; the other arms
reserve 8,000 for assessment and 8,000 per reviewer. Focused discovery,
correction and discussion reuse each reviewer's original grant, and all assessor
invocations share the one assessor reservation. No debate chair or new round quota
is introduced. Supported undisputed claims may bypass discussion entirely.

Before the first runtime await, focused case snapshots record these actual
participants/allocations, exact reviewer and assessor system prompts, input hashes,
and SHA256 hashes of the production assessment/discussion descriptor source.
The latter identifies the fixed protocol code; its quoted candidate-dependent text
is retained in native session evidence after candidates exist.

Focused evaluation records additionally retain a compact `discussion_trace` with
the producing `debate.json` SHA256, outcome reason, bypassed IDs, final independent
assessment dispositions, and per-round measured token deltas, latency, proposals,
new citation identities and disposition changes. Native session directories and
runtime metadata stay private. Citation identity checks and model dispositions
are not semantic truth or human adjudication. Missing/invalid traces remain
explicitly unavailable with null rounds; they invalidate otherwise successful
focused cells rather than imply successful zero-round execution. Failed cells keep
their failure status and observed usage in the full scheduled denominator.

The 20 development cases yield 240 cells. Compare assessed with aggregate, one
focused round with assessed, and three with one; do not attribute differences to a
larger token ceiling. Preserve actual attempted/completed rounds and extra cost,
including bypassed discussions and exhausted disputes. These development results
cannot promote a default or substitute for independently adjudicated, unseen
holdout evidence. Ordinary baseline/procedures/architecture modes remain available.

The [published architecture development experiment](../evaluations/reports/2026-10-12-architectures/README.md)
retains 420 scheduled cells, including 185 complete, 116 failed and 119 invalid
outcomes. Its 652 automated observation judgments leave 54 pending actual humans.
The [focused protocol experiment](../evaluations/reports/2026-10-12-focused/README.md)
retains 240 cells: three complete empty results, 178 failed and 59 invalid. Discovery
failures prevented every native assessor invocation and all actual discussion
rounds; the one-versus-three bounds were not exercised. No specialists activated
on the original pilot paths. These are protocol reliability limits on the current
aliased model rail, with no demonstrated architecture winner or default promotion.
Both packages include portable scoring/table reproduction, source reconstruction
patches, public artifact hashes and explicit provenance/privacy limitations.
