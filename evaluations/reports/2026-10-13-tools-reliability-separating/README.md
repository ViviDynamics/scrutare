# Tool evidence, a reliability estimate, and cases meant to separate the arms

Thirteen frozen development jobs, 321 cells, on the original pilot cases,
`evaluations/development-v2` and the new `evaluations/development-v3`. Four further
jobs were frozen and withdrawn before launch. Every outcome is retained, including
the voided ones. Judgments are automated and provisional; there are zero human
judgments. Nothing here promotes a default, and `evaluations/holdout-v1` was not
opened or used.

Headline:

- The unchanged profile passed its qualification gate in 3 of the 4 schedules ever
  run (95% Wilson interval 30% to 95%). Over those 144 cells, 138 completed on the
  first attempt (95.8%, interval 91.2% to 98.1%). No upstream 5xx occurred in this
  increment, so the evaluator's rerun path still has never run live.
- Captured Ruff evidence changed nothing measurable. Both arms found every labeled
  defect and no finding cited the tool. The tools arm cost 4 to 6% more tokens in
  the revised-profile job (0.94 to 1.04 times in the silent-tool job).
- Assessment filtered nothing, because nothing reached it that it should have
  rejected. On `dev3-12`, the clean case whose caller is not captured, reviewers in
  every assessed cell reported two other behaviours of the changed function. Both are
  true from the captured file, though not reachable from that caller. The assessor
  supported them in all 9 cells. They are pending for a person.
