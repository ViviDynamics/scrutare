# GitHub App service

The App service admits signed `pull_request` events on `opened`, `synchronize`,
`reopened`, and `ready_for_review`. Its worker calls the same installed
`scrutare review` CLI used by standalone callers, so the `panel`,
[iterative](iterative-review.md) and [debate](debate.md) strategies and every
other [configuration](config.md) field behave as they do there. Reviews are
posted with a repository-scoped installation token, so their author is the
installed App's bot account. The service retains the CLI's captures, sessions,
findings, verdict, posting journal, result and artifact manifest under its
persistent data directory.

This repository supplies the service and reference garden manifests. Live
acceptance for issue #18 additionally requires a published image, the operator's
secrets and repository allowlist, a public webhook route, and observed reviews
on PR open and push. The source tests do not establish live deployment or App
identity.

## Configuration

Run `scrutare-app` or `python -m scrutare.interfaces.app_server` on Linux with the
separately installed nare runtime and gh already available. The published
container image contains all three. Every setting below is operator supplied;
no credentials are accepted through review request bodies.

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
| `SCRUTARE_APP_JOB_TIMEOUT` | Seconds one review may run, 1 to 3300, default 3300 |
| `SCRUTARE_APP_NARE_EXECUTABLE` | Installed nare executable, default `nare` |
| `SCRUTARE_APP_HOST`, `SCRUTARE_APP_PORT` | Default `127.0.0.1`, 8080 |

Startup fails when the identity, either secret, the allowlist or the model
origin list is missing or malformed, including an empty allowlist.

Provide the model rail's existing `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the
service environment. Model origins must include every origin the repository
configuration selects, for the default rail and each override:
`https://api.anthropic.com`, `https://api.openai.com`, or the approved gateway
origin, with its port if it has one. An HTTP origin is always refused.

The worker reads `scrutare.yaml` from the base repository's **default branch**,
ignoring changes to that file on an unmerged PR, and passes those exact bytes to
the CLI. A repository without that file, or with an invalid one, fails the job
before any model call. Three current settings behave specifically here:

- `context.enabled` reads commit, tree and blob objects through the same
  installation token. A pull request from a private fork the App cannot read
  fails capture, as the [repository context](repository-context.md) contract
  requires.
- `analysis.enabled` has no capture to consume. The service never passes
  `--analysis-capture`, so [static analysis](static-analysis.md) evidence is
  recorded as unavailable.
- `strategy: iterative` keeps one history per repository and pull request across
  webhook and API jobs. The engine refuses a history whose configuration has
  changed, so editing an iterative repository's `scrutare.yaml` on its default
  branch fails later jobs for pull requests that already have history. Restore
  the configuration or archive that history before requesting another review.

The App requires Contents read, Issues read, Pull requests write, and Metadata
read permissions, with the Pull request webhook subscription. Each job obtains
a fresh installation token narrowed to exactly its repository and these
permissions. That token lasts one hour, so the worker interrupts a review at
`SCRUTARE_APP_JOB_TIMEOUT` and records the job as `interrupted`.

Installation tokens cannot read the token owner's login. Ordinary posting does
not need it. Recovery after an uncertain review POST does, so under the App an
uncertain delivery always fails closed and is never resent. Inspect
`posting.json` in the retained run, as the [posting](posting.md) contract
describes.

## Jobs

| Status | Meaning |
| --- | --- |
| `queued`, `running` | Admitted; one job runs at a time. |
| `posted` | The CLI exited 0 and its stdout is byte-identical to the installed `result.json`. Approval, changes requested and escalation are all posted results. |
| `failed` | Configuration, model origin, token, capture, execution or posting was refused or failed (CLI exit 1 or 2). A failure after confirmed delivery says so in the job's `error` or `stderr.log`. |
| `interrupted` | The CLI was interrupted (exit 130 or a signal), reached the job deadline, or was running when the service restarted. Delivery may have occurred. |
| `superseded` | A queued webhook job for the same pull request was already covered by a job that started later than its push. Its `result` names that job. |

