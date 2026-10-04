# Review poster: one anchored review with summary

Issue #6

## Scope

In: Pure payload rendering from a verified verdict and captured diff; a safe injectable gh transport; durable one-review-per-run posting and reconciliation; bounded transient handling; documentation.
Out: Persona sessions, strategy execution, CLI wiring, escalation, App identity or credentials, and live test posting. These belong to other issues. Tests exercise real payload/state behavior through injected transports without notifying PR participants.

## Assumptions

- Comment mode uses a COMMENT review, preserving inline anchors and the same summary without an approval or requested-changes event. A plain issue comment cannot carry inline anchors.
- A captured head remains authoritative after a force push. A newly closed or merged PR aborts before each permitted write.
- A marker supports reconciliation, not server idempotency. Ambiguous writes never repeat solely because one read found no review. Explicit throttling rejection may retry, at most three create attempts.
- Canonical verdict bytes remain independent of posting state. The captured config is authoritative for strategy, post mode and category policy.

## Global Constraints

- SPEC sections 2, 5, 6, 8, 9 and 11 bind. A model never declares a verdict. Each surviving merged finding gets one inline comment with every source persona/category/reason, anchored to the captured diff.
- Summary names strategy, code verdict, deciding rule, finding count and complete captured SHA. review mode maps approve to APPROVE and changes_requested to REQUEST_CHANGES. comment mode always COMMENT, with identical body/comments for the same run input.
- Revalidate all anchors before network. Invalid evidence aborts the payload, never silently drops findings while keeping their verdict. LEFT/RIGHT and rename/delete paths follow parse_diff.
- Posting sends one complete create-review request pinned with commit_id. Do not switch to the latest SHA, downgrade a rejected event, or change review identity.
- Persist intent and sending state before a write, preserve canonical inputs, hold a per-run exclusive lock, and reconcile uncertain delivery without another create. A receipt failure after successful send cannot permit resending.
- No token/App/deployment changes, new YAML fields, suppressions or em dashes in authored published prose. Raw captured evidence stays lossless in artifacts. Tests use no live mutations/model calls.
- TDD per task, focused iterations, then all six project gates on Python 3.10 and 3.14 before task commit. No implementation in coordinator.

## Tasks

### Task 1: Pure anchored review payload

