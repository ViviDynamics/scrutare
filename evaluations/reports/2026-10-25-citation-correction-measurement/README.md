# Citation correction and line counts on `spark/glm-5.3-flash`: a development measurement (#91)

**Result: no effect could be measured, because nothing went wrong.** In 144 discovery sessions across the three jobs, no answer was refused for its citations. So the correction turn (`findings.citation_correction`, PR #93, release 2026.10.34) was never offered, and line counts had no refusal to prevent. The correction's live path was exercised separately, by one transport smoke against this model.

This is a measurement on development cases, required by #91 before any new qualification. It is not a qualification and not a gate:
- nothing was frozen or posted on #51;
- the idle-window rule did not apply, though concurrent load was recorded;
- no default changed and nothing was promoted;
- judgments are automated (validation only), with no human judgment.

`evaluations/holdout-v1` was not opened. Only `spark/glm-5.3-flash` was called, with US$0 external spend.

## Design

`plan.json` was written before any call. The three jobs ran the same released source (`ff63a52`, Scrutare 2026.10.34), the same corpus bytes and the same rail. They differ only in the settings under test; `reproduce.py` checks that the three `evaluation.json` files differ in nothing else.

| Job | `context.line_counts` | `findings.citation_correction` |
| --- | :---: | :---: |
| `base` | off | off |
| `lines` | on | off |
| `lines-correct` | on | on |

Everything else is `c1`'s profile (PR #85):
- `structured_output: json_schema` and `max_output_tokens: 20000`;
- nare 2026.10.12, a 200,000 ceiling, an 1800 s timeout and 6 turns;
- the LAN LiteLLM path.

Each job ran the focused set's `aggregate` arm only (four evidence-v2 reviewers, no assessor). It covered four pilot development defect cases, three repeats each, so 12 cells, 48 discovery sessions per job and 144 in total. The cases were `security-auth-order`, `tests-consumer-fixture`, `contract-null-zero` and `operations-flush-loss`.

**Sizing.** `g2`, `s1`, `s2` and `c1` each lost 1 to 3 of roughly 144 discovery sessions to citation defects, so the rate is about 1 to 2%. Measuring a change in a rate that low takes thousands of sessions, which would mean days of a shared engine. This measurement does not attempt that; it is sized to see the mechanism fire and to price it:
- The aggregate arm gives the most citations per token: four discovery sessions and no assessor per cell.
- The four cases include the two (`security-auth-order`, `tests-consumer-fixture`) behind every recent past-end and placeholder-hash refusal.
- The three jobs ran at once, one cell each, so all three saw the same engine load. That put 12 of this evaluator's streams in flight, `c1`'s peak.
- Planned: about 2 to 2.5 h and 2 million tokens, comparable to one `c1`. Actual: 1 h 56 min (04:17:49Z to 06:14:00Z) and 2,145,458 tokens.

## Results

From `reproduce.py`; see `summary.json`.

| | `base` | `lines` | `lines-correct` |
| --- | ---: | ---: | ---: |
| Cells complete and accounted | 10 of 12 | 12 of 12 | 12 of 12 |
| Discovery sessions complete | 46 of 48 | 48 of 48 | 48 of 48 |
| First answers refused for citations | **0** (95% CI 0 to 7.4%) | **0** (0 to 7.4%) | **0** (0 to 7.4%) |
| Corrections offered / corrected | (off) | (off) | 0 / 0 |
| Correction tokens | (off) | (off) | 0 |
| Sessions lost, by cause | 2 `output_cap` | none | none |
| Valid citations in cell findings | 113 | 131 | 129 |
| Findings in cells | 41 | 48 | 49 |
| Tokens (all cells) | 749,613 | 714,798 | 681,047 |
| Output tokens | 205,802 | 167,582 | 153,880 |
| Mean cell latency | 580 s | 471 s | 452 s |

- **Pooled across the three jobs:** 0 of 144 discovery sessions had a citation refusal (95% Wilson interval 0 to 2.6%). That is consistent with the earlier schedules' 1 to 2%, and does not show any setting lowering it: at 1.5%, a run of 144 sessions with no refusal happens about 1 time in 9.
- **`base` lost two cells,** both on `operations-flush-loss` (repeats 1 and 2). In each, one reviewer's request ran to the 20,000-token cap, and the session failed as `output_cap` (complete accounting, no answer used). This has nothing to do with citations or either setting, and one job is too few to tell whether line counts affect it.
- **The token and latency differences between jobs are within run-to-run noise** for 12 cells. The `base` total includes the two capped requests (about 40,000 output tokens). These figures are not evidence that either setting is cheaper.
- **Each corrected session's extra cost** would have been one tool-free turn over the session's context. In the smoke below that was 5,453 input and 455 output tokens (5,908 in all), against a median discovery session of 13,105 tokens here.

## The correction's live path

No session needed a correction, so the turn's live path is shown by one transport smoke on this model (`provider-evidence.json`, `live_resume_smoke`):
- It took a retained `lines-correct` discovery session (`security-auth-order/aggregate/1`, `devops`) and resumed it with Scrutare's correction prompt and schema. The run used `--tools none`, `--max-turns 1`, `--structured-output json_schema` and `--max-tokens 20000` on the LAN path.
- **The defect it named is synthetic.** The retained answer was valid, and the prompt claimed `end_line` 8 was past the file's last line, 7. So the smoke tests the transport, not the model's correction behaviour.
- **Outcome:**
  - nare finished `done` in one more turn (3 to 4), on the same session id, with tools `[]`;
  - it emitted one schema-constrained turn record (`tools_withheld: false`, the tool-free form);
  - the answer had exactly the correction schema's shape;
  - the model kept `end_line` 7 (now within the stated bound) and copied the revision and content hash exactly as it had read them.

The installed proofs in PR #93 cover the rest with native nare 2026.10.12: a past-end citation corrected, a placeholder hash corrected, a correction refused again, one with no answer, not-offered cases, and replay.

## Load, spend and fallbacks

**Router.** Read before launch and again after: no `fallbacks`, `context_window_fallbacks` or `content_policy_fallbacks`, no wildcard groups, and one deployment for `spark/glm-5.3-flash` (`openai/glm-5.3-flash` on the Spark host). `num_retries` is 2, but no row needed a retry.

**Run window** (04:17 to 06:20Z, every key): all 677 spend-log rows were served by that deployment, with status `success`, 0 attempted fallbacks, 0 retries and 0 paid-provider rows.
- This evaluator (nare, `python-httpx2`): 480 requests, 1,623,647 prompt and 527,719 completion tokens. That includes the one smoke request.
- opencode, on the proxy master key, ran alongside: 197 requests, 17.6 million prompt tokens.
- The hour before launch had 121 opencode requests.
- Every 15-minute sample during the run saw opencode.
- So the jobs did not run on an idle engine. All three jobs shared the same load.

LiteLLM's nominal attribution for the self-hosted group is not billed by any provider.

## Reproduce

```sh
python reproduce.py --output citation-correction-summary.json
```

It needs Python 3.10 or newer and uses only the standard library, with no network. It:
- checks every published hash and refuses files outside the manifest;
- requires the three preparations to name the same source, plan, cases and arm;
- requires the evaluations to differ only in the two settings;
- checks each session diagnosis against its cells and the frozen rail and cap;
- requires a correction record exactly where the setting is on;
- recomputes the table above.

Some figures come from private captures and are not recomputed:
- each session's nare error text and first-stream token split (`session-diagnosis.json`);
- the first-answer classification (`first-answers.json`), made with the released `citation_defects` over the private streams;
- the proxy rows, load samples and smoke.

The retained captures' hashes are in each job's `original-artifact-sha256.json`, and the raw spend rows' hash is in `provider-evidence.json`. Each `preparation.json` is published byte-identical to the one the job ran, so it keeps one local path, the operator's nare console.

## What this does not establish

- **Whether either setting reduces citation refusals.** None occurred in any job, with or without them.
- **How often a live correction succeeds.** None was offered. The smoke used a synthetic defect.
- **Behaviour on an idle engine,** or on cases other than these four.
- **Review quality.** Nothing here judges findings beyond mechanical validation.

## Next

#91's acceptance asks for this comparison before any new qualification, and it is now recorded. Running a new frozen qualification of `spark/glm-5.3-flash` with these settings is a maintainer decision. Any new gate must be frozen and posted on #51 before launch. Nothing here changes a default.
