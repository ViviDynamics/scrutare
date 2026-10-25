# Measure line counts and the citation correction on the development corpus

Issue #91

## Scope
In: the comparison #91 requires before any new qualification. Three jobs on the released 2026.10.34, run at the same time on `spark/glm-5.3-flash` only: `c1`'s profile, then plus `context.line_counts`, then plus `findings.citation_correction`. A live transport smoke of the correction turn, and publication with public reproduction.
Out: a frozen qualification, which is a maintainer decision; any default change or promotion; the holdout; any other model.

## Assumptions
- It is a measurement, so the idle-window rule does not apply. Concurrent load is still recorded.
- Sizing, written in `plan.json` before any call:
  - 4 pilot development defect cases, the aggregate arm and 3 repeats, so 48 discovery sessions per job;
  - about one `c1` schedule of traffic.
  - It does not estimate an effect. At a 1 to 2% refusal rate, the 48 sessions that can offer a correction had a 38 to 62% prior chance of offering none. That gives a fair chance, not an assurance, of seeing the mechanism fire. `plan.json` overstated this, and the package README says so.
- A refusal counts as a citation refusal when the released diagnoser classifies the session's first answer that way, over the private stream.

## Tasks
- [x] 1. Write the plan, prepare the three jobs on the released commit, check the router, record pre-launch load, and launch them detached.
- [x] 2. Record load during the run, verify spend and fallbacks after it, diagnose every session, and classify every finished answer.
- [x] 3. Run a live transport smoke of the resumed tool-free correction turn, with a synthetic defect.
- [x] 4. Publish with `reproduce.py` and a package test that refuses changed evidence: `tests/test_citation_correction_measurement_report.py`.
