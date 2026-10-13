# Structured-output protocol qualification

These are bounded development qualifications, separate from the earlier architecture
comparisons and from semantic quality or promotion. The first two schedules used
the unchanged production source at `579fb89d5bb2df57796b42e5b15533424301c0a5`.
That commit is publicly available; no unpublished source patch is required.

Each frozen schedule contains the same three original development cases:
`security-auth-order`, `tests-consumer-fixture`, and
`contract-explicit-default-clean`. Aggregate, assessed, focused1 and focused3 each
run three times: 36 cells per model, 72 retained cells in total. Context and v2
evidence stay enabled. The configured total ceiling is 40,000 tokens, native max
turns 6, session timeout 120 seconds and concurrency 3. Assessed/focused arms
reserve 8,000 for the assessor and grant each original perspective 8,000; aggregate
grants each perspective 10,000. Correction, discussion and reassessment reuse grants.
Focused arms reserve no chair. Native read-only tools remained available. Captured
static-analysis evidence and specialist routing were disabled.

| Model alias/configuration | Complete | Failed | Invalid | Partial | Measured total tokens | Runs over configured ceiling | Qualification |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| gpt-6-luna | 0 | 25 | 9 | 2 | 1,487,639 | 22 | Not qualified |
| gpt-6.1-sol | 3 | 18 | 9 | 6 | 1,389,352 | 13 | Not qualified |
| gpt-6-luna, batching candidate at 40,000 | 6 | 18 | 9 | 3 | 1,383,635 | 12 | Not qualified |
| gpt-6-luna, batching candidate at 80,000 | 29 | 0 | 7 | 0 | 1,643,473 | 0 | Not qualified |
| gpt-6.1-sol, batching candidate at 100,000 | 30 | 1 | 5 | 0 | 1,556,686 | 0 | Not qualified |

Luna retained zero surviving findings; Sol retained 24 mechanically valid source
observations. Both schedules retained zero actual assessor calls and completed
discussion rounds. Nine of 144 Luna discovery outcomes completed; 129 stopped
partially for budget and six failed with native max-turn stops. Sol completed 26
of 144 discovery outcomes; 117 stopped partially for budget and one failed.
Native done/end_turn does not itself establish production completion or a valid
verdict. Empty successful cells cannot qualify nonempty evidence or assessor coverage.
Every failure and partial outcome remains in the matrix.

The model names are proxy aliases, with dated backend identities unverified.
Advertised proxy metadata implied nominal schedule estimates of $0.72 for Luna and
$14.40 for Sol. These are estimates, not hard billing caps: native enforcement can
occur after a provider turn, and the retained measured usage exceeded grants.
No invoice or actual monetary spend is asserted here.

The frozen gate requires all 36 runs to complete with complete accounting, all six
aggregate defect-case repeats to contain mechanically valid nonempty v2 evidence,
and all six assessed defect-case repeats to invoke a complete native assessor.
Discussion coverage is reported separately and is not required for this first
protocol qualification; supported, undisputed findings may legitimately bypass it.
Neither model passed. No defaults were promoted. Actual human adjudication remains
the next ordered step; no later specialist fixtures or unseen validation are claimed.
The new 40-case holdout was not exposed or used.

The batching candidate at `0298f218374b2723cf264e3a561dbe256c6367b1` adds explicit
relative artifact paths and guidance to batch initial reads. It preserves validators,
read boundaries, source/context eligibility and default settings. Its separate
36-cell 40,000-token schedule also failed: 24 mechanically valid source observations,
zero assessor calls and zero completed discussion rounds. All six aggregate
defect-case repeats in both Sol and batch40 retain nonempty valid evidence; their
overall completeness and actual-assessor requirements still failed.
Of 144 native discovery outcomes, 24 completed and
120 stopped partially for budget. Its maximum measured run used 43,331 tokens.
The original two schedules and this candidate's failed 40,000-token schedule retain
108 cells permanently. More complete cells do not establish qualification or a
semantic improvement. The source patch and temporary-index reconstruction proof
reproduce the candidate's exact tracked tree from the remote-known original base.

A separate 80,000-token configuration for the same batching source and model was
prepared before the first batching-candidate call and run only after the 40,000
trial failed. It changes the review/per-persona configuration ceilings, while keeping
the same cases, three repetitions, four arms, max turns, timeout, concurrency and
requirements for completeness, nonempty discovery and actual assessor invocation.
The aggregate arm actually grants four 20,000-token quotas; assessed/focused arms
reserve 16,000 for the assessor and grant four 16,000-token quotas. These grants
remain shared across phases. This is a higher-spend trial, with a nominal proxy
estimate of $1.44, not an equal-cost architecture gain or a raised product default.

Its 36 retained cells bring the publication to 144 cells. All six aggregate
defect-case repeats produced nonempty mechanically valid v2 evidence. Four of six
assessed defect-case repeats executed complete assessors; the authorization case's
first and third assessed repeats did not. Across all arms, 16 actual assessor calls
occurred, 11 complete and five partial; zero discussion rounds occurred. Twenty-nine
runs completed, and seven were invalid because no valid final verdict was available.
Maximum measured run usage was 58,981, below this trial's 80,000 total ceiling, but
individual participant grants still bound phase admission. The frozen all-36-complete
and all-six-assessors criteria failed. The higher-budget trial is also unqualified;
the failed 40,000-token trial has not been overwritten or retroactively admitted.

