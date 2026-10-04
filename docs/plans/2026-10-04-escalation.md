# Escalation Implementation Plan

> For agentic workers: use subagent-driven-development task by task, with independent task and final reviews.

Issue #11. Goal: derive and deliver a distinct escalated outcome from typed code-owned nonconvergence evidence, with unresolved summaries and human review requests.
Architecture: extend the pure verdict additively, reuse one COMMENT review and its durable poster, then deliver a separately journaled reviewer-request stage under the same run lock. Tech stack: Python 3.10+, existing gh transport and canonical JSON. Spec: docs/SPEC.md sections 6-8 and 11.

## Scope

In: pure exhaustion/verdict evidence, safe summary and validated mentions, one-attempt reviewer transport, durable two-stage Python API and artifacts/output, documentation.
Out: actual session/strategy execution and full CLI review invocation, owned by #3/#10/#15/#17/#7. No nare workaround, provider calls, team resolution, App identity, credentials or deployment changes.

## Assumptions and rulings

- Component acceptance is independent of blocked live callers, as #6 and #9. All specified effects are exercised through production APIs and captured artifacts; the CLI continues to report ingested. Cost if wrong: reopen #11 until real strategy integration is accepted, without advertising live review completion.
- A COMMENT review satisfies the summary-comment requirement and retains captured SHA/inline evidence, consistent with #6. Cost if wrong: add a separately specified issue-comment surface with equivalent recovery.
- Only plain human GitHub account logins are supported at escalation posting boundary; config still accepts nonempty strings. Case-insensitive duplicates normalize to first spelling. Cost if wrong: add explicit team-resolution semantics without silently changing existing target meaning.
- Exhaustion means exactly the positive round bound was completed and convergence is false. It is typed caller control evidence, not a model finding or proof of execution history. Cost if wrong: strategy-owned outcome evidence must extend the representation before integration.
- Positive current requested membership confirms desired state, not ownership by this run. Missing/partial membership after uncertain delivery never authorizes resend. Cost if wrong: operator reconciliation can be needed, avoiding duplicate notifications.

## Global Constraints

- SPEC is authority. The verdict is never model-declared. When the strategy's rounds are exhausted without convergence, the harness never approves; verdict is escalated.
- Existing YAML config only. Ordinary verdict artifact bytes and review rendering remain unchanged. Conditional exhaustion evidence is an additive schema_version1 variant for #13 replay.
- Every model call goes through nare; this component makes none and does not work around nare#41.
- PR closes or merges mid-run: abort remaining writes. Force push retains captured SHA rather than claiming to review a newer head.
- Read-only tests/fake network only, no live review or reviewer-request writes. Python3.10 and3.14 all six project gates before each task commit. No em dashes in authored prose or suppression/quality weakening.
- Coordinator owns plan/ledger/reviews; workers implement production and do not dispatch subagents. One implementation worker at a time.

## Review Focus

1. Advisory-only/empty unresolved evidence at exhaustion must never approve (Task1).
2. Invalid human targets or forged mention text must not publish unintended mentions or partial invalid work (Task1/3).
3. A human who already reviewed disappears from current requests: uncertain absence must never trigger another notification (Task2/3).
4. A crash between two external writes must preserve the first receipt and not silently declare complete delivery (Task3).
5. Orphan/tampered cross-stage artifacts and concurrent callers must fail before network and preserve canonical evidence (Task3).

## Tasks

### Task 1: Code-derived exhaustion and safe escalation review