**Files:** src/scrutare/poster/__init__.py, payload.py, tests/test_review_payload.py.
**Interfaces:** frozen ReviewComment(anchor: Anchor, body: str), frozen ReviewPayload(head_sha: str, body: str, event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"], comments: tuple[ReviewComment, ...]); fresh to_dict() REST payload with commit_id/body/event/comments and deterministic to_bytes(). build_review_payload(verdict: Verdict, diff: bytes | str, *, head_sha: str, strategy: Strategy, post_mode: PostMode, run_id: str) -> ReviewPayload. PostingError(GitHubError) as safe base domain error, placed in poster/errors.py if shared errors need a focused file.

- [ ] RED: test exact event mapping for blocking/advisory/empty verdicts, identical body/comments between modes, summary fields and full captured SHA, and every persona/source reason in one comment per group.
- [ ] Validate typed inputs, 40 or 64 hexadecimal head SHA, known strategy/mode, nonempty safe run ID (32 lowercase hex characters), immutable comment tuple and direct constructors. The opaque marker is exactly <!-- scrutare-run:{run_id} --> in the summary. Caller-owned run IDs are persisted by Task 3.
- [ ] Build anchors using existing parse_diff and reject any verdict finding absent from the captured index before producing payload. Cover both sides, deleted/renamed files, multiple groups and mixed category sources. Do not fetch current diff or rederive a model verdict.
- [ ] Inline REST fields are path, line, side and body. Display problem and source attribution/category/reason with safe text rendering so adversarial Markdown/HTML/newlines cannot forge authored labels or the summary marker. Preserve the original values in verdict artifacts. No hardcoded verdict-category policy in renderer.
- [ ] Pure rendering performs no I/O, network, subprocess, clocks or randomness. Fresh JSON data and immutable output; test byte/dict mutation isolation and adversarial strings.
- [ ] Focused red/green, six gates both versions, self-review, task commit and exact report.

### Task 2: One-attempt write transport and bounded safe reads

**Files:** src/scrutare/poster/client.py, errors.py, __init__.py, tests/test_review_client.py. Keep existing engine/github.py read-only; reuse GitHubClient and its public lifecycle/metadata validation rather than duplicate those rules.
**Interfaces:** ReviewClient(runner: Runner | None = None, *, sleeper: Callable[[float], None] | None = None). get_pr(ref), get_reviews(ref), get_login() for validated read-only data; create_review(ref, payload: ReviewPayload) -> PostedReview. Frozen PostedReview(review_id: int, html_url: str, commit_id: str, body: str, state: str, login: str). Shared PostingError, PostingRejected, PostingRateLimited with safe retry timing, and PostingUncertain. Task 3 owns all write retries, not the transport.

- [ ] RED: verify exactly one gh create call with JSON bytes on stdin, explicit method/endpoint, supported API headers, shell=False, capture_output=True, text=False, timeout60 and child GH_HOST pinned github.com while preserving authentication environment. Validate repository/PR targets using existing public reference resolution before any subprocess. Untrusted finding strings never become command arguments.
- [ ] Parse gh --include structured HTTP status/header blocks and JSON. Success is on stdout; actual failed GET probe shows non-2xx structured response on stderr followed by a gh diagnostic (evidence .agents/state/6-gh-framing.json). Parse the HTTP block, not a free-form stderr status guess; never expose stderr, request bytes, raw exceptions or server messages. Retain only safe rate-limit headers needed for retry decisions. Accept CRLF/LF and actual HTTP/2.0 framing. Missing/malformed status or success body after launch is uncertain for POST.
- [ ] create_review performs one attempt. Positive review ID plus valid safe GitHub URL, commit/body/state/login required. Confirm expected commit/body/event-derived state before success; a malformed or mismatched success is ambiguous, not permission to resend. RateLimited only for explicit429 or403 with rate-limit evidence; ordinary4xx definite rejection, timeout/network/5xx ambiguous. Missing executable or proven pre-launch failure is definitely unsent and safe actionable failure.
- [ ] Read-only metadata/review pagination may delegate to GitHubClient with a bounded three-attempt retry for safe generic read failures. get_login must validate a login and use safe framing; no raw error values. Never retry writes inside this class. Existing malformed pagination/metadata validation remains intact. A closed PR is checked by orchestration via assert_pr_open, not treated as a retryable write.
- [ ] Test status classes, ordinary403 versus rate-limited403,429,5xx,timeouts,missing CLI, invalid UTF-8/JSON, metadata/list failures, login validation, hostile stdin and safe diagnostics. No live requests in tests.
- [ ] Focused red/green, six gates both versions, self-review, task commit and exact report.

### Task 3: Durable posting flow and replay-safe artifacts

**Files:** src/scrutare/poster/posting.py, journal.py if needed to keep persistence separate, __init__.py, tests/test_post_review.py, docs/posting.md, README.md.
**Interface:** post_review(run_dir: Path, verdict: Verdict, *, client: ReviewClient | None = None, sleeper: Callable[[float], None] | None = None) -> PostedReview. Captured metadata and config.yaml/config.json supply repository/PR/head and validated strategy/mode/policy. Captured config snapshots must agree, and verdict.config must match their effective partition. Cross-check top-level repository/PR/head against nested captured PR metadata before any network.

- [ ] RED: full fake-client flow sends one complete review and persists a receipt; repeated same-run calls return the confirmed result with zero second creates. Different evidence/mode/payload for a started run fails. Tests use realistic ingestion fixtures and real run-directory bytes.
- [ ] Before network, load/validate captured metadata, config and diff, derive payload from validated verdict, and require existing verdict.json bytes match Verdict.to_bytes(); write an absent verdict using existing write_verdict. Persist findings.json as an ordered array of MergedFinding.to_dict() if absent and require existing bytes/data match the same evidence. Leave captured artifacts and canonical verdict unchanged through all outcomes. Do not implement replay CLI.
- [ ] Serialize runs with a nonblocking OS advisory lock over a persistent .posting.lock file, using POSIX fcntl (supported deployment/CI Linux, workstation macOS). Process exit releases the lock; do not unlink a live lock inode. Unsupported lock environments fail safely before network. Reject concurrent invocation with a safe error.
- [ ] Generate one uuid4().hex run ID only before first preparation and persist it. Store exact canonical review-payload.json and posting.json schema_version1 with target/head/event/run ID/payload hash/status and confirmed receipt when available. Atomic sibling writes. Existing state/payload must validate and match freshly derived evidence; a run cannot silently acquire a new marker after it may have sent.
- [ ] States distinguish prepared, sending, rejected/unsent, unknown and posted. sending is persisted before every POST. On restart sending/unknown reconciles only. Successful receipt persistence failure leaves sending/unknown durable and never permits a duplicate send. Failures writing preparation/sending state occur before mutation.
- [ ] Immediately before each allowed write, assert_pr_open(client.get_pr(ref)); closed/merged aborts with no new review. A new head is allowed, but payload always uses captured SHA. GitHub rejection of old/unreachable commit is a failure, never a switch to new head. Document unavoidable close-after-check race.
- [ ] Only explicit throttling rejection retries writes, maximum three attempts total persisted across invocation/restart. Persist a retry deadline so restart cannot bypass a required server wait. Respect server Retry-After/reset guidance; secondary throttle without timing waits at least60 seconds. A wait above60 seconds or exhausted attempt budget returns safe retry-later rejection rather than sending early. Inject sleeper for tests. Recheck lifecycle and persist sending before permitted retry. Known unsent launch failures may resume later; ambiguous timeout/5xx/disconnect/malformed success reconciles, never blind resends.
- [ ] Reconciliation reads all reviews and the current authenticated login, matches opaque marker plus exact summary/body, capturedcommit,event-derivedstate and own author. One valid match recovers the receipt. Zero/multiple/malformed matches or failed listing remain uncertain; absence never proves an ambiguous write failed. Never edit/delete another review. Bounded safe reads, no review-event downgrade.
- [ ] Cover closure/merge, forcepusholdSHA, explicit throttlethen success, permanent failure, timeout/5xx with recovered receipt, unresolved ambiguity with no second create, restart from sending, receipt-write failure, journal corruption/payload mismatch, concurrent lock, exact preserved verdict/config/findings bytes and safe errors. Verify actual documented example through fake-client integration without real posting.
- [ ] Document API scope, event mapping, retry/uncertainty recovery, artifact format and practical limits. README accurately separates available poster API from unfinished live-session CLI.
- [ ] Focused red/green, six gates both versions, self-review, task commit and exact report.
