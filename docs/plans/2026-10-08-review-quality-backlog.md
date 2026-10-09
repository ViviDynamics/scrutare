# Research-informed review quality backlog

Date: 2026-10-08. Status: backlog approved for shipping by the maintainer. Each issue receives
a concrete implementation plan before coding. R1–R9 map to filed issues #43–#51.

## Goal and evidence boundary

Improve actionable defect detection across distinct perspectives while controlling
false positives, latency, and token cost. Each issue must ship an independently
usable capability and report its effect against a shared baseline. Preserve nare
as the model execution layer, caller-owned attribution, bounded accounting,
read-only review, code-derived verdicts, and retained replay evidence.

The papers support investigating repository context, concrete inspection tasks,
supervised collaboration, and decision protocols. They do not prove a universally
best persona roster or that more agents and debate improve code review. Semantic
verification, the routing rules below, and promotion thresholds are our proposed
engineering choices, to be tested rather than attributed to the papers.

## Current implementation

- `engine/review_inputs.py` prepares only `diff.patch`, `files.json`, and
  `context.json`. `engine/persona_inputs.py` restricts reviewers to this view.
  Despite contextual instructions in the persona prompts, full changed files,
  unchanged callers, contracts, and associated tests are unavailable.
- `personas/builtins.yaml` contains concrete senior-dev, junior-dev, security,
  and devops prompts; the baseline is already more than generic role labels.
- `findings/verification.py` validates diff anchors, with one correction
  opportunity. This establishes comment placement, not defect validity.
- `findings/models.py` and `engine/session_output.py` accept file, line, side,
  category, problem, and reason. Trigger, supporting context, and counterevidence
  are not separately validated evidence fields.
- Panel already supplies independent first passes. Debate already has bounded
  rounds, a chair, shared accounting, preserved captures, and escalation. Improve
  those mechanisms rather than create competing strategies.
- Iterative carries findings across pushes. Additional context will require
  dependency-aware invalidation when supporting files change.

## Research references

- [CodeAgent, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.632/): direct
  automated-review research; collaborative agents and a supervisory relevance
  checker, evaluated on change intent, vulnerability, style, and revision tasks.
- [Magistrate, 2025 preprint](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5973895):
  repository context, related-file grouping, and static analysis plus LLM review.
  Preliminary evidence, including evaluation on a 108-PR subset.