The higher-budget trial retains 68 source observations, all mechanically validated:
24 on the authorization case and 44 on the stale-test case. These are unadjudicated
observations, not 68 unique confirmed defects or human judgments. The clean-control
case produced no surviving observations; that alone is not a semantic quality claim.
Actual human review and later discussion/specialist measurements remain outstanding.
Across Sol, batch40, batch80 and Sol100, `unadjudicated-findings.json` retains all 196
source observations without semantic dispositions. They have not been classified
as genuinely ambiguous or designated additional mandatory human cases. The next
ordered semantic triage must distinguish explicit automated decisions from actual
human judgments and add genuine ambiguities to the separately prepared human packet.

The final bounded schedule used the same batching source with `gpt-6.1-sol` and a
prospectively frozen 100,000-token ceiling. Aggregate grants are four 25,000-token
quotas; assessed/focused grants are four 20,000-token quotas plus a 20,000 assessor
reservation. Cases, repetitions, timeout, max turns, concurrency and qualification
criteria remain unchanged. This changes both model and budget relative to batch80;
it cannot isolate a batching effect or show an equal-spend gain. At the initial
Sol proxy's advertised rates, its nominal ceiling estimate is $36, with the same
after-turn enforcement and billing uncertainty described above.

The final 36-cell schedule retained 30 complete, five invalid and one failed run,
with 30 runs fully accounted. All six failed native discovery sessions were
independently classified from retained errors as HTTP 500 upstream overload. These
transport failures are distinguished from schema, citation and budget refusals;
raw errors and private references are not published. No retry or further model
schedule was run. Its 15 actual assessor calls all completed; however, only three
of six assessed defect-case repeats invoked an assessor, because the authorization
case's three assessed repeats were interrupted upstream. Five of six aggregate
defect-case repeats retained nonempty valid evidence; one stale-test repeat failed
upstream. Thus the original frozen gate still failed. Zero discussion rounds ran.
The 80 source observations from this trial remain mechanically accepted and
semantically unadjudicated. Maximum measured run usage was 57,138, below its total
ceiling. The full publication retains 180 cells and 196 unscored source observations.
No candidate passed every frozen criterion, and no default model or budget changed.

These counts retain every source assertion in the evaluator's flattened
`findings` records. They differ from consolidated run-level findings: Luna has
0 consolidated findings/0 sources, Sol 23/24, batch40 22/24, batch80 65/68, and
Sol100 72/80. The total is 182 consolidated run-level findings and 196 source
observations, with no claim that these are distinct global defects. Each schedule's
`consolidated-findings.json` preserves the per-run group/source relation and original
artifact hashes. Missing artifacts remain unavailable, rather than successful empty
reviews. Public reproduction verifies exact raw-group flattening against every
evaluator record.

## Retained boundary diagnostics

`boundary/` contains normalized evidence from the earlier focused nano job and the
Luna budget replay. The native validator exactly reproduced 378/378 retained schema
complaints; unchanged Scrutare parsing reproduced 221/221 protocol refusals and
35/35 immutable-citation refusals. This investigation did not demonstrate a native
integration mismatch. The retained identifiers were never repaired into acceptance.
Shape acceptance does not establish revision/hash identity or semantic correctness.

The boundary README describes private diagnostic replay procedures used by the
investigator. Those scripts require private native captures and are not part of
this public reproduction package. Normalized diagnostics can be inspected here;
their private-native reconstruction and authentication cannot be reproduced from
the public data alone. Raw model/native transcripts, credentials and provider
headers are not published.

The final Luna read diagnostic distinguishes failed path guesses from successful
rereads: 55 absolute-path guesses and eight directory reads were refused, while
known artifacts were successfully read once. Twenty-seven of 144 sessions batched
all four known artifacts on the first turn; 83 started with only the diff. Of 103
final answers accepted by the unchanged shape and immutable-citation checks, 94
were withheld for budget and nine completed within quota. This does not establish
semantic correctness. Explicit relative artifact paths and batching are a bounded
prospective prompt hypothesis, with no measured improvement asserted by the old jobs.

## Public reproduction and provenance

Run from any isolated copy with Python 3.10 or newer, without network or providers:

```sh
python reproduce.py --package . --output /tmp/protocol-summary.json
```

This checks every public artifact hash, exact 36-cell identities, alignment between
public experiment rows and normalized cells, the four-person initial native matrix,
and recomputes the frozen gates and counts. It does not rerun a model, parse private
answers, or authenticate normalized diagnostics as raw native evidence. Actual
session parsing and citation revalidation used the frozen installed production
wheel against retained private captures before the normalized report was exported.
There are zero actual human judgments; mechanical admission is separate from truth.

Public experiment run directories are replaced by relative run identities; snapshot
runtime paths use `nare@2026.10.4`. Original artifact hashes are retained separately
inside each schedule. `preparation.json` pins original source and input hashes,
`snapshot.json` retains actual participant/grant/prompt identities, and
`qualification-gate.json` retains the criteria frozen before calls. Published hashes
refer to sanitized public bytes. Large JSON is compact to keep review manageable.

The published corpus manifests identify only these three development cases. Their
owned source and labels are in the repository's original `evaluations/pilot` corpus.
No clean-control or semantic-accuracy assertion follows merely from protocol status.
