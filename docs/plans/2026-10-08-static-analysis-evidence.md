# Captured static-analysis evidence

Issue #48

## Scope
In: a strict adapter for captured Ruff 0.16.10 JSON, explicit caller-supplied
capture, pinned revision/tool provenance, bounded diagnostics, immutable prepared
evidence and replay/iterative binding.
Out: executing PR commands, plugins, application code or networked analysis.
Scrutare accepts captured results because an execution sandbox is not established.

## Assumptions
- `analysis.enabled` is opt-in and requires immutable repository context.
- A CLI caller may explicitly provide a capture; repo configuration cannot name
  arbitrary local evidence paths. Missing captures are explicitly unavailable.
- The pinned Ruff adapter normalizes data only. Tool provenance is supplied by
  the capture producer; local unsigned hashes establish integrity, not authenticity.
- Only diagnostics whose locations bind captured head bytes enter the model root.
  Raw output remains private, including unavailable, failed and truncated results.
- Diagnostics cannot become findings or change verdicts without model review.
- Tool duration is separate from token accounting; model consumption uses the
  existing shared nare ledger. Comparative benefit remains unmeasured until #51.

## Tasks
- [x] 1. Configuration and strict bounded capture adapter: red revision/version,
  malformed diagnostics, traversal and partial/unavailable tests.
- [x] 2. Immutable prepared inputs and shared prompt policy: red exclusion,
  tampering and legacy byte-compatibility tests.
- [x] 3. Explicit CLI/ingestion and iterative/replay source binding: red lifecycle
  tests plus installed wheel/nare proof that no tool is executed or finding created.
- [x] 4. Corpus adapter and measured comparison hooks, documentation.
- [ ] 5. Independent review, full two-lane gates, and incremental quality comparison.

## Verification evidence

- Parent observed initial adapter/config red: 17 failures and one pass.
- Lifecycle tests failed for missing CLI/capture propagation and lost projected tool
  evidence, then passed after implementation.
- Corpus declaration/freeze tests failed for absent fields and unchecked paths,
  then passed after implementation.
- Separate normalized-byte bound failed before deterministic truncation was added.
- 32 focused adapter/lifecycle/corpus tests pass; 531 existing area tests passed.
- Installed wheel plus external nare read normalized diagnostics as evidence while
  returning zero findings; no tool execution capability existed. Raw-source tamper
  was rejected by offline replay.
- Full verification and comparative benefit are owned by the shipping coordinator.
- Independent review round 1 found real Ruff `name`/`severity` fields were rejected
  and Python `splitlines()` disagreed with Ruff on form feed and U+2028. Real pinned
  Ruff regressions observed seven failures, then exactly two range failures after
  the metadata fix; bounded metadata validation and CRLF/CR/LF splitting resolved
  both. Unknown-field rejection and Unicode scalar columns remain covered.
- The expanded adapter/context/lifecycle/evaluation/native area passed 623 tests
  on Python 3.10; the installed-wheel proof now consumes generated Ruff JSON.

## Final dependency integration
- Rebased only the two tool-evidence commits onto merged context, procedures, v2 evidence, assessment and comparison foundation. Preserved every existing configuration field and the iterative head revision binding for tool-manifest dependencies.
- Independent scoped integration review: no findings in config, iterative union, evaluation freeze-before-await/snapshot provenance or the combined installed proof.
- Integrated adapter/config/assessment/iterative/evaluation/ingestion/read-root checks: 569 passed, including installed nare with real captured Ruff both alone and together with v2 assessment. Existing tamper refusal and zero automatic findings remain asserted. Ruff and strict types passed.
- Both full canonical Python lanes are required before push. Incremental tool benefit remains pending under #51; the issue stays open.
