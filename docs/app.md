# GitHub App service

The App service admits signed `pull_request` events on `opened`, `synchronize`,
`reopened`, and `ready_for_review`, and signed `pull_request_review_comment`
events on `created` and `edited` that [reply on a review thread](#replies-on-review-threads).
Its worker calls the same installed
`scrutare review` CLI used by standalone callers, so the `panel`,
[iterative](iterative-review.md) and [debate](debate.md) strategies and every
other [configuration](config.md) field behave as they do there. Reviews are
posted with a repository-scoped installation token, so their author is the
installed App's bot account. The service retains the CLI's captures, sessions,
findings, verdict, posting journal, result and artifact manifest under its
persistent data directory.

This repository supplies the service and reference cluster manifests. Live
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
read permissions, with the Pull request webhook subscription. Answering replies
additionally needs the **Pull request review comment** subscription and no
further permission. Each job obtains
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
| `unchanged` | A job that posted no review because none was needed: replies are not enabled for the repository, or the pull request already shows the current head and verdict. Threaded responses may still have been delivered; see `result.replies`. |
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
exits 1 both for a refusal and for a failure after delivery. `delivery` also
covers threaded responses, read from the run's `reply-posting.json`: an uncertain
response POST makes the job `uncertain`, and a confirmed response with no review
makes it `confirmed`.

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
reviewable `pull_request` and reply `pull_request_review_comment` events from its
allowlisted installations that failed
in transport or with a 5xx status. Redelivered events pass the same signed
admission. The bounds are the 100 most recent delivery records, the last 24
hours, and three accepted requests per delivery. A failed check is reported on
stderr and tried again at the next interval. A longer outage needs a manual
redelivery from the App's Advanced settings or a performer API request.

## Replies on review threads

A person who disagrees with a finding replies in its thread. With the **Pull
request review comment** event subscribed, the service admits that reply and
queues a reassessment at the current head, without a push. The repository opts
in with `replies.enabled` in its default-branch `scrutare.yaml`; see
[replies on Scrutare threads](iterative-review.md#replies-on-scrutare-threads-opt-in)
for the trust policy, dispositions and the threaded response. Without the
subscription the feature still works on the next push, because every review
reads the replies it captures.

Admission authenticates the exact body first, like every event. It then ignores
what can never contest: a comment that starts a thread, a deleted comment, a
closed pull request, and any comment whose author or sender GitHub types as
anything but `User`, which covers Scrutare's own responses and other bots.
Admission has no network access and does not know the repository's policy, so
the engine decides trust from what it captures.

- **Idempotency.** A reply is keyed on signed content: repository, pull request,
  comment ID and the SHA-256 of its text. A redelivery, a replay under a new
  delivery ID and an edit that leaves the text unchanged return the job already
  admitted. An edit that changes the text is admitted once more.
- **Coalescing.** Each pull request still holds at most one queued webhook job.
  A reply that arrives while a job of its pull request is queued returns that
  job, which will capture the reply. A reply never displaces a queued push. A
  new push supersedes a queued reply job. Several replies before a job runs are
  one job, one reassessment and one response per thread.
- **Worker.** A reply job skips the reviewed-head guard, since the head is
  unchanged by design, and runs `scrutare review --replies-only`. It posts a
  review only when the pull request does not already show the current head and
  verdict; otherwise it ends `unchanged`. When the repository has not enabled
  replies, the job ends `unchanged` before any model origin check or CLI run.
  A push job that finds its head already reviewed may be carrying coalesced
  replies, so on a repository with replies enabled it runs the same
  replies-only review instead of ending `superseded`. Without the setting the
  guard behaves as before.
- **Budget.** Reassessments spend the same `rounds.max` as pushes. Once the
  bound is reached no session starts and the review escalates.
- **Redelivery.** Missed `pull_request_review_comment` deliveries are requested
  again under the same bounds as pushes.

A response whose POST outcome is unknown is never sent again; a later capture
that shows it records it as delivered. Pending responses go out with the next
job for that pull request.

## Listener

The listener serves each connection on its own thread, with at most 32
connections and a ten-second deadline for a whole request, however slowly it
arrives. Requests need one `Content-Length` of at most 1 MiB; chunked bodies are
refused. `/healthz` is also served by a separate listener on
`SCRUTARE_APP_HEALTH_PORT`, which probes should use so intake load cannot fail
them. The listener has no TLS or rate limiting of its own. The service writes
only a startup line with its ports and credential-free failure notices to
stderr; it logs no requests.

## Cluster rollout

`deploy/cluster` holds plain manifests in the shape cluster's Argo CD directory
applications use: a `scrutare` namespace, one non-root `Recreate` Deployment, a
ClusterIP Service on port 80 and a retained 10Gi `ceph-rbd` claim. Probes use
the health port, which the Service does not expose. The namespace
and claim carry `Prune=false,Delete=false`. The pod has no Kubernetes token,
RBAC, host mounts or Docker socket and satisfies the `restricted` pod security
level. Never scale the worker above one replica.

Nothing here applies itself. Rolling out is an infrastructure change:

1. Copy `deploy/cluster/*.yaml` to `clusters/production/namespaces/scrutare/` in the
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

Replies need one change to the GitHub App's settings, which only its owner can
make: under **Permissions & events**, **Subscribe to events**, tick **Pull
request review comment**. No permission changes: the event is available with the
Pull requests permission the App already holds, replies are written with Pull
requests write, and the collaborator permission lookup needs only Metadata read.
Live acceptance for replies then requires, on a repository with
`replies.enabled`: a maintainer's reply with no push producing exactly one
threaded response and no duplicate inline threads; a redelivery from the App's
Advanced settings producing nothing further; a reply from an account without
write access ending `unchanged`; and confirmation that
`GET /repos/{owner}/{repo}/collaborators/{login}/permission` succeeds with the
narrowed installation token. If that lookup is refused, every account is
untrusted and no reply triggers a reassessment.

### Optional whole-review retry

`SCRUTARE_APP_WHOLE_REVIEW_RETRY=true` enables one whole-review retry before
posting. It defaults to `false`; no live setting is changed by installing this
release. The worker invokes the review CLI with `--whole-review-retry` and an
immutable `--expected-head`. The CLI retains each failed attempt and clones the
original capture/configuration into a separate run directory. Iterative history
is frozen too; an intervening history writer refuses recovery.

The retry uses the same model, grants, caps and session timeouts, within the
unchanged overall job deadline. Failed, partial or unaccounted attempts supply no
posted answer. A failed original with incomplete accounting fails closed, since
the App has no independent provider-ledger reconciliation facility. Two failed
attempts finish the job as failed, with no answer. Confirmed or uncertain posting
journals prevent retry. A moved head supersedes the job at capture, before
recovery, before posting, and at the posting transport's final metadata check
(including after a posting backoff). GitHub does not provide an atomic
compare-head-and-post API; the posted review remains bound to its captured SHA.

The accepted result's `recovery` object and retained `review-recovery.json` record
attempt statuses, separate per-attempt usage and cumulative usage. The ordinary
path and its result artifacts are unchanged when the setting is off.
