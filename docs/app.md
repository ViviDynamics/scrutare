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
| `SCRUTARE_APP_RECONCILE_SECONDS` | Interval for requesting redelivery of missed webhooks, default 600; 0 disables |
| `SCRUTARE_APP_NARE_EXECUTABLE` | Installed nare executable, default `nare` |
| `SCRUTARE_APP_HOST`, `SCRUTARE_APP_PORT` | Default `127.0.0.1`, 8080 |
| `SCRUTARE_APP_HEALTH_PORT` | Separate liveness listener on the same host, default 8081 |

Startup fails when the identity, either secret, the allowlist or the model
origin list is missing or malformed. An empty allowlist and an empty origin list
are both refused. Each origin is exactly `https://host` or `https://host:port`
in lower case.

Provide the model rail's existing `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the
service environment. Model origins must include every origin the repository
configuration selects, for the default rail and each override:
`https://api.anthropic.com`, `https://api.openai.com`, or the approved gateway
origin, with its port if it has one. An HTTP origin is always refused. A
vendor's own origin is accepted only for that vendor's provider, so the OpenAI
credential is never sent to `https://api.anthropic.com` or the reverse. A
gateway origin receives the credential of whichever provider the configuration
names for it.

The worker reads `scrutare.yaml` from the base repository's **default branch**,
ignoring changes to that file on an unmerged PR, and passes those exact bytes to
the CLI. A repository without that file, or with an invalid one, fails the job
before any model call and names the missing file or invalid field. Three current settings behave specifically here:

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
| `failed` | Configuration, model origin, token, capture, execution or posting was refused or failed, and the posting journal shows the review was either never sent or was confirmed. |
| `interrupted` | The CLI was interrupted (exit 130 or a signal), reached the job deadline, was running when the service restarted, or ended with a review POST whose outcome is unknown. |
| `superseded` | A webhook job that needed no review of its own. Its `result` names the job that covers it. |

Every `failed` and `interrupted` result carries `delivery`, read from the run's
posting journal rather than inferred from the exit code:

- `not_sent`: no review was sent. Requesting another review is safe.
- `confirmed`: the review was delivered and a later step failed. Do not resubmit.
- `uncertain`: a POST may have reached GitHub. Inspect `posting.json` in the
  retained run before resubmitting.

Performers must decide from `delivery`, never from the status alone. The CLI
exits 1 both for a refusal and for a failure after delivery.

A review captures the pull request head current when it runs, so two rules keep
one head from being reviewed twice by webhook jobs. At admission, a new push
supersedes the queued webhook job of the same pull request, so each pull request
holds at most one queued push and one author cannot fill the queue. Before a
webhook job runs, the worker reads the current head and supersedes the job when
an earlier job delivered, or may have delivered, a review of exactly that head;
that includes `failed` jobs with `delivery: confirmed` and `interrupted` jobs
with `delivery: uncertain`. A head that was superseded before any review is
admitted again if it becomes the head once more. Performer API jobs are never
merged or skipped.

Failed and interrupted jobs are never retried automatically. Each job has a
permanent private working directory holding the fetched configuration and the
CLI's `stdout.json` and `stderr.log`. Each repository has a stable working
directory, named by the repository's SHA-256, whose `.scrutare/runs` holds the
runs and iterative history. A job left running by a restart or an unexpected
worker error ends `interrupted`, with `delivery` read from the runs it created;
one that had not started its review reports `not_sent`. A shutdown that arrives
as a claimed job is about to start its review returns it to the queue. Monitor and archive completed jobs. The queue bounds
pending work, not the job table or lifetime disk usage.

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

## Webhook

GitHub uses `POST /webhook` with its exact-body HMAC-SHA256 signature. Signed but
unlisted installations or repositories receive 403. Irrelevant events are
acknowledged and ignored. The response carries only the job's `status` and
`id`; job records need the performer credential. The delivery ID header is not covered by the
signature, so admission is keyed on signed content: the repository, pull request
number and announced head SHA. A redelivery, a replay under a new delivery ID,
and a later `reopened` or `ready_for_review` event at the same head all return
the job already admitted for that head. A head whose job failed is therefore
reviewed again only by a new push or a performer API request.

GitHub does not redeliver a failed webhook on its own. A delivery that arrives
while the service is down, restarting, unreachable or at its admission cap (503)
would otherwise be lost. At startup and every `SCRUTARE_APP_RECONCILE_SECONDS`
the service lists the App's recent deliveries and asks GitHub to redeliver
reviewable `pull_request` events from its allowlisted installations that failed
in transport or with a 5xx status. Redelivered events pass the same signed
admission. The bounds are the 100 most recent delivery records, the last 24
hours, and three accepted requests per delivery. A failed check is reported on
stderr and tried again at the next interval. A longer outage needs a manual
redelivery from the App's Advanced settings or a performer API request.

## Listener

The listener serves each connection on its own thread, with at most 32
connections and a ten-second deadline for a whole request, however slowly it
arrives. Requests need one `Content-Length` of at most 1 MiB; chunked bodies are
refused. `/healthz` is also served by a separate listener on
`SCRUTARE_APP_HEALTH_PORT`, which probes should use so intake load cannot fail
them. The listener has no TLS or rate limiting of its own. The service writes
only a startup line with its ports and credential-free failure notices to
stderr; it logs no requests.

## Garden rollout

`deploy/garden` holds plain manifests in the shape garden's Argo CD directory
applications use: a `scrutare` namespace, one non-root `Recreate` Deployment, a
ClusterIP Service on port 80 and a retained 10Gi `ceph-rbd` claim. Probes use
the health port, which the Service does not expose. The namespace
and claim carry `Prune=false,Delete=false`. The pod has no Kubernetes token,
RBAC, host mounts or Docker socket and satisfies the `restricted` pod security
level. Never scale the worker above one replica.

Nothing here applies itself. Rolling out is an infrastructure change:

1. Copy `deploy/garden/*.yaml` to `clusters/garden/namespaces/scrutare/` in the
   infrastructure repository. Set the image to the first published release tag
   that contains the App, list the confirmed repositories in
   `SCRUTARE_APP_INSTALLATIONS`, and list the model origins. The placeholders
   fail closed: the image tag does not exist, and an empty allowlist or origin
   list refuses to start.
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

Confirm during live acceptance that the tunnel forwards GitHub's deliveries with
a `Content-Length`; a route that re-frames them as chunked would be refused with
400. Read the [threat model](security/threat-model.md) before exposing the
service.

Acceptance requires opening a fixture PR and pushing a fix through the installed
App, verifying the review author's App login, the captured head SHAs and saved
artifacts, then calling the performer API for the same engine result. Keep #18
open until that evidence and the rollout are recorded.
