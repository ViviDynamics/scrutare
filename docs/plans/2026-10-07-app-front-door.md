# GitHub App front door

Issue #18

## Scope
In: authenticated webhook and performer API, scoped installation credentials,
durable serialized review jobs, repository configuration, the existing CLI engine,
and garden Kubernetes deployment resources and threat model.
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
- [ ] Signed event and API validation with refusal tests.
- [ ] Durable idempotent bounded queue with interrupted-job evidence.
- [ ] App JWT and narrowed installation tokens, repository config, CLI worker.
- [ ] HTTP entry point, Kubernetes resources, threat model and operator guide.
- [ ] Full two-lane preflight, review, CI and source PR. Live acceptance remains
  explicit until an installed App actually reviews a PR on open and push.
