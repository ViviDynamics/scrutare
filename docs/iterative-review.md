# Iterative review across pushes

Set `strategy: iterative` in `scrutare.yaml` and retain the same runs directory
between invocations. Each call captures the current pull request and posts its
own review. The first push executes the configured personas through nare. Later
pushes execute those sessions against new patch hunks and newly contested
finding hunks. Other findings stay in the pool without another model call.
Changing Git's index hashes alone does not make an unchanged hunk new; changed
hunk coordinates do, because their anchors need verification again.

Root inline comments from scrutare-generated reviews are excluded, so posted
findings never contest themselves. Replies on those threads remain eligible.
Other inline review comments that match an active finding's path and line contest that
finding. A comment with an absent or null line matches active findings on that path. Existing
discussion is consumed once; editing a comment creates fresh contest evidence.
Prior findings and matching discussion are supplied as untrusted evidence in the
persona prompt, outside the prepared file read root.

`iterative.json` records every finding with its latest disposition:

- `upheld`: still supported or carried from untouched evidence.
- `fixed`: changed evidence no longer yields the finding, or its hunk left the PR.
- `withdrawn`: unchanged contested evidence no longer yields the finding.

Only upheld findings enter the current verdict. Fixed and withdrawn entries stay
visible in the saved pool. The current run retains the raw capture, a separate
`iterative-round` capture containing just the reviewed hunks, child sessions,
usage, verdict, and provenance inventory. Offline replay uses the ordinary
canonical findings and verdict artifacts.

Private `.iterative` history under the runs directory is keyed by repository and
PR number. A process lock refuses concurrent reviews of the same PR; different
PRs have independent locks and bounds. Atomic state snapshots survive process
restarts. Configuration changes are refused instead of silently resetting the
pool or bound. Keep the history alongside the run artifacts, including when
running in CI or from a webhook worker. A fresh runs directory starts new history.

`rounds.max` counts actual review waves across pushes. The round is reserved
before a child process starts, so a failed or interrupted wave still counts.
An identical push without a new contest consumes no round. Unresolved blocking
findings at the last round escalate, as does any new reviewable patch after the
bound. Escalation uses the existing GitHub delivery and human reviewer request
behavior. The normal token budgets apply to each actual wave and its anchor
correction opportunity.

## Replies on Scrutare threads (opt-in)

By default any inline comment that matches an active finding contests it on the
next invocation, whoever wrote it, and nothing is answered. Set
`replies.enabled: true` to apply a trust policy and have Scrutare answer in the
thread. The setting is accepted for `strategy: iterative` only. `panel` and
`debate` keep no finding pool or round bound between invocations, so the
configuration is refused for them instead of running a second reassessment
path. Enabling it changes the configuration, so an existing history is refused
like any other configuration change; start with a fresh one.

```yaml
strategy: iterative
replies:
  enabled: true
  trust: write      # or: anyone
```

A reply never decides anything. A trusted reply selects the hunk of the upheld
finding it answers for one more review wave. The wave reads the pool and the
discussion as untrusted evidence, and the code derives the disposition from what
that wave yields:

- The finding is produced again: `upheld`. It keeps blocking.
- The unchanged hunk no longer produces it: `withdrawn`. It leaves the verdict.
- The changed hunk no longer produces it: `fixed`.

Reply text cannot change configuration, budgets, scope or the verdict. It
reaches the model as JSON data after an explicit notice, bounded per wave to 16
comments (trusted first, then newest) of 2000 characters each, and is never
copied into anything Scrutare posts.

**Who may contest.** With `trust: write`, only an account whose verified
repository permission is `admin` or `write` triggers a reassessment. The
permission is read at capture with the review's own token and retained in
`reply-permissions.json`; a lookup that fails counts as untrusted, and at most
32 accounts are looked up per capture, newest comments first. `author_association`
is not used, because `MEMBER` and `COLLABORATOR` do not imply write access.
`trust: anyone` admits every account GitHub types as `User`. Bots and Scrutare's
own responses never trigger a reassessment under either policy. An untrusted
reply triggers nothing and spends nothing. It stays unconsumed until an ordinary
review wave covers its file, where it is supplied once as evidence marked
untrusted.

**Which thread is Scrutare's.** A thread counts only when its root comment
belongs to a review whose delivery an earlier run of this pull request
confirmed in `posting.json`. Review text and markers can be copied, so they are
not consulted. A review whose POST stayed uncertain defines no thread: a reply
there can still contest by file and line, but is not answered.

**The response.** Each thread with new trusted replies gets one response naming
the disposition, the outcome, the head SHA and the round, followed by the
current finding quoted as untrusted text:

| Outcome | Meaning |
| --- | --- |
| `reassessed` | A wave reviewed the hunk again with the replies as evidence. |
| `incomplete` | The wave did not cover the finding completely, so it stands. |
| `exhausted` | `rounds.max` is used up. No session starts; the review escalates under the existing policy. |
| `recorded` | The finding was already fixed or withdrawn; its recorded disposition is reported without a wave. |

Several replies in one thread before a run are one contest, one wave and one
response. A reply is identified by its ID and text, so a redelivered or reacted-to
comment is not new evidence and an edit is, once. Reassessments use the same
ledger and `rounds.max` as pushes: a pull request can never spend more than
`rounds.max` waves, however many replies arrive.

The same run posts its review as usual, which is how GitHub sees a re-derived
verdict. At a head that already carries Scrutare's threads, findings whose
inline comment would be byte-identical are counted in the summary but not posted
as a second thread.

`scrutare review --replies-only` is for callers triggered by a reply rather than
a push. When the head is unchanged and no trusted reply contests anything, it
posts no review and reports `status: unchanged`; a new head or a trusted contest
is reviewed as usual. Ordinary invocations, including the Action's, handle
replies without the flag.

Retained for replay: the raw `review_comments.json`, `reply-permissions.json`,
and `replies.json` with every new comment's author, trust, text and status, each
response's original finding and disposition before the wave, and the result
after it. The reassessment's sessions are in `iterative-round/`, the pool in
`iterative.json`, and delivery in `reply-posting.json`. See
[posting](posting.md#threaded-responses) for delivery and recovery.

Installed acceptance in `tests/test_installed_iterative.py` builds the candidate
wheel, installs it outside the checkout, and executes the separately installed
nare release with an offline vendor fixture. Four invocations prove initial
findings, an unchanged push without sessions, a reviewed fix with retained
history, and bound exhaustion without starting additional sessions. A second
case replies without a push: an untrusted reply that triggers nothing, a
maintainer's prompt-injection reply that leaves the finding blocking and gets
one threaded response, a redelivery that changes nothing, and an edit whose
reassessment withdraws the finding and approves.