Files: findings/verdict.py and exports, poster/payload.py, new poster/reviewers.py normalization utility, tests/test_escalation_verdict.py and test_escalation_payload.py. Existing tests retained.
Interfaces: frozen Exhaustion(strategy:Strategy, rounds_completed:int, round_limit:int, converged:bool=False), validated known strategy, actual int counters excluding bool, positive bound, rounds_completed==round_limit, converged is False. Verdict gains exhaustion:Exhaustion|None=None, derive_verdict(..., *, exhaustion:Exhaustion|None=None)->Verdict. Exhaustion overrides status to escalated and rule to rounds_exhausted_without_convergence; otherwise exact existing behavior. to_dict conditionally adds exhaustion {strategy,rounds_completed,round_limit,converged} only when present; v1/canonical normal bytes preserved.
normalize_human_reviewers(values:tuple[str,...])->tuple[str,...] in poster/reviewers.py validates 1-39 ASCII alphanumeric/hyphen human logins, no leading/trailing/doubled hyphen, @ prefix, slash, whitespace, controls or bot suffix; dedup case-insensitively first order/spelling. Safe PostingError referencing github.human_reviewers, no bad value echo.
build_review_payload existing signature gains human_reviewers:tuple[str,...]=(). Escalated always COMMENT irrespective post_mode, strategy must equal exhaustion.strategy. Body includes distinct status/rule, completed/bound, captured SHA, all unresolved anchors/problems/sourcepersona/category/reasons and validated @mentions with wording that does not claim requests already succeeded. Explicit empty target sentence: This repository has no escalation targets configured. Empty findings: No unresolved findings were supplied. Keep marker and inline evidence. Preserve ordinary rendering bytes; neutralize untrusted mentions in new escalation body so configured validated targets are the only deliberate mentions.

- [x] RED: test_exhaustion_overrides_blocking_advisory_and_empty_to_escalated, test_normal_verdict_bytes_unchanged, test_invalid_exhaustion_rejected including bool/overshoot/converged, test_artifact_exhaustion_reconstruction_is_byte_identical.
- [x] RED: test_both_modes_render_escalation_as_comment_with_summary_and_mentions, test_empty_targets_and_findings_say_so, test_invalid_targets_fail_without_echo, test_duplicate_targets_normalize, test_untrusted_summary_cannot_forge_heading_marker_or_mentions, test_strategy_mismatch_rejected.
- [x] Implement the typed extension and focused pure renderer/normalizer. Do not add reviewer HTTP transport, persistence orchestration or CLI review execution.
- [x] Run focused RED/GREEN, self-review, full six gates bothPythonversions, commit and exact report.

### Task 2: One-attempt reviewer-request transport

Files: poster/client.py (reuse existing safe HTTP helper), poster/reviewers.py receipt, exports as needed, tests/test_reviewer_requests.py.
Interfaces: frozen ReviewerRequestReceipt(reviewers:tuple[str,...],provenance:Literal['post_response','observed_requested','no_targets']) with legal normalized tuple/provenance combinations; empty only no_targets, nonempty otherprovenance. ReviewClient.request_reviewers(ref:PullRequestRef,reviewers:tuple[str,...])->ReviewerRequestReceipt; get_requested_reviewers(ref)->tuple[str,...]. Consume Task1 normalization. One POST attempt only, exact JSON {reviewers:[...]}, endpoint repos/{owner}/{repo}/pulls/{number}/requested_reviewers; stdin/shellFalse/timeouts/headers/environment consistent existingclient. Require documented201 success with matching PR number/repository identity and all intended logins in PR-shaped requested_reviewers response. Do not require live head==capturedhead. POST explicit rejection/throttle classification reuses existing logic; launch failure provenunsent, timeout/5xx/malformed success uncertain. No POST retries intransport, no /user prereq.
GET documented object {users:[...],teams:[...]} with validusers; return all normalized human logins forpositive membership checks. No documentedpagination params, no borrowing list-reviewpagination; reject unexpected pagination metadata before pretending complete. Bounded read retries viaexistingclient _read. Distinguish response shapes, allow extra validrequestedusers. No team resolution.

- [x] RED: test_request_reviewers_uses_exact_one_attempt201_pr_shape, test_wrong_pr_missing_targets_or_malformed_receipt_uncertain, test_get_requested_reviewers_uses_users_teams_shape, test_invalid_targets_before_transport, test_additional_users_and_force_push_allowed.
- [x] RED: transport403/429timing, permanent4xx,5xx,timeout,launchfailure,invalidsuccess, malformedGET, unexpectedpagination. Validate frozen directreceipt types/provenance/no_targets combos.
- [x] Implement focusedmethods/receipt without orchestrator/journal/real network. Reuse HTTP classification rather than duplicating it; small shared write helper is allowed if existingreviewbehavior tests remain unchanged.
- [x] Focused RED/GREEN, six gates both versions, self-review, commit and exact report.

