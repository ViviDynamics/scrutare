# Action release acceptance

Recorded 2026-10-06 for published
[Scrutare 2026.10.2](https://github.com/ViviDynamics/scrutare/releases/tag/2026.10.2).
The public assets and both literal hosted outcomes were independently audited.
This records observed release acceptance; issue 14 and M1 closure remain subject
to the coordinator's remaining checks.

## Released identity

[Release workflow 37457858904](https://github.com/ViviDynamics/scrutare/actions/runs/37457858904)
published the immutable release at commit
`0e48f9904a398fe33520c85d44bcf73f3f0c8723`. Both hosted runs resolved
`ViviDynamics/scrutare@2026.10.2` and checked out the trusted base at that commit.
The runtime includes nare `2026.10.4`.

| Public asset | Verified SHA-256 |
| --- | --- |
| [scrutare-2026.10.2-py3-none-any.whl](https://github.com/ViviDynamics/scrutare/releases/download/2026.10.2/scrutare-2026.10.2-py3-none-any.whl) | `1cf725bf3307538507db1047f6e9f6f5945c58b2d6db9d8ee99c5e5984f8271e` |
| `ghcr.io/vividynamics/scrutare:2026.10.2` | `sha256:3000238e9e325059c5dfca2a5f9d5c7719c6d66806dfe4d50b54b4bf190cf33e` |

Saved anonymous install/pull receipts and independent packaged-source hash
comparisons establish these public assets. The image evidence covers Linux
amd64. There is no PyPI or arm64 acceptance claim.

## Hosted success

[Run 37459891906, attempt 1](https://github.com/ViviDynamics/scrutare/actions/runs/37459891906)
completed on [caller PR 35](https://github.com/ViviDynamics/scrutare/pull/35).
The CLI captured reviewed head `7f4ab868bbd6256147c27aafc6876edf215a9858`.
One `senior-dev` persona reviewed only
`tests/fixtures/action-acceptance/target.py` through OpenAI Spark alias
`spark/glm-5.3-flash` at `https://llm.vividynamics.com/v1`, with 10,000 reported-token
after-turn allowances for the persona and whole review.

Two completed streamed turns reported 6,024 tokens, with complete accounting
and no overshoot. CLI stdout exactly matched persisted `result.json`.
The actual [github-actions[bot] review 5427984625](https://github.com/ViviDynamics/scrutare/pull/35#pullrequestreview-5427984625)
was COMMENTED with two inline comments and the captured commit. Fresh remote
review/comment reads matched the saved payload and receipts. The code-derived
verdict was `approve`; `github.post_mode: comment` sent COMMENT. This establishes
focused delivery, without establishing APPROVE permission or whole-PR coverage.

## Hosted invalid-config failure

[Run 37462193493, attempt 1](https://github.com/ViviDynamics/scrutare/actions/runs/37462193493)
used triggering event/PR head `8d13754cca0fee828277a9d8e10e2c19bc8f157f`.
The trusted fixture's invalid strategy failed before GitHub capture, nare launch,
model work or posting. There is no CLI captured reviewed-head result for this
failure. The composite review and parent job remained failed while the internal
uploader succeeded. The service-bound archive retained nonempty stderr and empty
CLI stdout, with no session, result, findings, verdict, posting journal or
manifest. Configuration validation precedes captured-run creation here.

Fresh remote review-list comparison showed no new review and preserved the
prior successful COMMENT. Absence of success outputs is supported by the
executed failure evidence, released adapter control flow and native adapter
fixtures; no direct hosted public-output observer was instrumented.

## Limits and future runs

The immutable `2026.10.1`
[hosted Spark attempt 37406395668](https://github.com/ViviDynamics/scrutare/actions/runs/37406395668)
completed one turn before Cloudflare HTTP 524. This release2 success establishes
this streamed run, without proving that slow first bytes or silent gaps cannot
fail. Token allowances remain soft thresholds, with no hard spending ceiling or
complete provider billing claim. Reported tokens do not establish billed cost.

For fresh authorized runs, retain the trusted base/configuration, admission,
credentials, COMMENT mode and evidence checks in the [pipeline guide](pipeline.md)
and [fixture procedure](../tests/fixtures/action-acceptance/README.md).
