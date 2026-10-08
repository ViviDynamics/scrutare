# scrutare

A code review agent harness: many perspectives, one verdict, decided by code.

scrutare reviews a pull request the way a review team does. A panel of
reviewer agents, each a [nare](https://github.com/ViviDynamics/nare) session
with its own perspective (senior developer, junior developer, security,
devops), surveys the diff and the surrounding code read-only, and reports
structured findings. Code verifies that every finding is anchored to the diff,
derives the verdict by rule, and produces replayable review artifacts. The
CLI posts one captured-head review with inline comments and durable receipts. The default
panel performs one convergence pass; failed coverage withholds a verdict.

scrutare sits beside [nare](https://github.com/ViviDynamics/nare),
[qare](https://github.com/ViviDynamics/qare), and
[coordinare](https://github.com/ViviDynamics/coordinare) in the Coordinare
project family: an orchestrator calls nare to develop, scrutare to review, and
qare to QA. The engine is standalone, with a complete review CLI and Python API. A reusable
GitHub Action, MCP server, and conductor/coordinare reviewer integration are
planned interfaces.

## Three ways to converge

`strategy` is a top-level config setting. Panel is the implemented default;
iterative and debate are planned and the engine rejects them before execution:

- **panel** (milestone 1): the perspectives review independently, in one
  wave. Code verifies anchors, offers one bounded correction opportunity per
  persona, dedupes findings, and derives the verdict by rule.
- **iterative** (milestone 2, not yet implemented): the findings pool persists,
  and each new push is reviewed for what is new or contested, until the bound
  is reached.
- **debate** (milestone 3, not yet implemented): the perspectives see each
  other's findings and a chair session arbitrates disputes, downgrades nitpicks,
  and accepts the final finding set.

The verdict is derived by code, never declared by a model: any
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

PR ingestion, YAML configuration validation, the persona registry, and the pure
findings pipeline, code-derived verdicts, and durable Python poster API are
implemented. Capture prepares filtered artifacts and the public per-persona
input API fixes each persona's read root and read-only tool policy. The
[path filters guide](docs/path-filters.md) describes this boundary. The
[findings guide](docs/findings.md) covers
validated input, diff anchors, one supplied correction round, and lossless
dedupe. The [posting guide](docs/posting.md) covers captured-head reviews,
durable receipts, bounded retries and uncertain-delivery recovery. The
[escalation guide](docs/escalation.md) covers code-derived exhaustion, COMMENT
summaries and durable human review requests through the Python API. The
[replay guide](docs/replay.md) covers offline verdict recomputation and separate
saved and locally recorded posted comparisons. The Python
[panel API](docs/panel.md), `scrutare.engine.strategy.run_review`, runs the
complete panel pipeline through an external nare executable, verifies and
corrects anchors against filtered inputs, dedupes, and writes findings, panel
evidence and a code-derived verdict. A valid partial findings document counts,
including an explicitly empty array; missing or failed initial output withholds
the verdict. Corrections share the initial wave's remaining reported token
allowance. Limits apply after turns and can overshoot. The CLI posts the resulting review and records delivery; Python panel callers
choose when to post. The lower-level
[session fan-out API](docs/session-fanout.md) remains available for candidate
findings. The [review CLI](docs/cli.md) captures, runs the panel and posts one review.
[Release packaging](docs/releases.md) builds a wheel and a non-root image from
the same package version.
The design and milestone order are in
[docs/SPEC.md](docs/SPEC.md).

## Install and review a pull request

The first Scrutare release has not been published. Once the `2026.10.0`
[GitHub release](https://github.com/ViviDynamics/scrutare/releases) and its
assets are available, install its pinned wheel with Python 3.10 or newer:

```sh
uv tool install --python 3.10 \
  https://github.com/ViviDynamics/scrutare/releases/download/2026.10.0/scrutare-2026.10.0-py3-none-any.whl
```

Wheel users also need the [GitHub CLI](https://cli.github.com/) on PATH and a
separate [nare 2026.10.4 installation](docs/session-fanout.md). Use existing
authorized GitHub access with permission to read the PR and submit reviews,
and credentials for the selected model provider. Every model call goes through
nare. Scrutare does not install nare into its own environment.

Create `scrutare.yaml` in your repository:

```yaml
models:
  default:
    model: <model>
```

```sh
scrutare review --pr https://github.com/owner/repo/pull/12 \
  --nare-executable /absolute/path/to/nare-environment/bin/nare
scrutare review --pr 12 --config path/to/settings.yaml
```

`--nare-executable` defaults to `nare` on PATH. A numeric PR resolves the
repository through `gh repo view` from the current checkout. The command reads
configuration once before GitHub access, captures a coherent PR, runs the panel,
and posts the verdict against the captured head. Success prints the exact JSON
saved in the run's `result.json`, after recording delivery and `artifacts.json`.
A delivered changes-requested verdict still exits 0. Failure exits 1 with no
success document; usage errors exit 2 and interruption exits 130.

After the corresponding image is published, the pinned container includes gh,
git and the separate nare runtime:

```sh
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e GH_TOKEN -e ANTHROPIC_API_KEY -v "$PWD:/work" \
  ghcr.io/vividynamics/scrutare:2026.10.0 review --pr 12
```

This forwards credentials already supplied for an authorized run. Choose the
credential variable matching your configured provider. The image defaults to
UID/GID 10001; the example uses your host identity so mounted run artifacts stay
writable. See [releases](docs/releases.md) for local installation before the
first release and container details.

The default panel verifies anchors, allows one correction opportunity and derives
the verdict in code. Valid partial findings can produce a verdict; missing or
failed initial output withholds it. Token limits apply after turns and can
overshoot. Check `panel_status`, coverage and accounting evidence even on a
successful approval. Iterative and debate strategies are rejected before GitHub
access. Review does not manufacture escalation from failure or a budget stop.

Every run retains raw capture and configuration bytes, a filtered three-file
reviewer root, sessions, findings, verdict and delivery evidence when available.
Default filters exclude `docs/**` and Markdown files. The manifest inventories a
quiescent local snapshot; it does not authenticate artifacts or prove remote
delivery. Replay continues to use canonical findings, policy and verdict bytes.
See [CLI usage](docs/cli.md), [configuration](docs/config.md),
[path filters](docs/path-filters.md) and [persona authoring](docs/writing-personas.md).

## Audit a saved verdict offline

```sh
scrutare replay "/path/to/run artifacts"
python -m scrutare replay "/path/to/run artifacts"
```

Replay needs stored `findings.json`, effective `config.json`, and `verdict.json`,
without authentication, network, models or current-directory configuration.
It reads artifacts without changing them and prints one JSON result with
separate saved and locally recorded posted identity. Exit codes are 0 for
identity, 1 for differences and 2 for incomplete or invalid evidence. An
ingestion-only run lacks the verdict evidence needed for replay. Saved identity
alone does not confirm posting; recorded exhaustion does not prove strategy
execution. See the [replay guide](docs/replay.md) for target availability,
request delivery status, evidence formats and the trust boundary.

## Development checks

CI checks Python 3.10 and 3.14. Use the same commands locally:

```sh
uv sync --locked --extra dev
uv run --locked --extra dev pytest
uv run --locked --extra dev ruff check .
uv run --locked --extra dev mypy src scripts/check-release.py
uv build --wheel
```

CI also installs nare 2026.10.4 in a separate Python 3.14 environment pinned
to its public release commit and locked dependencies. Each matrix job runs
offline tests through that installed CLI. To include the same proof locally,
set `SCRUTARE_TEST_NARE_EXECUTABLE` to the absolute path of the separately
installed release's `nare` executable before running pytest. An absent setting
skips this supplemental suite locally; a configured but unusable runtime fails.
The suite makes no live model calls and strips provider credentials.

The full preflight table, including a wheel installation check, is in
[.agents/test-commands.md](.agents/test-commands.md). `repo.env.example`
contains shared Vivi Dynamics workflow settings. Copy it to local `repo.env`
for the workflow tools. Authentication uses already configured, authorized `gh`
access; keep login commands and credentials outside the example. Scrutare runtime selection uses `--nare-executable`; provider credentials belong
to the selected model rail.

## Licensing

scrutare is source-available under the [Elastic License 2.0](LICENSE), the
same license as the rest of the Coordinare family. You may run, modify, and
self-host it, including commercially. You may not offer it to third parties as
a hosted or managed service.

Agent harnesses can request the same full review through the
[stdio MCP server](docs/mcp.md), installed as `scrutare-mcp`.