- [Perspective-Based Reading, IEEE Computer 2000](https://www.cs.umd.edu/~basili/publications/journals/J79.pdf):
  explicit designer, tester, and user inspection procedures. Human requirements
  inspection, not an LLM code-review experiment.
- [Code Broker, 2026 technical report](https://arxiv.org/abs/2604.23088): parallel
  specialized assessment and tool evidence. Its qualitative evaluation is too
  limited to establish comparative review quality.
- [Voting or Consensus?, Findings ACL 2025](https://aclanthology.org/2025.findings-acl.606/)
  and [Demystifying Multi-Agent Debate, Findings ACL 2026](https://aclanthology.org/2026.findings-acl.1694/):
  decision protocol, initial diversity, confidence, and cost matter. These are
  reasoning-task studies; applicability to code review requires local evaluation.

## Order and dependencies

| Order | Draft | Deliverable | Depends on |
| --- | --- | --- | --- |
| 1 | R1 | Labelled evaluation corpus and non-posting runner | None |
| 2 | R2 | Immutable repository context at captured revisions | R1 |
| 3 | R3 | Procedure-based perspectives and verification reviewer | R1; evaluate with R2 |
| 4 | R4 | Structured, versioned evidence for findings | R1; context references use R2 |
| 5 | R5 | Evidence assessment with explicit unresolved outcomes | R2, R4 |
| 6 | R6 | Capture and normalize static-analysis evidence | R2, R4 |
| 7 | R7 | Discuss disputed claims and preserve supported minorities | R5 |
| 8 | R8 | Deterministic specialist routing and related-file batching | R2, R3 |
| 9 | R9 | Compare architectures and promote measured improvements | R1–R8 |

R3 and R4 can use separate worktrees once R1's evaluation interface is stable.
R6 can proceed alongside R7 after their prerequisites merge. R8 can proceed
alongside R5/R6/R7, but must integrate their evidence and budget contracts before
R9. Treat the numbered order as preferred merge order, not nine mandatory serial
development sessions. Use one issue per PR and fresh implementation/review agents
under the existing ship-issue workflow when execution is requested.

The deferred GitHub App issue #18 is independent of every issue here. Evaluation
and review improvements use the existing engine and do not need an App identity.

## R1 (#43) — Add a labelled code-review evaluation corpus and non-posting runner

**Problem:** Passing protocol and orchestration tests does not measure whether
reviewers find real defects or emit misleading findings.

**Scope:** Add local captured-PR evaluation using the production engine through
nare, without GitHub posting. Start with 30 reviewed cases: 20 defect-bearing and
10 clean controls, spanning correctness/contracts, security, tests, operations,
and consumer-facing regressions. Include cross-file defects and plausible but
incorrect allegations. Reserve 10 cases, including clean controls, as held-out
evaluation. Record provenance/licensing for external material; label files must
never enter the model read root.

**Acceptance:**
- Each defect has a documented trigger, impact, supporting evidence, acceptable
  finding match criteria, and fix or counterexample. Use blinded human adjudication
  for ambiguous matches; an LLM judge is not sole ground truth.
- Compare senior-dev alone, the current four-persona panel, and current debate.
  Fix model/version/settings and total review token limits; repeat each case three
  times and report actual usage, latency, partial runs, and accounting failures.
- Report finding precision, defect-level recall, clean-PR false-positive rate,
  duplicate rate, per-perspective unique detections, and tokens per true positive.
  Missing or invalid runs cannot be counted as clean approvals.
- Offline deterministic tests cover scoring and non-posting. Paid model runs are
  explicit evaluation jobs, separate from ordinary CI. Publish the initial measured
  baseline; no accuracy claim based on mocked model output.

**Likely touchpoints:** new evaluation package/CLI and fixtures; `engine/strategy.py`,
`engine/ingestion.py`, and retained run artifacts. Establish the interface before
downstream issues start. The initial corpus is a pilot, not broad statistical proof.

## R2 (#44) — Supply bounded, immutable repository context to reviewers

**Problem:** Reviewers cannot trace unchanged callers or tests from their current
diff-only read root.

**Scope:** Capture text files at exact base/head revisions, expose full changed
files and bounded related context, and retain a manifest of revision, path, hash,
selection reason, omissions, and size limits. Distinguish files usable as context
from files eligible for findings; comment anchors remain in selected diff hunks.
Specify explicit context include/exclude rules rather than silently broadening
the existing changed-file selection rules.

**Acceptance:**
- A cross-file fixture permits tracing a changed function through an unchanged
  caller and its tests. Retrieval never reads a moving branch or ambient checkout.
- Missing, binary, oversized, excluded, deleted, and renamed content has explicit
  treatment. No traversal, symlink escape, execution, or credentials in read roots.
- Selection and truncation are deterministic and saved for replay. All strategies
  share the same captured evidence contract.
- Iterative findings are reassessed when recorded supporting dependencies change,
  even when the original finding's diff hunk remains unchanged.
- Compare diff-only and contextual runs with equal model/token limits using R1.

**Likely touchpoints:** `engine/ingestion.py`, `engine/github.py`,
`engine/review_inputs.py`, `engine/persona_inputs.py`, `engine/iterative.py`,
provenance/replay and installed runtime tests. Basis: Magistrate; quality gain is
a hypothesis to measure.

## R3 (#45) — Define inspection procedures and add a testing/verification perspective

**Problem:** Roles need distinguishable inspection outputs; test adequacy currently
appears as a secondary responsibility in several prompts.

**Scope:** Give each built-in a versioned procedure: correctness traces contracts
and state transitions; security traces input to sensitive sinks; operations traces
delivery/failure/recovery; the consumer perspective traces usage and hidden
preconditions. Add an opt-in testing/verification reviewer that constructs boundary
cases and checks whether assertions would detect the claimed failure. Preserve
existing persona names and overrides; clarify junior-dev's consumer responsibility
without breaking configuration.

**Acceptance:**
- Each procedure states evidence to inspect, a concrete failure hypothesis, and
  criteria for refusing unsupported findings. Tests lacking an assertion are not
  automatically blocking unless a concrete consequence is established.
- Record the effective prompt/procedure hash. Retain independent first passes and
  the existing category/verdict ownership rules.
- Compare the current panel, revised procedures, and revised procedures plus the
  testing reviewer separately, keeping total token budgets equal.
- Publish unique detections, overlap, and false positives; the new reviewer is
  selectable before any default roster change.

**Likely touchpoints:** persona registry/builtins, prompt capture, docs, R1 cases.
Basis: perspective-based reading; transfer from human inspection is experimental.

## R4 (#46) — Add versioned evidence records to findings

**Problem:** Free-text problem/reason fields cannot reliably bind supporting
evidence or distinguish a failure trigger from a conjecture.

**Scope:** Introduce a versioned evidence contract with trigger/preconditions,
expected versus observed behavior, impact, and base/head context citations. Keep
evidence separate from the inline comment anchor. Stable candidate IDs are
caller-generated; model assertions and mechanically validated citations remain
distinct. Preserve the ability to load/replay existing artifacts.

**Acceptance:**
- Validate cited revision, path, line range, and content hash against the captured
  view. Nonexistent citations cannot become validated evidence.
- A valid citation is labelled as such, never as proof that the claim is true.
- Reanchoring changes only the comment anchor; merging retains every source and
  its evidence. Old findings remain explicitly legacy evidence, not retroactively
  assessed findings.
- Version schemas, capture/replay, posting serialization, debate selections, and
  iterative persistence together; no silent acceptance of mixed-version records.

**Likely touchpoints:** `findings/models.py`, `engine/session_output.py`, dedupe,
reanchor, debate/iterative models, replay, provenance and poster. This is a proposed
foundation for supervision, not a field schema claimed by the papers.

## R5 (#47) — Assess defect evidence separately from anchor validation

**Problem:** A correctly placed comment can still describe an invented defect.

**Scope:** Add opt-in assessment after initial anchor checks. A separate bounded
nare session examines a candidate's supporting and contradictory context and
returns supported, refuted, or unresolved, with citations and reasoning. Code
validates references and applies policy; the assessor cannot supply a PR verdict
or rewrite a candidate into a new allegation.

**Acceptance:**
- Preserve original candidates, assessments, counterevidence, and dispositions.
  State explicitly that model-based support is not formal proof.
- A supported minority finding survives absent independent refutation; agreement
  counts and self-reported confidence alone cannot decide it.
- Refuted findings are excluded with retained reasons. Unresolved potentially
  blocking findings cannot silently become approval. Specify escalation behavior
  and distinguish uncertainty from technical failure or incomplete accounting.
- Assessment shares the review ledger; reserve a documented assessment allocation
  before discovery so initial fan-out cannot consume it accidentally.
- R1 measures precision improvement and any recall loss, including correct
  minority findings and convincing false claims. Replay checks recorded policy
  and evidence without pretending to establish semantic truth.

**Likely touchpoints:** new assessment stage, `engine/budgets.py`, strategy/panel
composition, findings/verdict artifacts and replay. Inspired by CodeAgent's
supervision; semantic truth assessment is our extension.

## R6 (#48) — Supply captured static-analysis evidence through trusted adapters

**Problem:** Reviewers currently lack tool-produced evidence to corroborate or
challenge their interpretations.

**Scope:** Accept captured CI/static-analysis results and add a first trusted,
pinned adapter for Python analysis or declarative AST checks. Normalize diagnostics
to revision, tool/version, rule, location, and source artifact. Keep Scrutare's
read-only boundary; do not add arbitrary PR commands or application boots.

**Acceptance:**
- Reject mismatched revisions and retain unavailable, failed, or truncated tool
  results explicitly. Tool silence is not proof of correctness.
- Any executed adapter ignores PR-supplied executable configuration/plugins,
  operates in an isolated environment without credentials/network, and has
  resource limits. If those guarantees are unavailable, use captured results only.
- Diagnostics are evidence inputs, not automatic blocking findings. Retain source
  artifacts and hashes, account for model consumption of tool results, and record
  tool runtime separately.
- Benchmark incremental benefit over contextual review/assessment without tools.

**Likely touchpoints:** new evidence adapters, prepared input manifests, assessment
inputs, provenance and fixtures. Basis: Magistrate and Code Broker; keep qare's
execution/testing responsibilities separate.

## R7 (#49) — Focus debate on disputed evidence and protect supported minority findings

**Problem:** The current debate sends the full finding pool to every perspective
each round and allows chair selection; that can spend tokens on settled claims
and lose valid findings without an evidence-based explanation.

**Scope:** Extend the existing debate path with stable candidate IDs and explicit
disputes. Keep initial discovery independent; discuss only conflicting or unresolved
claims, with the smallest relevant evidence bundle. Require evidence-backed
dispositions rather than majority agreement. Keep round bounds and escalation.

**Acceptance:**
- Supported undisputed findings bypass discussion. Every rejection or downgrade
  names the claim and supporting counterevidence; code rejects invalid transitions.
- Correct one-reviewer security/correctness claims survive unsupported opposition.
  Unresolved blocking claims escalate under explicit policy.
- Equivalent-claim grouping retains all sources. Different triggers or causes
  at the same anchor remain distinct; consolidation cannot erase a blocking source.
- Compare independent aggregation, assessment without debate, one discussion
  round, and bounded multiple rounds at equal total token limits. Record each
  round's new evidence, disposition changes, tokens, and latency.

**Likely touchpoints:** `engine/debate.py`, `engine/debate_inputs.py`, output
contracts, budget reservation, consolidation and replay. Basis: the two ACL debate
studies; no assumption that the longest discussion wins.

## R8 (#50) — Route specialist procedures and group related files deterministically

**Problem:** Running every possible specialist on every PR wastes budget, while
reviewing related implementation and tests separately can lose relationships.

**Scope:** Add opt-in routing from captured changed paths and explicit trusted
configuration to performance/concurrency, data-integrity/migrations, and configured
domain procedures. Group implementation, callers, contracts, and tests into related
work units. Start with inspectable deterministic rules, not another model delegator.

**Acceptance:**
- Persist routing inputs, activated/omitted procedures, grouping rationale, and
  allocation. Provide a manual override and a generalist fallback for unknown cases.
- A specialist activation cannot silently remove baseline correctness or security
  coverage; allocation changes are explicit within the fixed review budget.
- Preserve cross-group dependencies, related context, and global source attribution.
  Test mixed, renamed, large, and unrecognized file sets.
- Measure routed versus fixed-panel recall/cost on covered and missed routing
  cases. Specialists ship opt-in pending R9.

**Likely touchpoints:** config/personas, new routing module, fan-out/context
selection, budgets, artifact contracts. Related-file grouping follows Magistrate;
the proposed routing mechanism is a locally testable design choice.

## R9 (#51) — Publish architecture comparisons and promote only measured improvements

**Problem:** Independently useful features still need evidence that their
combination improves review quality before they become defaults.

**Scope:** Freeze configurations before held-out evaluation. Compare baseline,
context only, procedures only, combined context/procedures, assessment, tool
evidence, focused debate, and specialist routing. Isolate component effects rather
than compare only the old system with a larger, more expensive system.

**Acceptance:**
- Report paired case-level results across three repetitions at equal review token
  ceilings, with actual usage, precision/recall by defect type, false-positive PRs,
  duplicates, failure rates, and latency. Document provider/model/prompt versions
  and corpus limitations. Keep held-out labels out of prompt tuning.
- Proposed promotion rule, fixed before evaluation: defect recall improves by at
  least 5 percentage points with precision no more than 2 points lower, OR precision
  improves by at least 5 points with recall no more than 2 points lower. No new
  misses on the critical-security fixture set; actual token use and p95 latency
  remain within 20% of baseline. These are product trade-offs, not paper findings.
- The 30-case pilot is insufficient for sweeping accuracy claims. Report uncertainty
  and require an expanded held-out corpus if results are unstable or inconclusive;
  retain opt-in status rather than force a winner.
- Promote only the components meeting the declared gate. Publish effective defaults,
  compatibility/migration notes, a revert path, and limitations. Retain a lightweight
  offline regression set in CI and run paid quality evaluations explicitly.

**Likely touchpoints:** evaluation reports, config defaults, docs, installed
compatibility tests and release notes.

## Execution boundaries

These are issue-ready scope proposals. Detailed interfaces, schema choices,
configuration keys, and test plans must be reviewed in each issue's design before
implementation. Every runtime issue needs focused behavioral tests, relevant
installed nare/CLI/MCP compatibility checks, and the repository's release checks.
No default architectural change should be justified solely by passing mocked
integration tests or borrowing a paper's reported improvement percentage.
