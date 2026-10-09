# Deterministic specialist routing

Routing is opt-in and consumes selected captured current and previous paths plus
trusted configuration. It does not inspect repository instructions or invoke a
model to select reviewers. Filename signals are hypotheses, not proof of a defect.

```yaml
routing:
  enabled: true
  mode: auto
  rules:
    - persona:
        name: billing-domain
        system_prompt: |
          Trace billing invariants and retries using captured evidence.
          Seek a counterexample before reporting a concrete trigger and impact.
          Conduct read-only review; never run repository commands or post reviews.
          Follow the caller's output contract; code determines the verdict.
      paths: [billing/**]
  groups:
    - name: billing
      implementation: [billing/**]
      callers: [api/**]
      contracts: [contracts/**]
      tests: [tests/**]
```

Built-in path rules activate `performance-concurrency` for names containing async,
thread, queue, cache, concurrency or performance signals, and
`data-integrity-migrations` for migration directories, SQL, schema, transaction,
database or integrity signals. Exact patterns are retained in `trusted_rules` in
routing artifacts. A configured rule with the same procedure name explicitly
replaces its built-in path rule. Specialists use packaged `routing-v1` procedures;
normal `inspection.procedures` still controls the established reviewer profiles.

Set `mode: manual` and `force: [performance-concurrency]` to select specialists
explicitly. In automatic mode, `force` adds required procedures regardless of path
matches. Manual selection never removes configured reviewers. Every routed run
retains them and adds `senior-dev` and `security` if absent, preserving correctness
and security coverage for unknown paths. An existing inline definition retains its
exact bytes and identity. A conflicting configured inline domain definition fails
rather than silently replacing another definition. Unknown signals remain visible
as `unmatched_paths` and use the general coverage fallback.

`routing-focus.json` is part of the immutable guarded read root. It records selected
old/new identities, activation and omission reasons, trusted patterns, manual
selection, effective reviewers, and related work units. Explicit group selectors
assign implementation, callers, contracts and tests. Remaining paths use a recorded
normalized-basename heuristic, including `test_foo.py` and `foo_test.py`. This does
not discover a call graph or prove a relationship. Related paths are available only
when normal pinned repository context captures them; groups never fetch a branch,
execute source, or expand comment eligibility beyond selected diff hunks.

All reviewers retain the same global context, including cross-group dependencies
and recorded omissions. Grouping annotates focus rather than isolating sessions or
filtering source observations. Panel, debate and iterative review retain original
persona attribution. Iterative child rounds recompute routing for their narrowed
captured changes while retaining the supporting context.

`routing.json` persists the focus record, complete input hashes, actual ledger
allocations, total allocated tokens, configured per-persona ceiling, fixed review
ceiling, and any reserved debate chair quota. The existing ledger divides admitted
capacity fairly across the actual reviewers and chair, without raising the total
ceiling. More specialists can reduce each reviewer's quota; partial, failed or
unstarted sessions continue to withhold a complete result. There is no automatic
promotion: measured routed versus fixed-panel comparisons and missed routing cases
belong to the evaluation promotion gate in #51.
