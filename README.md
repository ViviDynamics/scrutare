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

PR ingestion is implemented. The review panel, model sessions, findings,
verdicts, posting, replay, and configuration semantics are planned. The design
and milestone order are in [docs/SPEC.md](docs/SPEC.md).

## Capture a pull request

Install Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), and the
[GitHub CLI](https://cli.github.com/). `gh` must already be authenticated with
read access to the repository. Scrutare uses that existing authentication.

```sh
uv sync --locked --extra dev
uv run --locked scrutare --version
uv run --locked scrutare review --pr https://github.com/owner/repo/pull/12
uv run --locked scrutare review --pr 12 --config scrutare.yaml
```

A numeric PR reference resolves the repository from the current checkout
through `gh repo view`. `python -m scrutare` exposes the same interface.
`--config` is optional and must name an accessible regular file. Its bytes are
copied verbatim to `config.yaml`; YAML validation and settings arrive in issue
#8. A config error fails before GitHub access.

The command captures inputs only and makes no model calls or GitHub writes.
It returns a nonzero exit code with a concise error if capture fails, including
when the PR closes or merges. Success prints one JSON object:

```json
{"status":"ingested","run_dir":".scrutare/runs/run-example","head_sha":"captured-head-sha","scrutare_version":"0.1.0"}
```

Each unique run directory contains `diff.patch`, `files.json`, `reviews.json`,
`comments.json`, `review_comments.json`, and `metadata.json`, plus `config.yaml`
when supplied. Metadata records the complete PR object under `pull_request`,
the captured head, repository, PR number, schema version, and application
version. Capture retries if the head changes while inputs are being fetched.
`ingested` means the inputs were saved; a review verdict does not yet exist.

## Development checks

CI checks Python 3.10 and 3.14. Use the same commands locally:

```sh
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy src
uv build --wheel
```

The full preflight table, including a wheel installation check, is in
[.agents/test-commands.md](.agents/test-commands.md). `repo.env.example`
contains shared Vivi Dynamics workflow settings. Copy it to local `repo.env`
for the workflow tools. Authentication uses already configured, authorized `gh`
access; keep login commands and credentials outside the example. Scrutare
ingestion itself requires no application environment variables.

## Licensing

scrutare is source-available under the [Elastic License 2.0](LICENSE), the
same license as the rest of the Coordinare family. You may run, modify, and
self-host it, including commercially. You may not offer it to third parties as
a hosted or managed service.
