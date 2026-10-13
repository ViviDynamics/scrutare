# GitHub App front door

Issue #18

## Scope
In: authenticated webhook and performer API, scoped installation credentials,
durable serialized review jobs, repository configuration, the existing CLI engine,
and cluster Kubernetes deployment resources and threat model.
Out: App registration and live rollout until an operator supplies the App identity,
installation allowlist, secret references, and public webhook routing. Those facts
cannot be inferred from a development GitHub token.

## Assumptions
- App requests use a repository/installation allowlist controlled by the operator.
- Repository configuration comes from the base repository's default branch, not
  an untrusted PR head. Model endpoint origins need operator approval.
- One durable worker serializes reviews. Interrupted delivery is never retried
  automatically because GitHub may already have received the review.
- The service calls the installed CLI with an isolated installation-token
  environment. Models never receive App, GitHub, API, or webhook credentials.

## Tasks
- [x] Signed event and API validation with refusal tests.
- [x] Durable idempotent bounded queue with interrupted-job evidence.
- [x] App JWT and narrowed installation tokens, repository config, CLI worker.
- [x] HTTP entry point, Kubernetes resources, threat model and operator guide.
- [x] Resumed 2026-10-13 on the current engine: CLI exit 130 and signal deaths are
  uncertain interruptions, success requires the byte-identical installed result,
  a job deadline fits the installation token lifetime, and current-surface
  configuration reaches the CLI only with every rail's origin approved.
- [x] Queued pushes covered by a started review are superseded, not re-reviewed.
- [x] Cluster manifests follow the infrastructure repository's plain Argo CD
  directory conventions; the image smoke check starts the deployed console.
- [x] Independent review round 1: threaded bounded intake with a request deadline
  and separate health listener; `delivery` evidence from the posting journal;
  newest-push coalescing and a reviewed-head guard; signed-content admission;
  GitHub redelivery of missed webhooks; provider-bound, validated model origins.
- [x] Independent review round 2: reviewed heads recorded for every job that may
  have delivered; one queued push per pull request at admission; orphaned leases
  recovered from their runs; event-driven listener tests.
- [x] Full two-lane preflight, review, CI and source PR.
- [ ] Out of this plan's source scope: live acceptance stays open on the issue
  until an installed App actually reviews a PR on open and push.
