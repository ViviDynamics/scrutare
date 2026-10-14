# Publish architecture comparisons and promote only measured improvements

Issue #51 (third measurement increment; also measures #47, #48, #49, #50)

## Scope
In:
- An opt-in `context.line_counts` setting that shows reviewers the citation bound the
  validator already enforces. In the two `line_range_outside_capture` refusals of
  the routing job in `evaluations/reports/2026-10-13-qualification-rerun`, the
  retained sessions cited exactly one line past the end of a file that reviewers
  read as unnumbered text. Refusals in earlier packages were not re-examined. The
  validator is not changed.
- `evaluations/development-v3`: twelve original development cases with real Ruff
  diagnostics, wrong-allegation clean controls on specialist paths, specialist-area
  defects, and a pair whose deciding evidence is outside the capture.
- A report package under `evaluations/reports/` for: captured Ruff evidence versus
  assessment (#48), two further frozen qualification schedules and a completion
  estimate with intervals, discussion on the untried and the new cases (#49),
  routed versus fixed and assessment versus aggregation on the new cases (#47, #50),
  and the line-count setting on and off.

Out:
- Any default change or promotion. Every quality feature stays opt-in.
- `evaluations/holdout-v1`: not opened, sent or used.
- Human judgments: none exist and none are inferred. Automated matches are provisional.
- Any change to the citation validator, lint rules for product code, or CI.

## Assumptions
- Measured spend is actual tokens at the proxy-advertised rates (input $2 and output
  $10 per million, cached input charged as uncached), limited to US$60 for the
  increment. A job launches only if spend measured so far plus the worst case of
  every job in flight and of the next job (attempts x ceiling x output rate) stays
  within the limit.
- The reliability schedules reuse the exact source, wheel, corpus bytes and
  configuration of the earlier passing schedule. The rerun for cells stopped only by
  upstream HTTP 5xx is allowed within a frozen bound; a rerun cell counts as an
  accounting failure.
- New-case jobs run from a commit on `main`, so the corpus and the setting merge
  before they are measured, and the report follows in a second pull request.
- Four fixture files must contain a real Ruff diagnostic. The repository lint
  exempts exactly those files and codes; a test pins the list to the labels.
- A discussion round is never forced or seeded from labels. Zero rounds is a valid
  result and is reported as such.

## Tasks
- [x] 1. Opt-in line counts: failing tests for configuration, manifest, tamper
      refusal and prompts first; then the implementation; validator untouched.
- [x] 2. Development corpus v3 with executable gold controls, sealed inventory,
      pinned-Ruff expectation test and lint-exemption pin.
- [x] 3. Frozen jobs: reliability r2 and r3; discussion on `dev2-03`, `dev2-08`,
      `dev2-12`; tools, focused, routing/assessment and line-count jobs on v3.
- [x] 4. Report package with reproduction script, artifact hashes and a test.
- [ ] 5. Status blocks on #51, #47, #48, #49 and #50. Nothing is closed.
