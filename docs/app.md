# GitHub App service

The App service admits signed `pull_request` events on `opened`, `synchronize`,
`reopened`, and `ready_for_review`. Its worker calls the same installed
`scrutare review` CLI used by standalone callers. Reviews are posted by a
repository-scoped installation token, so the author is the installed GitHub App.
The service retains the CLI's captures, sessions, findings, verdict, posting
journal, result and artifact manifest under its persistent data directory.

This repository supplies the service and garden manifests. Live acceptance for
issue #18 additionally requires an actual registered and installed Scrutare App,
published candidate image, secrets, TLS webhook routing and observed reviews on
PR open and push. The source tests do not establish live deployment or App identity.

## Configuration

Run `scrutare-app` or `python -m scrutare.interfaces.app_server` on Linux with the
separately installed nare runtime and gh already available. Every setting below
is operator supplied; no credentials are accepted through review request bodies.

| Variable | Value |
| --- | --- |
| `SCRUTARE_APP_ID` | Registered GitHub App numeric ID |
| `SCRUTARE_APP_PRIVATE_KEY` | Path to the App RSA PEM key |
| `SCRUTARE_APP_WEBHOOK_SECRET` | Secret configured on the GitHub webhook |
| `SCRUTARE_APP_API_TOKEN` | Separate shared secret for trusted performers |
| `SCRUTARE_APP_INSTALLATIONS` | JSON object, e.g. `{"owner/repo":12345}` |
| `SCRUTARE_APP_MODEL_ORIGINS` | JSON array of permitted HTTPS model origins |
| `SCRUTARE_APP_DATA_DIR` | Persistent private directory, default `/data` |
| `SCRUTARE_APP_MAX_PENDING` | Running + queued admission cap, default 100 |
| `SCRUTARE_APP_NARE_EXECUTABLE` | Installed nare executable, default `nare` |
| `SCRUTARE_APP_HOST`, `SCRUTARE_APP_PORT` | Default `127.0.0.1`, 8080 |

Provide the model rail's existing `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the
service environment. Model origins must include the selected provider origin
(`https://api.anthropic.com`, `https://api.openai.com`, or the approved proxy
origin, with port if applicable). The worker reads `scrutare.yaml` from the base
repository's **default branch**, ignoring changes to that file on an unmerged PR.
Configuration controls personas, strategy, budgets, filters and posting; it must
name a model and an operator-approved HTTPS origin. Missing config fails the job.

The App requires Contents read, Issues read, Pull requests write, and Metadata
read permissions, with the Pull request webhook subscription. Each job obtains
a fresh installation token narrowed to exactly its repository and these scopes.
Keep configured budgets low enough to finish within the token's one-hour lifetime.

## Performer API

`POST /api/reviews` with `Authorization: Bearer <api-token>` and JSON:

```json
{"repository":"owner/repo","number":12,"installation":12345,"request_id":"performer-task-42"}
```

The response is HTTP 202 with a durable job ID. Repeating the same request ID
and target returns that job; changing its target returns 409. The trusted
conductor/coordinare reviewer performer polls `GET /api/jobs/<id>` with the same
credential until `posted`, `failed`, or `interrupted`. A posted job's `result`
is the CLI result document. A changes-requested verdict is a successful review.
An escalated verdict also includes human review request evidence. This HTTP
adapter exposes no repository mutation or arbitrary command execution API.

GitHub uses `POST /webhook` with its exact-body HMAC-SHA256 signature and delivery
ID. Signed but unlisted installations/repositories receive 403. Irrelevant events
are acknowledged and ignored. The queue returns 503 at its admission cap, allowing
the caller to retry. GitHub redeliveries reuse the delivery ID. Distinct IDs,
including separate API requests for the same PR, are separate reviews.

Jobs run sequentially under one process-wide filesystem lock. State is SQLite on
local/block-backed storage; NFS is unsupported. Each job has a permanent private
working directory. A process restart marks in-flight jobs `interrupted` and does
not replay them: delivery may already have happened. Inspect saved posting
evidence before submitting a replacement request. Failed delivery likewise does
not automatically retry the whole review. Monitor and archive completed jobs;
the queue bounds pending work, not lifetime disk usage. Each repository has a
stable working directory (named by the repository's SHA-256) so iterative review
shares its run root and cross-push history across webhook and API jobs. Request
configuration and stdout/stderr evidence remain in private per-job directories.

## Garden rollout

`deploy/garden` contains a restricted `scrutare` namespace, one non-root
Deployment, ClusterIP Service and a retained `ceph-rbd` block-backed PVC. The
pod has no Kubernetes token, RBAC, host mounts or Docker socket. The deployment
uses `Recreate`; never scale the worker above one replica. The PVC is review
history and must survive application updates. Garden's `ceph-rbd` storage class
was checked through the cluster API on 2026-10-07.

Create secrets out-of-band named `scrutare-github-app` (key `private-key.pem`)
and `scrutare-app` (environment variables above and model credentials). No secret
values belong in Git. Replace the explicit `app-build-required` image marker
with a published candidate **digest** built from this source; released 2026.10.2
does not contain the App. Render with `kubectl kustomize deploy/garden`, review
the [threat model](security/threat-model.md), then apply through garden's GitOps
process. Provide a TLS ingress/tunnel route for `/webhook` with request size and
rate limits; restrict `/api/*` to the trusted performer network. ClusterIP and
port-forwarding alone cannot receive GitHub's public webhook deliveries.

Acceptance requires opening a fixture PR and pushing a fix through the installed
App, verifying the review author's App login, the captured head SHAs and saved
artifacts, then calling the performer API for the same engine result. Keep #18
open until that evidence and the rollout are recorded.