A job that starts captures the pull request head current at that moment. Every
webhook job still queued for the same pull request is therefore covered by it
and becomes `superseded` instead of posting a duplicate review of the same
head. A push admitted after the job started is reviewed separately. Performer
API jobs are never merged.

Failed and interrupted jobs are never retried automatically, because the review
may already have been delivered. Each job has a permanent private working
directory holding the fetched configuration and the CLI's `stdout.json` and
`stderr.log`. Each repository has a stable working directory, named by the
repository's SHA-256, whose `.scrutare/runs` holds the runs and iterative
history. A process restart marks in-flight jobs `interrupted`. Monitor and
archive completed jobs; the queue bounds pending work, not lifetime disk usage.

Jobs run sequentially under one process-wide filesystem lock. State is SQLite on
local or block-backed storage; NFS is unsupported.

## Performer API

`POST /api/reviews` with `Authorization: Bearer <api-token>` and JSON:

```json
{"repository":"owner/repo","number":12,"installation":12345,"request_id":"performer-task-42"}
```

The response is HTTP 202 with a durable job ID. Repeating the same request ID
and target returns that job; changing its target returns 409. The trusted
conductor/coordinare reviewer performer polls `GET /api/jobs/<id>` with the same
credential until `posted`, `failed` or `interrupted`. A posted job's `result`
is the CLI result document. An escalated verdict also includes the human
reviewer request receipt. This HTTP adapter exposes no repository mutation or
arbitrary command execution API.

GitHub uses `POST /webhook` with its exact-body HMAC-SHA256 signature and delivery
ID. Signed but unlisted installations or repositories receive 403. Irrelevant
events are acknowledged and ignored. The queue returns 503 at its admission cap,
allowing the caller to retry. GitHub redeliveries reuse the delivery ID and
return the original job.

## Garden rollout

`deploy/garden` holds plain manifests in the shape garden's Argo CD directory
applications use: a `scrutare` namespace, one non-root `Recreate` Deployment, a
ClusterIP Service on port 80 and a retained 10Gi `ceph-rbd` claim. The namespace
and claim carry `Prune=false,Delete=false`. The pod has no Kubernetes token,
RBAC, host mounts or Docker socket and satisfies the `restricted` pod security
level. Never scale the worker above one replica.

Nothing here applies itself. Rolling out is an infrastructure change:

1. Copy `deploy/garden/*.yaml` to `clusters/garden/namespaces/scrutare/` in the
   infrastructure repository. Set the image to the first published release tag
   that contains the App, list the confirmed repositories in
   `SCRUTARE_APP_INSTALLATIONS`, and list the model origins. The placeholders
   fail closed: the image tag does not exist and an empty allowlist refuses to
   start.
2. Add an Argo CD `Application` for that directory in the `infrastructure`
   project, with the `scrutare` namespace among that project's destinations.
3. Create the secrets out of band. No secret values belong in Git.

   ```sh
   kubectl -n scrutare create secret generic scrutare \
     --from-literal=SCRUTARE_APP_WEBHOOK_SECRET='...' \
     --from-literal=SCRUTARE_APP_API_TOKEN='...' \
     --from-literal=OPENAI_API_KEY='...'
   kubectl -n scrutare create secret generic scrutare-github-app \
     --from-file=private-key.pem=/path/to/vivi-scrutare.private-key.pem
   ```

   Supply `ANTHROPIC_API_KEY` instead of, or as well as, `OPENAI_API_KEY` to
   match the model rails. The image is public, so no pull secret is needed.
4. Publish only the webhook. Add a Cloudflare tunnel rule for the chosen
   hostname with `path: ^/webhook$` and service
   `http://scrutare.scrutare.svc.cluster.local`, route its DNS record, and set
   that URL and the webhook secret on the GitHub App. Performers reach
   `/api/reviews` and `/api/jobs/<id>` through the cluster Service and must not
   be given a public route.

The listener has no TLS, rate limiting or slow-client protection of its own.
Read the [threat model](security/threat-model.md) before exposing it.

Acceptance requires opening a fixture PR and pushing a fix through the installed
App, verifying the review author's App login, the captured head SHAs and saved
artifacts, then calling the performer API for the same engine result. Keep #18
open until that evidence and the rollout are recorded.