### Task 3: Durable two-stage escalation and documentation

Files: new poster/escalation.py, minimal posting.py lock-heldhelper extraction and validation integration, exports, tests/test_post_escalation.py, docs/escalation.md and README/config reference.
Interfaces: post_escalation(run_dir:Path,verdict:Verdict,*,client:ReviewClient|None=None,sleeper:Callable[[float],None]|None=None)->PostedEscalation; frozen PostedEscalation with review:PostedReview,reviewer_request:ReviewerRequestReceipt, verdict property fixed escalated and fresh to_dict serializableoutput. Only escalatedVerdict accepted. Compare exhaustion.strategy and round_limit to captured config.strategy/rounds.max before all writes. Pass normalized capturedhumanreviewers into reviewedpayload, preserving post_review escalation COMMENT compatibility.
Use a single outer run_lock for completeoperation with narrowlyinternal lock-held reviewhelper; public post_review stillownslock, nevernested. Preserve existingreviewjournaling and retries exactly. Validate all available review/request artifacts andintent before any network through either entrypoint. post_review is explicitlylowlevelreviewonly; post_escalation promisesbothstages. Existingescalationjournal preventsconflicting/tampered bypass throughpost_review.
Artifacts: existing posting.json/review-payload.json/verdict.json/findings.json unchanged normalflow; reviewer-request.json canonical {reviewers:[...]}; escalation.json schema_version1 immutableintent repository,pr_number,head_sha,run_id,review_id (onceknown), normalizedreviewers, payload/verdict/config/diff/requestSHA256 plus strictrequeststage status/attempts/receipt/failure/retry_at/http_status. Prepare maybeforefirstcomment orafterconfirmedcomment, but validate preexistingfuturestage artifactsbeforeallnetwork; interruptionneverpermitsblindoverwrite. Exactstatefieldsconsistentplan/receipt; unresolveduncertaintyexplicit.
Request stages prepared/sending/unknown/rejected/posted and skipped/no_targets. Persist sending before POST; max3attempts for explicitthrottle/provenunsent only, savedabsolute deadlines, eachwait<=60seconds analogousexistingposter; permanentrejection staysfailed. Emptytargets: skipped/no_targets, zeroattempts/noPOST, but summaryCOMMENT stillpublished. Confirmedreview notsentagain. For sending/unknown, GETpositivealltargets -> observed_requested; absent/partial/readfailure/malformed -> uncertain withnoresend. Successresponse provenance post_response. No causalownershipclaimfromGET. Receipt writefailure retainsrecovery state. Checkopen/unmergedbeforeeachwrite; closeafterCOMMENT retainsfirstreceipt andfailsrequests. No rollbackornewapproval afterescalation. Preservecaptured SHAforcepush.

- [x] RED: real coherentcapture fake-network flow postsoneCOMMENT thenoneexactrequest; repeatsperformnowrites andsamefreshoutput; emptytargetsCOMMENT only withskippedreceipt; wrongverdict/configbound/invalidtarget failbeforefirstwrite.
- [x] RED: assert durable sending before bothwrites; crash/failurebefore/aftereachreceipt, restartdoesnotduplicatereview or uncertainrequests; positivefullmembershiprecoverobserved_requested, zero/partial/removedreviewer/malformed/readfailurestayunknown noresend.
- [x] RED: throttledeadlines/threeattemptcap persistacrossresumes fakeclockadvancingsleeper; permanentknownrejection; requestretryneverrepostsCOMMENT; close/mergebeforefirstwrite andbetweenwrites, forcepushcapturedSHA.
- [x] RED: orphanpayload/journal, changedtargets/exhaustion/config/diff/runid/reviewreceipt, malformedstates/provenance, concurrentlock, conflictingordinarypost_review failbeforewrites. Exactoldpostingtestsretainbehavior.
- [x] Implementfocusedstate machine and minimal lockrefactor, no alternateengine. Document APIs/artifacts/provenance/recovery/targets/COMMENT andlivecaller integrationlimitations; CLI remains honestingestion. If filegets unwieldy, reportbeforeunplannedrestructure.
- [x] Focused RED/GREEN, allsixgatesbothversions, self-review, commit/report. Controller ownsremoteissueintegrationnotes aftership.
