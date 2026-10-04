# Posting a captured review

The Python poster API accepts an existing ingestion run and a code-derived
`Verdict`. It sends one complete GitHub review containing a summary and one
inline comment per merged finding, retaining every source persona, category and
reason. It does not run reviewers, call models or implement a replay CLI.
The `scrutare review` command still captures inputs only.

`post_review(run_dir: Path, verdict: Verdict, *, client: ReviewClient | None = None,
sleeper: Callable[[float], None] | None = None) -> PostedReview` uses existing
`gh` authentication when no client is supplied. That identity needs permission
to create a review with the configured event. The poster never changes tokens,
App identity or review events to work around a rejection.

In this example, `run_dir` is the `Path` returned by `ingest_pr`, `verdict` is
produced by the verified findings pipeline with the captured verdict policy,
and `client` is a `ReviewClient` supplied by the caller. A fake client can be
supplied to exercise the same flow without contacting GitHub. Omit `client=client`
to use the default transport.

```python
from scrutare.poster import post_review

receipt = post_review(run_dir, verdict, client=client)
print(receipt.html_url)
```

The receipt has `review_id`, `html_url`, `commit_id`, `body`, `state` and `login`.
Calling the API again with the same run and evidence returns a confirmed receipt
without another create request. Canonical verdict bytes, ordered merged findings,
captured diff anchors, YAML settings and the effective JSON config must agree
before any network access. Top-level repository, PR number and head must also
match the nested captured PR object. Changes to a started run's evidence,
strategy, mode, policy or payload fail safely.

## Events and lifecycle

| Captured mode | Code verdict | GitHub event | Receipt state |
| --- | --- | --- | --- |
| review | approve | APPROVE | APPROVED |
| review | changes_requested | REQUEST_CHANGES | CHANGES_REQUESTED |
| comment | either | COMMENT | COMMENTED |

The summary names the strategy, verdict, deciding rule, finding count, complete
captured SHA and an opaque run marker. Each comment uses its exact captured
LEFT or RIGHT anchor. Immediately before every permitted write, the poster
rechecks that the PR is open and unmerged. A push or force push is allowed, but
`commit_id` always stays at the captured SHA. An unreachable old commit or an
unauthorized event can be rejected; the poster never switches commits or events.
GitHub can close or merge the PR between the lifecycle read and the POST. This
race cannot be eliminated by the client.

## Durable artifacts

Captured files are preserved. An absent `verdict.json` is written with the
existing canonical verdict writer; existing bytes must match `Verdict.to_bytes()`.
`findings.json` contains the ordered array of `MergedFinding.to_dict()` values.
Existing matching findings retain their original formatting and bytes.

| Artifact | Purpose |
| --- | --- |
| `.posting.lock` | Persistent inode for a nonblocking exclusive POSIX advisory lock |
| `review-payload.json` | Exact canonical UTF-8 JSON sent by the transport |
| `posting.json` | Version 1 journal for target, identity, evidence and outcome |

The journal records `schema_version`, `repository`, `pr_number`, `head_sha`,
`event`, one UUID hex `run_id`, and SHA-256 hashes of the canonical payload,
verdict, effective config and captured diff. It also stores `status`, `attempts`,
`failure`, `retry_at`, `http_status` and `receipt`. `retry_at` is an absolute Unix
wall-clock deadline in seconds, or null. The receipt is null until confirmed;
then it contains the six fields listed above.

States are `prepared`, `sending`, `rejected`, `unknown` and `posted`. Rejection
records distinguish `throttle`, `unsent` and `permanent`. Each journal and payload
write uses an atomic sibling replacement with file and directory synchronization.
`Sending` is persisted before each POST. A failure persisting a successful
receipt leaves the prior `sending` or `unknown` state, so restart reconciles.
Partial preparation, corrupt journals and missing or changed payloads fail
before a write. Keep the complete run directory: deleting journals discards
delivery history and must never be used to resolve uncertainty.

The lock supports Linux and macOS through `fcntl`. Another invocation receives
a safe error immediately. Process exit releases the lock; the lock file is
never unlinked during posting. Environments without working advisory locks fail
before network access. Durability depends on the filesystem honoring atomic
replacement, synchronization and advisory locks, and on a reasonably accurate
system clock for retry deadlines.

## Rejection, retries and uncertainty

`PostingRejected` reports explicit HTTP rejection or a proven unsent launch
failure. Ordinary rejection is permanent for this run. A proven unsent failure
can resume on a later invocation. Only `PostingRateLimited`, a rejection subtype,
permits an automatic write retry. There are at most three create attempts total
for a run, including attempts across restarts and proven unsent launches.

The poster persists the server wait deadline and respects Retry-After and
applicable exhausted-quota reset guidance. Secondary throttling without usable
timing waits at least 60 seconds. Each retry wait is at most 60 seconds, with at
most two waits within the three create attempts. A larger remaining wait raises
`PostingRateLimited` with that delay. A restart cannot bypass the saved deadline.
After waiting, it rechecks lifecycle and
persists `sending` again. Exhausted attempts raise a safe retry-later rejection
and never send another review. An injected sleeper must actually satisfy the
required wait; tests use an advancing fake clock.

Timeouts, disconnects, 5xx responses and malformed success are uncertain. The
poster reads all review pages and the current authenticated login. Exactly one
review must match the marker, complete summary body, captured commit, expected
state, valid review URL/ID and that author. Then its receipt can be recovered.
Zero, multiple or malformed marker matches, failed listing or unavailable
identity remain `unknown` and raise `PostingUncertain`. Missing matches never
prove that an ambiguous write failed, even on a later invocation. `sending` and
`unknown` restarts only reconcile and never create another review.

Normal successful posting does not require `/user`. Installation tokens that
cannot read the authenticated login can still post, but may leave uncertain
runs unresolved. Reads use the transport's bounded retries. The poster never
edits or deletes another review. Inspect uncertain delivery and the saved run
before any manual GitHub action; do not create a fresh run to bypass its journal.
