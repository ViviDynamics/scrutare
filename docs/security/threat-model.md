# Scrutare App threat model

**Status**: current as of 2026-10-13 | **Issues**: [#18](https://github.com/ViviDynamics/scrutare/issues/18), [#69](https://github.com/ViviDynamics/scrutare/issues/69)

Written to the discipline of [Conductor's threat model](https://github.com/ViviDynamics/conductor/blob/main/docs/security/threat-model.md):
each boundary states what defends it and what remains exposed. Read this before
exposing the service. The App handles hostile PR text, model credentials and
GitHub review-write tokens. Its decisions never merge code.

## Trust boundaries

| Boundary | Mitigation | Residual risk |
| --- | --- | --- |
| GitHub to webhook | Exact-byte HMAC-SHA256 before parsing, bounded body, explicit installation and repository allowlist, admission keyed on the signed repository, pull request and head rather than the unsigned delivery ID, at most one queued push per pull request, and no webhook review of a head whose review was or may have been delivered. The response names only the job's status and ID | A stolen webhook secret can submit work within the allowlist. A captured signed body can be replayed but only returns the status and ID of the job already admitted for that head. Many pull requests pushed at once can still fill the queue, and the refused events then depend on redelivery. A push that lands between a job's head check and its capture can be reviewed by both that job and its own. Genuine pushes spaced apart each start a review and spend the configured model budgets |
| Reply to reassessment (opt-in `replies.enabled`) | Same exact-byte HMAC and allowlist. Admission drops new threads, deleted comments, closed pull requests and every author or sender not typed `User`, keys the job on the signed repository, pull request, comment ID and text hash, and keeps one queued webhook job per pull request; a reply never displaces a queued push. The worker starts no CLI and checks no model origin when the default-branch configuration has not enabled replies. The engine, not admission, decides trust: a verified `admin` or `write` permission read with the job's token, a failed lookup counting as untrusted. Only a thread rooted in a review whose delivery an earlier run confirmed is Scrutare's. Reply text reaches the model as bounded JSON data after an untrusted notice and is never rendered into a response. Dispositions and the verdict are derived by code from what the reassessment yields. Reassessments spend the pull request's `rounds.max`; past it no session starts and the review escalates | Anyone who can comment can queue one cheap job per pull request at a time, each costing a token mint, a configuration read and, where replies are enabled, a capture; many pull requests at once can still fill the queue. A trusted account can spend every remaining round of a pull request by replying; after the bound each further trusted reply gets an `exhausted` response and no further review or reviewer request. A reply is model input: a persuasive or injected reply from a trusted account can bias the reassessment into no longer yielding a finding, which withdraws it. An untrusted reply is still supplied, marked untrusted, to the next wave that covers its file, where it can bias that wave like any other pull request text. Each admitted reply on a repository with replies enabled leaves a retained run on disk, including replies that trigger nothing. The write-access check is one API read per account per capture, bounded to 32 accounts; a permission revoked after capture is honoured for that run. `trust: anyone` removes the permission check and lets any non-bot account spend rounds. Account type is GitHub's `type` field. A reply edited by a maintainer is still attributed to its original author. Whether the permission endpoint accepts the narrowed installation token is assumed from GitHub's documentation and must be confirmed in live acceptance |
| Threaded response delivery | `sending` is persisted in the pull request history and the run journal before each POST; a response left `sending` or `unknown` is never sent again and is confirmed only by its marker in the thread from the login of a confirmed Scrutare review; the `delivery` field covers responses; at an already reviewed head, byte-identical inline threads are not posted twice | An uncertain response that did not arrive is lost: the thread stays unanswered until someone replies again. A reply landing between capture and the reassessment is answered by a later job. A response posted by a job whose terminal record was lost is recognised only at the next capture. Under the App an uncertain review POST leaves no confirmed review, so that review's threads are not recognised and replies there are not answered. A replies-only job at an already reviewed head posts a further review when the derived verdict differs from the one last posted, so "one review per head" becomes one per head plus one per reassessment that changes the verdict. That comparison trusts the local posting journals. A run that holds the history lock when another finishes posting makes the second report failure after its review was delivered |
| Missed deliveries | GitHub does not redeliver on its own. The service asks GitHub to redeliver recent failed reviewable deliveries, which return through signed admission | Only the 100 most recent delivery records and the last 24 hours are examined, three accepted requests per delivery. GitHub's record format for transport failures and its redelivery window are assumed from its documentation and must be confirmed in live acceptance. A longer outage loses events until someone redelivers or requests a review |
| Performer to API | Separate bearer token, allowlisted targets, required idempotency key, no credential or configuration overrides in the request | Every token holder can spend review budgets and read all job records. There is no per-caller isolation, and performer requests are never coalesced |
| PR text to nare | Read-only filtered artifact root, code-verified anchors, code-derived verdict, shared token ledger | Prompt injection can bias observations and chair selections. Empty or partial valid findings can approve. Read the evidence and coverage |
| Repo config to model transport | Configuration from the base repository's default branch only, HTTPS model-origin allowlist over the default rail and every override, each vendor's own origin accepted only for that vendor's credential, validation before any model call | Repository maintainers control prompts, strategy, budgets, context paths, escalation reviewers and models at permitted origins. An approved gateway origin receives whichever provider credential the configuration names for it. A compromised model endpoint sees source and that credential |
| App to GitHub | Short RSA JWT, fresh installation token narrowed to one repository and the read and review-write permissions it requests, API redirects refused, remote error bodies never surfaced | The service holds the App key. Its compromise can mint tokens for every installation of the App. Keep App permissions and installations narrow |
| Service to CLI and nare | Per-job environment holding only the installation token and provider credentials; the engine's nare environment allowlist strips GitHub credentials; job deadline inside the token lifetime. At the deadline the CLI is interrupted so it can stop its own sessions; if it has not exited after the grace period, its process group and the descendants found at that moment are killed | A session forked during that scan, or already detached from the CLI, can survive with its provider credential until the pod stops. Killed sessions are not reaped until the container exits. The CLI process can write reviews and request reviewers on its repository. The model transport holds its provider credential. OS process compromise defeats these boundaries |
| Worker to Kubernetes | Non-root restricted pod, no API token or RBAC, no Docker socket, read-only root filesystem, bounded CPU and memory | Garden's CNI does not enforce NetworkPolicy, so any pod in the cluster can reach the Service and no egress isolation is claimed. The API bearer token is the only defense on `/api/*` inside the cluster |
| Crash to durable delivery | Exclusive single-worker lock, SQLite transactions, permanent artifacts, no automatic replay of interrupted or failed jobs, and a `delivery` field on every unsuccessful job read from the posting journal | A crash, deadline or interruption between posting and the terminal record is uncertain delivery. A caller that resubmits on `uncertain` or `confirmed` can post a second review. A journal that cannot be read is reported as uncertain |

## Exposure

The listener defaults to loopback and speaks plain HTTP. Each connection has its
own thread, at most 32 are served at once, and a whole request must finish
within ten seconds, so a client that trickles headers or a body cannot hold the
listener. Liveness is answered by a separate listener that the Service does not
expose, so load on the published intake cannot make the kubelet restart the pod
and interrupt a running review. That listener is still reachable on the pod
address: a workload inside the cluster that holds its eight connections can fail
the probes and force that restart. A client that keeps all 32 connections occupied can still deny
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

- Signature, allowlist, signed-content idempotency, bounded queue, one queued
  push per pull request, re-admission, reviewed heads and release:
  `tests/test_app.py`.
- HTTP authentication and refusals, job privacy, slow clients, the connection
  cap, the separate health listener, the data lock, clean shutdown and the kill
  of descendant sessions: `tests/test_app_http.py`.
- Token narrowing, default-branch configuration, provider-bound model origins,
  credential isolation, delivery evidence for every exit code, the reviewed-head
  guard, orphaned-lease recovery and redelivery selection:
  `tests/test_app_worker.py`.
- The installed wheel driving the real nare CLI offline, with retained evidence
  and replay: `tests/test_installed_app.py`.
- Manifest restrictions and secret references: `tests/test_app_deploy.py`.
- Reply admission, signed-content idempotency, coalescing with pushes and the cap:
  `tests/test_app.py`. The replies-only worker path, the disabled-repository
  gate, the `unchanged` contract, response delivery evidence and reply
  redelivery: `tests/test_app_worker.py`.
- The trust policy, thread recognition, bots, edits, the round bound, the bounded
  prompt and the prompt-injection case: `tests/test_iterative_replies.py`.
  Exactly-once response delivery: `tests/test_post_replies.py`. A reply without
  a push through the installed wheel and nare: `tests/test_installed_iterative.py`.

## What this document does not cover

General chat, replies outside Scrutare's own finding threads, automatic
repository fixes, merges and cluster-management APIs are outside this service.
Replies are not accepted live until the review-comment subscription is added and
the acceptance in `docs/app.md` is recorded. Cloudflare, Argo CD and cluster configuration are verified
where they live, not here. Live deployment and App identity must be demonstrated
with actual installation events, not inferred from mocked or offline tests.
