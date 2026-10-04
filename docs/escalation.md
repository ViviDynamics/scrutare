# Escalating a captured review

The Python API delivers a code-derived `escalated` verdict as one GitHub
`COMMENT` review followed by requests to the captured `github.human_reviewers`.
It never approves an exhausted run, including a run with only advisory findings
or no unresolved findings. Both `github.post_mode` values produce `COMMENT` for
escalation. The summary retains the captured head SHA, unresolved findings and
inline anchors, identifies the completed round bound, and mentions configured
human targets without claiming that the requests have already succeeded.

This is a component API. The strategy runner must supply trustworthy control
flow evidence that it completed its bound without convergence. `Exhaustion`
validates this assertion; it does not prove execution history. Session execution,
strategy integration and full CLI review execution remain separate work. The
`scrutare review` CLI still reports ingestion, and this component makes no model
calls or changes to the nare dependency policy.

## Calling the API

`post_escalation(run_dir: Path, verdict: Verdict, *, client: ReviewClient | None =
None, sleeper: Callable[[float], None] | None = None) -> PostedEscalation` accepts
only an escalated verdict. Its exhaustion strategy and round limit must match
captured `strategy` and `rounds.max`. Findings must come from the existing
verified findings pipeline and use the captured verdict policy.

In this example, `run_dir` comes from `ingest_pr`, `config` is the captured parsed
configuration, `findings` are verified merged findings, and `exhaustion` is an
`Exhaustion` produced by the calling strategy after nonconvergence. `client` can
be a fake for offline testing or a `ReviewClient`; omit it to use existing `gh`
authentication. The authenticated identity needs permission for both writes.

```python
from scrutare.findings import derive_verdict
from scrutare.poster import post_escalation

verdict = derive_verdict(findings, config.verdict, exhaustion=exhaustion)
receipt = post_escalation(run_dir, verdict, client=client)
print(receipt.to_dict())
```

The frozen `PostedEscalation` contains `review: PostedReview`,
`reviewer_request: ReviewerRequestReceipt`, and a fixed `verdict` property of
`escalated`. `to_dict()` returns fresh JSON-serializable objects with those three
keys. A successful return confirms both stages, or confirms the COMMENT and
records an empty-target skip. Repeating a completed call returns the saved
receipts without any network requests.

`post_review` remains a lower-level API that sends or recovers only the review.
For an escalated verdict it applies the same configuration and target validation
and renders the same COMMENT, but does not request reviewers or promise full
escalation delivery. A subsequent `post_escalation` can complete that run.

## Human targets and mentions

At the posting boundary, targets must be plain GitHub account logins of 1 to 39
ASCII letters, digits and internal single hyphens. Leading or trailing hyphens,
doubled hyphens, whitespace, controls, `@` prefixes, team paths and literal
`[bot]` suffixes fail before the first network access. A login such as
`service-bot` is syntactically valid. No team resolution or account-type lookup
is performed. Case-insensitive duplicates retain the first spelling and order.
The YAML parser continues to accept nonempty strings; this stricter validation
occurs when posting escalation.

Only normalized configured targets become deliberate `@mentions` in the new
escalation rendering. Untrusted finding text uses a fullwidth `＠` to avoid
forging mentions. Empty targets produce an explicit summary sentence and a
`no_targets` receipt with zero request attempts. The COMMENT is still sent.

## Artifacts and restart

One nonblocking `.posting.lock` covers the complete operation. The existing
[review journal and artifacts](posting.md#durable-artifacts) keep their meanings.
Before either public posting entrypoint makes any network call, it validates all
available review and request artifacts and their links to captured evidence.
Orphan, missing, changed or malformed linked delivery artifacts fail without
overwriting them. As in ordinary review posting, missing `verdict.json` or
`findings.json` evidence can be regenerated from the supplied matching verdict.

After the COMMENT is confirmed, escalation writes two additional artifacts:

| Artifact | Contents |
| --- | --- |
| `reviewer-request.json` | Canonical JSON with exactly `{"reviewers": ["Alice"]}` in normalized target order |
| `escalation.json` | Version 1 immutable delivery intent, request state and receipt |

The escalation intent contains `schema_version`, `repository`, `pr_number`,
`head_sha`, `event` (`COMMENT`), `run_id`, `review_id`, and `reviewers`. It records
`payload_sha256`, `verdict_sha256`, `config_sha256`, `diff_sha256`,
`request_sha256` and `review_receipt_sha256`. The last hash binds all six
canonical validated review receipt fields, not just its ID. Config hashing uses
the effective canonical configuration, as in the review journal. Request
preparation occurs after the review receipt, so its ID is always known.

Request state fields are `status`, `attempts`, `receipt`, `failure`, `retry_at`
and `http_status`. States are `prepared`, `sending`, `unknown`, `rejected`,
`posted`, or `skipped`. Only empty targets may be `skipped`, with zero attempts
and a `no_targets` receipt. A request receipt has exactly `reviewers` and
`provenance`. Receipt provenance is:

| Provenance | Meaning |
| --- | --- |
| `post_response` | A valid successful POST response confirmed all targets |
| `observed_requested` | A recovery GET observed all targets currently requested |
| `no_targets` | No targets were configured, so no request POST was needed |

A recovery GET confirms desired current state, without claiming this run caused
it. It requires every intended target to be present, case-insensitively; extra
valid human targets are allowed. Missing, partial or malformed membership and
read failures leave delivery `unknown` and raise `PostingUncertain`. Someone
who already reviewed can disappear from current requests. Their absence never
proves the earlier POST failed and never authorizes another notification.

The poster persists `sending` before a POST. A crash or failed receipt write
leaves a state requiring reconciliation, unless the completed atomic receipt
replacement is already visible. Restart never resends a `sending` or `unknown`
request. Partial artifact preparation fails safely and requires inspection.
Preserve the entire run directory; deleting journals or creating another run to
bypass uncertainty discards delivery history.

Explicit throttling and proven unsent launches share a maximum of three request
attempts across restarts. Permanent rejection stays failed. Only throttling
retries automatically; a proven unsent launch can resume on the next call.
The saved absolute `retry_at` deadline cannot be bypassed by restarting. Each
sleep is at most 60 seconds; a larger remaining wait raises `PostingRateLimited`.
Injected sleepers must advance the clock sufficiently. Review and request
attempt budgets are separate, and request retries never repost the COMMENT.

Immediately before each external write, the poster checks that the PR is open
and unmerged. If it closes after the COMMENT, that receipt is retained and the
remaining write fails; the comment is not rolled back. The lifecycle check
cannot eliminate a close between its read and the POST. A force push retains
the captured SHA. The poster does not substitute another commit, token, identity
or event, and never converts escalation into approval.
