# Scrutare App threat model

Reviewed 2026-10-07 against [Conductor's threat model](https://github.com/ViviDynamics/conductor/blob/main/docs/security/threat-model.md).
Read this before exposing the service. The App handles hostile PR text, model
credentials and GitHub review-write tokens; its decisions never merge code.

| Boundary | Mitigation | Residual risk |
| --- | --- | --- |
| GitHub to webhook | Exact-byte HMAC-SHA256, bounded body, explicit installation/repository allowlist, durable delivery ID | Stolen webhook secret can submit work within the allowlist; genuine high-volume pushes can consume configured model budgets |
| Performer to API | Separate bearer token, allowlisted targets, required idempotency key, no credential/body config overrides | Every token holder can spend review budgets and inspect all jobs; there is no per-caller role isolation |
| PR text to nare | Read-only filtered artifact root, code-verified anchors, code-derived verdict, shared token ledger | Prompt injection can bias observations and chair selections; empty or partial valid findings can approve. Read the evidence and coverage |
| Repo config to model transport | Default-branch config, HTTPS model-origin allowlist | Trusted repository maintainers control prompts, budgets and models at permitted origins; a compromised model endpoint sees source and provider credentials |
| App to GitHub | Short RSA JWT, fresh installation token narrowed to one repo, API redirects refused, safe error messages | The parent service holds the App key; compromise can mint tokens for the App's installations. Narrow App permissions and installations |
| Parent to CLI and nare | Isolated per-job CLI environment; only installation token and selected provider credentials, nare's own credential allowlist strips GitHub credentials | The CLI process can write reviews for its repository; the model transport holds its provider credential. OS process compromise defeats these boundaries |
| Worker to Kubernetes | Non-root restricted pod, no API token or RBAC, no Docker socket, readonly root filesystem, bounded CPU/memory | Container/runtime and CNI enforcement remain external; no egress isolation is claimed. Apply and measure cluster network policy if required |
| Crash to durable delivery | Exclusive single-worker lock, SQLite transactions, permanent artifacts, no automatic replay of running/failed jobs | Crash between posting and durable terminal recording is uncertain delivery; operators must inspect receipts before resubmission |

The listener defaults to loopback. Remote deployments must terminate TLS and
protect the ingress-to-pod path. The stdlib HTTP listener is serialized and
uses ten-second socket timeouts; rate limiting and slow-client protection
belong to the ingress. `/healthz` exposes only liveness, while all job data
requires performer authentication. Logging omits request bodies and headers.

App and webhook/API credentials remain outside model input and repository
configuration. Secrets are mounted/injected by the operator and never created
from PR instructions. Evidence is unsigned local data, not proof of remote
delivery or tamper resistance. Model token limits are reported after turns and
can overshoot; the queue admission cap is not a hard financial ceiling.

Chat replies, automatic repository fixes, merges, and cluster-management APIs
are outside this service. Live deployment and App identity must be demonstrated
with actual installation events, not inferred from mocked or offline tests.
