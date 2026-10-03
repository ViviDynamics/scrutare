# scrutare

A code review agent harness: many perspectives, one verdict, decided by code.

scrutare reviews a pull request the way a review team does. A panel of
reviewer agents, each a [nare](https://github.com/ViviDynamics/nare) session
with its own perspective (senior developer, junior developer, security,
devops), surveys the diff and the surrounding code read-only, and reports
structured findings. Code verifies that every finding is anchored to the diff,
derives the verdict by rule, and posts one review with inline comments. When
the panel cannot converge within its bound, scrutare escalates to human
reviewers instead of pretending to agree with itself.

scrutare sits beside [nare](https://github.com/ViviDynamics/nare),
[qare](https://github.com/ViviDynamics/qare), and
[coordinare](https://github.com/ViviDynamics/coordinare) in the Coordinare
project family: an orchestrator calls nare to develop, scrutare to review, and
qare to QA. The engine is standalone, so it reviews any repository through its
CLI, GitHub Action, or MCP server, and the conductor/coordinare reviewer
performer is a caller like any other.

## Three ways to converge

`strategy` is a top-level config setting. All three behaviors are first-class,
and the implementer of a repository chooses:

- **panel** (milestone 1): the perspectives review independently, in one
  round. Code dedupes their findings, derives the verdict by rule, and posts
  it. The cheap default.
- **iterative** (milestone 2): the findings pool persists, and each new push
  is reviewed for what is new or contested, until the bound is reached.
- **debate** (milestone 3): the perspectives see each other's findings and a
  chair session arbitrates disputes, downgrades nitpicks, and accepts the
  final finding set.

In all three, the verdict is derived by code, never declared by a model: any
blocking finding requests changes, none approves, and the run's artifacts
record both the finding that decided it and the rule that fired.

## Model choice per perspective

One model is better at architecture, another at security, and the config says
so. A review-wide default provider, base URL, and model apply to every
persona, and any persona overrides any of the three:

```yaml
models:
  default:
    provider: anthropic
    model: <model>
  overrides:
    security:
      provider: openai
      base_url: https://litellm.internal/v1
      model: <security-tuned-model>
```

## Status

Design. The spec, including the engine flow, config reference, escalation and
failure paths, and the milestone order, is in
[docs/SPEC.md](docs/SPEC.md).

## Licensing

scrutare is source-available under the [Elastic License 2.0](LICENSE), the
same license as the rest of the Coordinare family. You may run, modify, and
self-host it, including commercially. You may not offer it to third parties as
a hosted or managed service.
