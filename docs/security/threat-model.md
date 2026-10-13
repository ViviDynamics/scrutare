# Scrutare App threat model

**Status**: current as of 2026-10-13 | **Issue**: [#18](https://github.com/ViviDynamics/scrutare/issues/18)

Written to the discipline of [Conductor's threat model](https://github.com/ViviDynamics/conductor/blob/main/docs/security/threat-model.md):
each boundary states what defends it and what remains exposed. Read this before
exposing the service. The App handles hostile PR text, model credentials and
GitHub review-write tokens. Its decisions never merge code.

## Trust boundaries

| Boundary | Mitigation | Residual risk |
| --- | --- | --- |
| GitHub to webhook | Exact-byte HMAC-SHA256 before parsing, bounded body, explicit installation and repository allowlist, admission keyed on the signed repository, pull request and head rather than the unsigned delivery ID, one webhook review per head | A stolen webhook secret can submit work within the allowlist. A captured signed body can be replayed but only returns the job already admitted for that head. Genuine pushes spaced apart each start a review and spend the configured model budgets |
| Missed deliveries | GitHub does not redeliver on its own. The service asks GitHub to redeliver recent failed reviewable deliveries, which return through signed admission | Only the 100 most recent delivery records and the last 24 hours are examined, three requests per delivery. A longer outage loses events until someone redelivers or requests a review |
| Performer to API | Separate bearer token, allowlisted targets, required idempotency key, no credential or configuration overrides in the request | Every token holder can spend review budgets and read all job records. There is no per-caller isolation, and performer requests are never coalesced |
| PR text to nare | Read-only filtered artifact root, code-verified anchors, code-derived verdict, shared token ledger | Prompt injection can bias observations and chair selections. Empty or partial valid findings can approve. Read the evidence and coverage |
| Repo config to model transport | Configuration from the base repository's default branch only, HTTPS model-origin allowlist over the default rail and every override, each vendor's own origin accepted only for that vendor's credential, validation before any model call | Repository maintainers control prompts, strategy, budgets, context paths, escalation reviewers and models at permitted origins. An approved gateway origin receives whichever provider credential the configuration names for it. A compromised model endpoint sees source and that credential |
| App to GitHub | Short RSA JWT, fresh installation token narrowed to one repository and the read and review-write permissions it requests, API redirects refused, remote error bodies never surfaced | The service holds the App key. Its compromise can mint tokens for every installation of the App. Keep App permissions and installations narrow |
| Service to CLI and nare | Per-job environment holding only the installation token and provider credentials; the engine's nare environment allowlist strips GitHub credentials; job deadline inside the token lifetime, after which the CLI, its process group and every descendant session are killed | Killed sessions are not reaped until the container exits. The CLI process can write reviews and request reviewers on its repository. The model transport holds its provider credential. OS process compromise defeats these boundaries |
| Worker to Kubernetes | Non-root restricted pod, no API token or RBAC, no Docker socket, read-only root filesystem, bounded CPU and memory | Garden's CNI does not enforce NetworkPolicy, so any pod in the cluster can reach the Service and no egress isolation is claimed. The API bearer token is the only defense on `/api/*` inside the cluster |
| Crash to durable delivery | Exclusive single-worker lock, SQLite transactions, permanent artifacts, no automatic replay of interrupted or failed jobs, and a `delivery` field on every unsuccessful job read from the posting journal | A crash, deadline or interruption between posting and the terminal record is uncertain delivery. A caller that resubmits on `uncertain` or `confirmed` can post a second review. A journal that cannot be read is reported as uncertain |

## Exposure

The listener defaults to loopback and speaks plain HTTP. Each connection has its
own thread, at most 32 are served at once, and a whole request must finish
within ten seconds, so a client that trickles headers or a body cannot hold the
listener. Liveness is answered by a separate listener that the Service does not
expose, so intake load cannot make the kubelet restart the pod and interrupt a
running review. A client that keeps all 32 connections occupied can still deny
intake for as long as it continues, and missed webhooks then depend on
redelivery. TLS and rate limiting belong to the route in front. On garden that
is the Cloudflare tunnel, which should publish only `/webhook`. A tunnel rule
for the whole hostname would also publish `/api/*` and `/healthz`; the bearer
token would then be the only protection for the performer API on the internet.
`/healthz` exposes only liveness. All job data requires performer
authentication. Logging omits request paths, bodies and headers.

## Identity and delivery

Reviews are written with an installation token, so GitHub attributes them to the
App's bot account. That token cannot read its own login, which the engine needs
only to reconcile an uncertain review POST. Under the App that reconciliation
fails closed: the job ends `interrupted` with `delivery: uncertain`, without a
second POST and without a confirmed result. A delivered but unconfirmed review
is possible and is never duplicated automatically.

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

- Signature, allowlist, signed-content idempotency, bounded queue, coalescing,
  release and restart behavior: `tests/test_app.py`.
- HTTP authentication and refusals, job privacy, slow clients, the connection
  cap, the separate health listener, clean shutdown and the deadline kill of
  descendant sessions: `tests/test_app_http.py`.
- Token narrowing, default-branch configuration, provider-bound model origins,
  credential isolation, delivery evidence for every exit code, the reviewed-head
  guard and redelivery selection: `tests/test_app_worker.py`.
- The installed wheel driving the real nare CLI offline, with retained evidence
  and replay: `tests/test_installed_app.py`.
- Manifest restrictions and secret references: `tests/test_app_deploy.py`.

## What this document does not cover

Chat replies, automatic repository fixes, merges and cluster-management APIs are
outside this service. Cloudflare, Argo CD and cluster configuration are verified
where they live, not here. Live deployment and App identity must be demonstrated
with actual installation events, not inferred from mocked or offline tests.
