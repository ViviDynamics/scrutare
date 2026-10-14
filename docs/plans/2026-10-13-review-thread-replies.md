# Answer replies on Scrutare review threads and reassess the contested finding

Issue #69

## Scope
In: an opt-in `replies` setting for `strategy: iterative`; a trust policy deciding
which inline comments may contest a finding; a mapping from a reply to the Scrutare
thread and finding it answers; one threaded response per contest with a
code-derived disposition; retained `replies.json` and `reply-posting.json`
evidence; a `--replies-only` CLI mode; App admission, coalescing, worker handling
and redelivery for signed `pull_request_review_comment` events; documentation and
threat model.

Out: panel and debate support. Neither keeps a cross-invocation finding pool, a
contest ledger or a shared round bound, so supporting them would mean a second
reassessment path. `replies.enabled` is refused for them at configuration load.
Live acceptance under the deployed App is out: it needs #18's deployment and the
maintainer adding the review-comment event subscription to the GitHub App.

## Assumptions
- The existing iterative contest machinery decides every disposition. A reply
  only selects which upheld finding's hunk is reviewed again; the finding stays
  upheld when the reassessment yields it and is withdrawn or fixed only when it
  does not.
- Trust is a verified repository permission (`admin` or `write`) read at capture
  with the review's own token and retained in the run. `author_association` is
  not used: `MEMBER` and `COLLABORATOR` do not imply write access. A failed
  lookup is untrusted. `replies.trust: anyone` admits every non-bot account.
- "Is this thread mine?" is answered from confirmed posting journals of this
  pull request's earlier runs (review ID and author login), never from `/user`
  and never from text an account can copy.
- A response is recorded in the pull request's private history in the same
  atomic save that consumes its contest, then delivered through a journal that
  persists `sending` before the POST. An uncertain POST is reconciled by marker
  and author on a later capture and is never resent.
- With the setting off, no new artifact, state key, prompt text or payload byte
  appears.

## Tasks
- [x] 1. Config: `replies.enabled` and `replies.trust`, iterative only, omitted
  from canonical configuration when default: `tests/test_config.py`.
- [x] 2. Capture: permission lookups retained as `reply-permissions.json` when
  the policy needs them: `tests/test_github.py`, `tests/test_ingestion.py`.
- [x] 3. Engine: trust policy, thread mapping, contests, dispositions, bounded
  discussion evidence, `replies.json`, byte-identical when off:
  `tests/test_iterative_replies.py`.
- [x] 4. Poster: threaded response rendering, exactly-once journal, no duplicate
  inline threads at an already reviewed head: `tests/test_post_replies.py`.
- [x] 5. Review run and CLI: responses delivered after the review, the
  `--replies-only` no-op, result document: `tests/test_review_run.py`,
  `tests/test_cli.py`.
- [x] 6. App: signed admission, idempotency on signed content, per-PR
  coalescing, worker gate, delivery field, redelivery: `tests/test_app.py`,
  `tests/test_app_worker.py`.
- [x] 7. Installed acceptance: a reply with no new push through the installed
  wheel and nare: `tests/test_installed_iterative.py`.
- [x] 8. Documentation and threat model.
