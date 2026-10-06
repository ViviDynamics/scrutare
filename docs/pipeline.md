# Reusable GitHub Action

Scrutare `2026.10.1` includes the reusable Action and its fixed runtime pin.
Before using this pin, verify the matching GitHub tag, release wheel, public
GHCR image, and literal hosted caller success/failure acceptance evidence.
Asset publication alone does not establish hosted acceptance.
The published `2026.10.0` release has a wheel and image, but no reusable Action.

Commit a reviewed `scrutare.yaml` to the PR's base branch first. Configure its
personas, provider rail, paths, and budgets using the existing
[configuration reference](config.md). The Action reads config bytes from the
captured base SHA, even if the PR edits the same file. Configuration introduced
only in the PR cannot configure that PR's review. A Linux runner needs Docker,
Python 3.10 or newer available as `python3`, and network access to GitHub, GHCR
and the configured provider. GitHub-hosted Ubuntu supplies the host tools.

This caller is exactly ten lines and requires no caller checkout:

```yaml
name: Scrutare
on: {pull_request_target: {types: [opened, reopened, synchronize, ready_for_review]}}
permissions: {contents: read, pull-requests: write}
jobs:
  review:
    runs-on: ubuntu-24.04
    steps:
      - uses: ViviDynamics/scrutare@2026.10.1
        env: {GH_TOKEN: '${{ github.token }}', OPENAI_API_KEY: '${{ secrets.OPENAI_API_KEY }}'}
        with: {config: scrutare.yaml}
```

The example uses the repository secret `OPENAI_API_KEY`. For an Anthropic rail,
replace that variable with `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`.
Provision the matching credential before admitting reviews.
`pull_request_target` runs the trusted base workflow for forks and can expose
secrets to model-driven reviews of untrusted diffs. Restrict admission to your
approved contributors or an explicit maintainer-controlled gate. Never add a
checkout or execution of PR head code. Same-repository `pull_request` events are
also supported; fork `pull_request` events are refused. Accepted event actions
are `opened`, `reopened`, `synchronize`, and `ready_for_review`.

The Action uses `GITHUB_TOKEN` through `GH_TOKEN` and posts as `github-actions[bot]`;
a custom token changes the identity. Set `contents: read` and
`pull-requests: write`, and enable the repository or organization policy
**Allow GitHub Actions to create and approve pull requests** before approval
acceptance. Fork review policies still apply. The M4 GitHub App identity is not
implemented. Every model call goes through the separately pinned nare runtime.

## Contract

| Surface | Meaning |
| --- | --- |
| `with.config` | Relative config path, default `scrutare.yaml`, in the trusted base repository/SHA. No absolute paths, traversal, control characters or symlinks. |
| `GH_TOKEN` | Already authorized GitHub token, forwarded to gh inside the container. |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | Optional provider credentials matching the trusted config, forwarded only when present. |
| GitHub runner environment | Event file/name, repository, run ID/attempt, workspace, temp and output paths must be supplied by the runner. |
| `outputs.verdict` | `approve`, `changes_requested`, or `escalated`, emitted only after one successful posted CLI result is validated. |
| `outputs.head-sha` | Captured reviewed SHA from the CLI, which can differ from event head after a force-push. |
| `outputs.run-dir` | Absolute runner-local run directory, useful only in this job; download the uploaded artifact for durable evidence. |

There are no command, engine, image, executable, model, budget, URL or identity
inputs. Runtime is fixed to `ghcr.io/vividynamics/scrutare:2026.10.1`. The internal
checkout and uploader are pinned to official v7.0.1 commits. Internal checkout
uses only the base repository and exact base SHA, without credential persistence,
submodules or LFS. The Action copies only the regular trusted config into a
separate read-only container mount and runs as the host UID/GID with `HOME=/tmp`.
The caller's source checkout and home are never container mounts.

One CLI review runs, with no wrapper retry. A posted `changes_requested` verdict
is a successful execution; branch protection should enforce the review itself.
A nonzero CLI exit fails the Action and emits no success outputs, even when a
saved verdict exists. Successful stdout must exactly match persisted `result.json`
and satisfy the pinned producer envelope. Upload success preserves an earlier
failure; upload failure also fails the Action. Failed preparation admits no
review, and without an owned evidence root no upload is attempted.

