# Scrutare App threat model

**Status**: current as of 2026-10-13 | **Issue**: [#18](https://github.com/ViviDynamics/scrutare/issues/18)

Written to the discipline of [Conductor's threat model](https://github.com/ViviDynamics/conductor/blob/main/docs/security/threat-model.md):
each boundary states what defends it and what remains exposed. Read this before
exposing the service. The App handles hostile PR text, model credentials and
GitHub review-write tokens. Its decisions never merge code.

## Trust boundaries

| Boundary | Mitigation | Residual risk |
| --- | --- | --- |
| GitHub to webhook | Exact-byte HMAC-SHA256 before parsing, bounded body, explicit installation and repository allowlist, durable delivery ID, queued pushes for one PR coalesced into the review that covers them | A stolen webhook secret can submit work within the allowlist. Genuine pushes spaced apart each start a review and spend the configured model budgets |
| Performer to API | Separate bearer token, allowlisted targets, required idempotency key, no credential or configuration overrides in the request | Every token holder can spend review budgets and read all job records. There is no per-caller isolation, and performer requests are never coalesced |
| PR text to nare | Read-only filtered artifact root, code-verified anchors, code-derived verdict, shared token ledger | Prompt injection can bias observations and chair selections. Empty or partial valid findings can approve. Read the evidence and coverage |
| Repo config to model transport | Configuration from the base repository's default branch only, HTTPS model-origin allowlist over the default rail and every override, validation before any model call | Repository maintainers control prompts, strategy, budgets, context paths, escalation reviewers and models at permitted origins. A compromised model endpoint sees source and the provider credential |
| App to GitHub | Short RSA JWT, fresh installation token narrowed to one repository and the read and review-write permissions it requests, API redirects refused, remote error bodies never surfaced | The service holds the App key. Its compromise can mint tokens for every installation of the App. Keep App permissions and installations narrow |
| Service to CLI and nare | Per-job environment holding only the installation token and provider credentials; the engine's nare environment allowlist strips GitHub credentials; job deadline inside the token lifetime | The CLI process can write reviews and request reviewers on its repository. The model transport holds its provider credential. OS process compromise defeats these boundaries |
| Worker to Kubernetes | Non-root restricted pod, no API token or RBAC, no Docker socket, read-only root filesystem, bounded CPU and memory | Garden's CNI does not enforce NetworkPolicy, so any pod in the cluster can reach the Service and no egress isolation is claimed. The API bearer token is the only defense on `/api/*` inside the cluster |
| Crash to durable delivery | Exclusive single-worker lock, SQLite transactions, permanent artifacts, no automatic replay of interrupted or failed jobs | A crash, deadline or interruption between posting and the terminal record is uncertain delivery. Operators must inspect receipts before resubmitting |

## Exposure

The listener defaults to loopback, speaks plain HTTP, is serialized, and uses
ten-second socket timeouts. TLS, rate limiting and slow-client protection belong
to the route in front of it. On garden that is the Cloudflare tunnel, which
should publish only `/webhook`. A tunnel rule for the whole hostname would also
publish `/api/*` and `/healthz`; the bearer token would then be the only
protection for the performer API on the internet. `/healthz` exposes only
liveness. All job data requires performer authentication. Logging omits request
paths, bodies and headers.

## Identity and delivery

Reviews are written with an installation token, so GitHub attributes them to the
App's bot account. That token cannot read its own login, which the engine needs
only to reconcile an uncertain review POST. Under the App that reconciliation
fails closed: the job ends without a second POST and without a confirmed
result. A delivered but unconfirmed review is possible and is never duplicated
automatically.

An escalated verdict requests the repository's configured human reviewers with
the same token. Those logins come from the default-branch configuration, not
from PR text.

## Credentials and evidence

App, webhook and API credentials stay outside model input and repository
configuration. Secrets are mounted or injected by the operator and never created
from PR instructions. Repository context is read through the GitHub API at
captured SHAs with the same narrowed token; nothing is cloned or executed.
Evidence is unsigned local data, not proof of remote delivery or tamper
resistance. Model token limits are reported after turns and can overshoot. The
queue admission cap and job deadline are not a hard financial ceiling.

## How to verify these claims

- Signature, allowlist, idempotency, bounded queue, coalescing and restart
  behavior: `tests/test_app.py`.
- HTTP authentication, job privacy and the process-group deadline:
  `tests/test_app_http.py`.
- Token narrowing, default-branch configuration, model-origin refusal, credential
  isolation, exit-code and result-contract mapping: `tests/test_app_worker.py`.
- The installed wheel driving the real nare CLI offline, with retained evidence
  and replay: `tests/test_installed_app.py`.
- Manifest restrictions and secret references: `tests/test_app_deploy.py`.

## What this document does not cover

Chat replies, automatic repository fixes, merges and cluster-management APIs are
outside this service. Cloudflare, Argo CD and cluster configuration are verified
where they live, not here. Live deployment and App identity must be demonstrated
with actual installation events, not inferred from mocked or offline tests.
