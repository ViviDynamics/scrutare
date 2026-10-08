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
finding. A comment without a line matches active findings on that path. Existing
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

Installed acceptance in `tests/test_installed_iterative.py` builds the candidate
wheel, installs it outside the checkout, and executes the separately installed
nare release with an offline vendor fixture. Four invocations prove initial
findings, an unchanged push without sessions, a reviewed fix with retained
history, and bound exhaustion without starting additional sessions.