## Evidence, costs and posting recovery

A unique `scrutare-<run-id>-<attempt>-<invocation>` artifact retains only this
invocation's raw CLI stdout/stderr and working directory, including hidden
`.scrutare/runs`, sessions, accounting, captured config and posting evidence.
It excludes the config-source checkout, host home and authentication files.
Hidden files are explicitly included, missing files are an error, overwrite is
disabled, and retention is seven days, subject to repository limits. Artifacts
can contain sensitive source, prompts and private provider error detail. Restrict
repository/artifact access appropriately; do not assume an uploaded archive is
secret storage. Credentials passed through environment are not intentionally
saved, but provider output can echo private content. Manifests and unsigned local
receipts prove local consistency, not genuine remote delivery or provider billing.

Budgets are after-turn allowances, not hard spending ceilings. Already admitted
turns may overshoot. Actual usage and overshoot remain recorded without clipping.
Missing initial findings or uncertain accounting fails the panel; initial sessions
are not retried. One anchor correction can consume remaining live allowance.

For repeated pushes, add job concurrency with a group keyed by repository and PR,
for example `scrutare-${{ github.repository }}-${{ github.event.pull_request.number }}`,
and set `cancel-in-progress: false`. GitHub can replace an older pending run, so
this is not an every-push queue. Avoid canceling a review during posting. The
adapter stops its named container on runner cancellation, but runner shutdown,
forced kill and a request already accepted by GitHub limit what cancellation can
prove. Evidence upload itself is also best effort during cancellation.

An uncertain POST is never automatically retried. Download the archive, inspect
`posting.json`, the exact review payload and captured SHA, then inspect remote
reviews as the recorded bot identity. Resolve whether delivery occurred before
starting any new review. A persistence failure after delivery still fails the
Action; a failed job alone does not prove no remote review exists. See
[posting](posting.md) and [CLI usage](cli.md).

## Hosted release acceptance

[The acceptance workflow](../.github/workflows/action-acceptance.yml) remains
inactive until a maintainer intentionally pushes to a same-repository PR from
`14-action-acceptance` with exactly one acceptance label. After merge and
publication of `2026.10.1`, create a draft temporary PR named
**Scrutare 2026.10.1 Action acceptance** from current main. Add
`scrutare-action-acceptance-success`, then push a tiny harmless change only to
`tests/fixtures/action-acceptance/target.py`. Only a `synchronize` event admits
this focused identity/delivery run. The trusted success fixture uses
`github.post_mode: comment` and senior-dev,
OpenAI `spark/glm-5.3-flash` at `https://llm.vividynamics.com/v1`, and 10000 tokens for
both persona and review after-turn allowances. It reviews only that fixture
path, not the whole PR. Any valid delivered verdict demonstrates execution;
the posted review is COMMENT and does not prove permission to APPROVE. Native
container cases separately verify default review approval and blocking semantics.

Before running acceptance, authorize and install the repository secret
`OPENAI_API_KEY` for that rail. Hosted comment-mode acceptance does not require
changing organization approval policy. Do not add the label until publication
and credential setup are verified.
Save the caller PR URL, workflow run URL/head, captured CLI SHA, actual remote
bot review ID/login/receipt, downloaded artifact SHA-256 and every extracted
file digest, using the [evidence collection commands](../tests/fixtures/action-acceptance/README.md).
A local factory test or direct script invocation cannot supply
literal hosted identity or delivery evidence.

After success is recorded, remove the success label, add
`scrutare-action-acceptance-failure`, then push another tiny fixture-only diff.
The invalid trusted config fails before any model call and forwards no provider
secret. Confirm the composite review step and parent job fail, the internal
uploader succeeds, diagnostics download intact, and success outputs are absent.
Do not convert this expected failed job to an allowed failure. Remove acceptance
labels afterwards. Never retry a run with uncertain posting. The issue remains
incomplete until both real hosted outcomes and published assets are recorded.