- Discussion was attempted for the first time: three rounds started and none
  completed. Each failed because the reviewers' reused grants ran out, which voided
  the whole review ([#74](https://github.com/ViviDynamics/scrutare/issues/74)).
- On cases written to invite wrong allegations, no reviewer raised one: 0 of 120
  cells, in any arm. The only findings there were 19 reports of an unlabeled edge.
- At the default assessor reservation, 12 of 24 cells of the first tools job were
  voided because the assessor ran 131 to 3,650 tokens past its 20,000 tokens.
- With the opt-in `context.line_counts`, 0 of 135 sessions were refused for an
  out-of-range citation, against 2 of 135 in the paired job without it.

## Profiles and sources

The base profile is the one qualified earlier: proxy alias `gpt-6.1-sol` (backend
revision unverified), a 100,000-token review ceiling, the batching prompt, three
repetitions, six native turns, a 120-second session timeout, concurrency 3 and nare
2026.10.4. Its assessor reservation is one fifth of the ceiling, 20,000 tokens.

After the first tools job voided half its cells, a revised profile was frozen
(`profile-sol100-assessor30.json`, sha256
`790b58819cc790a44a87e6db161f43b0a89c3ab8b9527f0a7348f34b6e628ded`, at
2026-10-14T00:37:47Z). It is identical except that the assessor reserves 30,000
tokens and the four reviewers of an assessed arm share the remaining 70,000. **It was
chosen after seeing those overruns**: 30,000 is about 27% above the largest assessor
use observed in `tools-voided`, and 17,500 per reviewer about 22% above that job's
largest reviewer session (14,341). Across all jobs before the freeze the largest
reviewer session was 15,412 tokens (`routing`), 13.5% below 17,500, and a later
`focused-uncaptured` session reached 16,896.
No voided cell is reused under it. **The revised profile is not qualified.** The
qualification schedules ran with the 20,000 reservation, and a schedule on the
revised profile did not fit the remaining spend.

| Job | Profile | Source | Cases | Arms | Cells |
| --- | --- | --- | --- | --- | ---: |
| `reliability-r2`, `reliability-r3` | base | `2f1e95c` | three pilot cases | aggregate, assessed, focused1, focused3 | 36 each |
| `discussion-untried` | base | `2f1e95c` | `dev2-03`, `dev2-08`, `dev2-12` | same four | 36 |
| `tools-silent` | base | `2f1e95c` | `dev2-01`, `dev2-06`, `dev2-07` | assessment, tools | 18 |
| `tools-voided` | base | `a34c8b2` | `dev3-01` to `dev3-04` | assessment, tools | 24 |
| `focused-wrong-candidates` | base | `a34c8b2` | `dev3-05` to `dev3-07` | the four focused arms | 36 |
| `routing` | base (no assessor) | `a34c8b2` | `dev3-06` to `dev3-10` | evidence, routing | 30 |
| `line-counts` | base (no assessor), `context.line_counts: true` | `a34c8b2` | same | evidence, routing | 30 |
| `tools` | revised | `f52a4e1` | `dev3-01` to `dev3-04` | assessment, tools | 24 |
| `focused-uncaptured` | revised | `f52a4e1` | `dev3-11`, `dev3-12` | assessed, focused1, focused3 | 18 |
| `focused-uncaptured-aggregate` | revised (no assessor) | `f52a4e1` | same | aggregate | 6 |
| `focused-comment` | revised | `f52a4e1` | `dev3-08` | the four focused arms | 12 |
| `assessment-separating` | revised | `f52a4e1` | `dev3-06` to `dev3-10` | assessment | 15 |

`withdrawn/` holds the four jobs frozen but never launched, with their reasons:
three contained an assessed arm at the 20,000 reservation, and the first line-counts
freeze named two of those as its baseline. No provider call was made for them.
Sources `a34c8b2` and `f52a4e1` are the merges of #70 (`context.line_counts` and
development-v3) and #71 (the evaluator's `--assessment-tokens`); `2f1e95c` is the
source of the earlier qualified schedule, so its wheel and inputs are byte-identical.

Two comparisons pair arms from different jobs and sources, which is weaker than a
same-job pair and is flagged `same_job: false` in `summary.json`: aggregate versus
assessed on `dev3-11`/`dev3-12`, and the fixed-panel evidence arm of `routing`
against `assessment-separating`. The jobs were split only to fit the spend bound.

## Reliability of the unchanged profile

| Schedule | Cells | Complete on first attempt | Gate |
| --- | ---: | ---: | --- |
| Sol100k (earlier package `2026-10-13-protocol`) | 36 | 30 | failed (six upstream HTTP 500) |
| r1 (earlier package `2026-10-13-qualification-rerun`) | 36 | 36 | passed |
| r2 | 36 | 36 | passed |
| r3 | 36 | 36 | passed |

r2 and r3 were declared before launch as two further draws of the same configuration
with no conditional stopping, and both outcomes count whatever they were. Each
allowed six recorded reruns of cells stopped only by upstream 5xx; none was needed.
All six losses fall in one schedule and cells in one schedule share an upstream
window, so the cell-level interval understates the uncertainty. Mechanical
completion is not review quality.

## What was compared

Detection counts a defect cell only when the cell completed with accounting and a
finding matched the label under the frozen rules (below). Intervals are 95% Wilson:
9/9 is [70%, 100%], 6/6 is [61%, 100%], 0/3 is [0%, 56%].

| Comparison | Cases | Detection (baseline / candidate) | Tokens, candidate / baseline | Mean latency |
| --- | --- | --- | --- | --- |
| Tools vs assessment, revised (#48) | `dev3-01`, `-02`, `-04` defects; `-03` clean | 9/9 vs 9/9; clean 0/3 vs 0/3 with findings | 1.04 to 1.06 | 52.7 s vs 58.0 s |
| Tools vs assessment, silent tool (#48) | `dev2-01`, `-06`; `dev2-07` clean | 6/6 vs 5/6 | 0.94 to 1.04 | |
| Assessment vs aggregation, separating (#47) | `dev3-06` to `-10` | 9/9 vs 9/9; clean 1/6 vs 2/6 cells with findings | 1.00 to 1.40 | 2.7 to 3.0x on defects |
| Routed vs fixed (#50) | `dev3-06` to `-10` | 9/9 vs 9/9; clean 1/6 vs 2/6 with findings | 1.38 to 1.42 | |
| Assessment vs aggregation, reassuring comment (#47) | `dev3-08` | 3/3 vs 3/3 | 1.39 | 28.0 s vs 71.8 s |
| Assessment vs aggregation, uncaptured caller (#47) | `dev3-11`; `dev3-12` | 3/3 vs 3/3; clean twin 2/3 vs 3/3 cells with findings | 1.20 to 1.25 | |
| One round vs assessment, three vs one (#49) | `dev3-05` to `-08`, `-11`, `-12`; `dev2-03`, `-08`, `-12` | no difference except cells failed by attempted rounds | about 1.00 | |

**Tools (#48).** Pinned Ruff 0.16.10 reported what the labels predicted: `F811` on
`dev3-01`, `F821` on `dev3-02`, two `F401` on the clean facade `dev3-03`, nothing on
`dev3-04` and on the development-v2 cases. Every reviewer found the two defects Ruff
names without help, nobody reported the unused imports in either arm, and the defect
Ruff cannot see was found as often with the tool silent. None of the 56 tools-arm
findings mentions Ruff, a rule code or lint. These cases do not separate the arms; a
defect that reviewers miss and the tool names is still needed. The one silent-tool
loss (`dev2-06/tools/3`) was an out-of-range citation, not a detection difference.

**Assessment (#47).** On every defect case the assessor supported every candidate.
On the clean cases it saw two kinds of candidate. One was the unlabeled zero-batch-size
edge on `dev3-07` (below), which it supported. The other was on `dev3-12`, where the
change makes `render_subject` render a missing value as empty text. The labeled
tempting allegation (callers that relied on `KeyError` now send blank subjects) was
never raised. The frozen allegation filter flagged all 11 `dev3-12` findings, but on
reading, none states that allegation; the filter's patterns (`blank`, `missing
value`) are too broad. Instead, 8 findings say that a missing value with a numeric
format specification such as `{count:d}` still raises, contradicting the new
docstring. The other 3 say that copying the mapping into the helper dict discards a
caller's own default mapping. Both are true from the captured `subject.py`, and the
assessor supported them in all 9 assessed cells. The only caller, deliberately not
captured, resolves every placeholder first, so neither is reachable there. Whether
they are defects is a person's decision; they stay pending. Assessment therefore had
no wrong candidate to reject in this increment. It cost up to 40% more tokens and
2.7 to 3.0 times the latency on defect cases. `dev3-12` is not used for any precision
figure, and none is published. Its label says `clean_label_requires_context: false`,
although its clean label also depends on code outside the capture; the
`evidence_outside_capture` field records that instead.

The assessor's fixed reservation is a second #47 finding. When it ends past its
reservation, even by 131 tokens, the engine yields no verdict for the whole review
and discards findings that discovery and assessment had already supported. In
`tools-voided` that cost 12 of 24 cells (assessor use 19,478 to 23,650 tokens with
candidates present, against 20,000). The product behaviour was not changed; the
evaluator gained an explicit reservation so the comparison could be rerun.

**Discussion (#49).** Across 90 focused cells, 3 rounds were attempted and 0
completed. The `dev2` and `dev3` cases written to invite a wrong allegation drew
none in 120 cells across all arms (the only findings there were 19 reports of the
zero-batch-size edge on `dev3-07`). Rounds started only where the assessor supported
a candidate but also gave a counter-citation, which the protocol treats as a
dispute: `dev3-11/focused1/3`, `dev3-11/focused3/3` and `dev3-08/focused3/2`. Each
reviewer's position reused what was left of its discovery grant, about 5,000 to
6,400 tokens; all twelve position sessions ended 393 to 1,366 tokens over, the round
was recorded `perspectives_incomplete` and the cell produced no verdict. Those are
the only detection losses of the focused arms. Filed as
[#74](https://github.com/ViviDynamics/scrutare/issues/74). One round versus several
remains unmeasured, and so does whether discussion changes an outcome.

**Routing (#50).** Specialists activated from paths alone in all 15 routed cells and
added no detection; every arm found all three routed defects, including the two
behind a reassuring comment that an unchanged file contradicts. On the two routed
clean controls neither arm raised the labeled allegation; the zero-batch-size edge
was reported in 2 of 6 routed and 1 of 6 fixed-panel cells. Routing cost
about 40% more tokens.

**Citations.** Across every job without `context.line_counts`, 7 of 1,179 reviewer
discovery sessions (0.6%, interval 0.3% to 1.2%; 7 of all 1,316 sessions including
assessor and position sessions, 0.5%, 0.3% to 1.1%, none of which was refused) and 7 of 291 cells (2.4%, 1.2% to 4.9%) were refused for
`line_range_outside_capture`. In every refusal, each of the 8 out-of-range citations
ended exactly one line past the end of the file. In the paired `routing` / `line-counts`
jobs (same cases, arms and source; only the setting differs) the counts were 2 of
135 sessions and 2 of 30 cells without the setting, 0 and 0 with it (intervals
[0%, 2.8%] and [0%, 11.4%]). The setting costs about 3% more tokens. Two events
against zero is suggestive, not established.

## Judgments, and when the rules were frozen

`automated-decisions.json` holds 578 matches made by the rules in `reproduce.py`; 32
findings match no rule and are pending for a person, never scored false. The
development-v3 rules (`FROZEN_RULES` and `ALLEGATIONS`) were written from the labels
and fixture sources alone and committed as `c4e092a` at 2026-10-14T00:22:09Z (pushed
at 00:22:11Z). At that moment `tools-voided` was the only development-v3 job
launched (00:20:39Z) and no development-v3 job had finished. It already had 3
completed cells on disk, the first written 22 seconds before the commit. That no
finding, candidate or session text of a development-v3 job had been read is the
operator's assertion; the artifacts cannot demonstrate it.
`frozen-rules.json` records their hash
(`f722d27708cb20461ad6074f2c1ae4a267321c28dce3c69c35ac44d382b4e683`), and
`reproduce.py` refuses to run if the rules differ. They were not amended: they matched
every development-v3 defect finding and assessed candidate. Two corrections to
`frozen-rules.json`, which is hashed and therefore left as written. Its
`state_of_jobs_when_frozen` says six development-v3 jobs "had been launched and some
had finished"; in fact six were frozen, one (`tools-voided`) had launched, and none
had finished. Its allegation filter for `dev3-12` turned out to be over-broad (above).

The rules for the pilot and development-v2 cases are copied unchanged from the
previous package, where they were written after that package's outputs existed.
**Outputs of `reliability-r2`, `reliability-r3`, `discussion-untried` and
`tools-silent` were read before the freeze, so every judgment on those four jobs is
post hoc.**

The 32 pending findings are: 19 reports on `dev3-07` that a zero batch size silently
skips the backfill, which is a real edge of that change, neither the labeled
allegation nor scored; 11 findings on `dev3-12` that the over-broad filter flags but
that state the two other claims described above; and 2
pilot findings worded outside the old filter. These, and every automated match, need
human adjudication. All matches are keyword filters, not a semantic judge.

## Spend

The limit for this increment was US$60 of measured spend. A job launched only if
measured spend so far, plus the worst case of every job in flight and of the next
job (attempts times ceiling times the advertised output rate), stayed within it.

The ledger has no launch event for `reliability-r2`, the first job; the bound
trivially held there (nothing spent, worst case 42).

Measured spend was **US$43.34** for 17,312,889 tokens, at the proxy-advertised rates
($2 per million input tokens, cached input charged as uncached, $10 per million
output). Counted the earlier nominal way (attempts times ceiling times output rate)
it is $321. `summary.json` lists both per job. No invoice or billed amount is
asserted.

Not run for lack of allowance under the worst-case rule: a 36-cell qualification
schedule on the revised profile, and reruns of the three cells failed by discussion
rounds (rerunning them at the same profile would fail the same way).

## Reproduce

```sh
python reproduce.py --package . --output ../recomputed-summary.json
```

Python 3.10 or newer, standard library only, no network. It checks every published
hash and the frozen-rule hash, the exact cell matrices, agreement between the public
experiment rows and the normalized cells, recomputes each gate, the automated
decisions and `summary.json`, and fails on any difference. Write the output outside the package: the script does
not itself reject unlisted extra files there (the repository test does). It does not rerun a model
or authenticate normalized evidence against native sessions; that needs the retained
private captures. Whether an assessor ran, its token use, citation refusals and the
evidence validation are read from `mechanical-qualification.json`, produced from the
private captures with the measured wheel.

Each job directory holds `preparation.json` (source, inputs and hashes, frozen before
the first call), `gate.json`, `evaluation.json`, `snapshot.json`, `experiment.json`,
`mechanical-qualification.json`, `corpus-manifest.json` and
`original-artifact-sha256.json`; tool jobs also hold `tool-evidence.json`, the
captured Ruff diagnostics with temporary paths removed. Raw native transcripts,
credentials and provider headers are not published.
