# Self-hosted rail profile

Issue #133, part of #137.

## Goal and design

Running Scrutare on a self-hosted model means assembling about a dozen opt-in
options by hand from `docs/config.md`. The GLM campaigns found values for some
of them, but those values live in frozen plans and evaluation notes, not in
anything a repository can name. This change ships them as one named, versioned
profile:

```yaml
profile: self-hosted@1
models:
  default:
    provider: openai
    base_url: https://litellm.example/v1
    model: onprem/<model>
```

The rules of the earlier plans stay: capabilities are read from `nare
contract`, new behavior is opt-in, and a configuration without a profile sends
the same argv and request bytes and records the same artifacts as before.

No live model call, credential, holdout content or deployment is part of this
work, and this repository's own `scrutare.yaml` is not switched: the App reads
it from the default branch, so switching it would change deployed reviews. A
test proves the one-line switch loads.

### Placement: a top-level `profile`

The issue sketches `models.default.profile`. The profile is top-level instead:

1. It fills fields outside the rail (`models.preflight`, `context.line_counts`,
   `findings.protocol_correction`, `findings.assessment.tokens`). A key under
   `models.default` that changes `findings` would be surprising.
2. It is review-wide. Overrides inherit the default rail's fields as before, so
   one key covers every rail, and `findings.protocol_correction` already
   requires every rail to be openai.
3. A per-rail profile would allow two rails with different profiles, which
   nothing needs.

The value is `NAME@VERSION`. A bare name is refused, so every repository pins a
version, and a change to the values is a new version in a reviewed change.

### Resolution

1. The file is parsed and validated without the profile, so its own errors are
   reported as before.
2. The profile is refused unless every rail (default and overrides) is
   `provider: openai`. Its rail values include `proxy_num_retries`, which nare
   offers on the openai rail only, and `protocol_correction`, which needs every
   rail openai. The profile is openai-only rather than partly applied.
3. Each value the profile covers is written where the file has no key for it,
   and only when its condition holds on the file's own settings. A key the file
   sets, `null` and `false` included, is kept. Overrides then inherit the filled
   default rail field like any other, and an override that sets the field keeps
   its own.
4. The result is parsed again. An error on a filled field names the profile.

Conditions keep a profile valid on any review shape, so this repository's
legacy-evidence file switches with one line:

| Value | Condition |
| --- | --- |
| rail fields, `models.preflight.route` | always |
| `context.line_counts` | `findings.evidence: v2` |
| `findings.protocol_correction` | evidence v2 and `citation_check` left at `post_session` |
| `findings.assessment.tokens` | `findings.assessment.enabled: true` |

`citation_check: session` excludes the correction turns, so a file that
chooses it gets no `protocol_correction` from the profile.

### Values of `self-hosted@1`

Every value is one the campaigns ran, with the evidence that put it there.

| Field | Value | Evidence |
| --- | --- | --- |
| `models.default.structured_output` | `json_schema` | s1 (`docs/evaluation.md`): no session failed the findings schema (nine had in g2), and none of 31 constrained corrections failed it. Kept in s2, c1, g3, g4 and g5. No encoding screen has run (2026-11-20 encodings plan: ties keep `json_schema`). |
| `models.default.max_output_tokens` | `20000` | c1 (`docs/evaluation.md`, 2026-10-22 plan): sized from s2's largest completed request, 12,387 tokens, after one s2 assessor request generated for 30 minutes; no c1 session timed out. Kept in g3, g4 (two requests hit it) and g5. |
| `models.default.proxy_num_retries` | `0` | 2026-10-29 plan (#106): the maintainer chose request-specific zero proxy retries, and the proxy honors the field. Retries inside the proxy are invisible to nare's accounting, and rule A requires every provider request attributed. |
| `models.preflight.route` | `true` | 2026-11-20 route preflight plan (#132): self-hosted runs died on gateway 502s, DNS timeouts and dropped connections found by spending a review. #137: the November gate attempts halted mostly on infrastructure faults. The route check sends no prompt and costs no tokens. |
| `context.line_counts` | `true` | g3 onward (2026-10-25 plan), against citations past a file's end (one in s2, two in c1). The #91 measurement saw no citation refusal with or without it, so its effect is unmeasured. |
| `findings.protocol_correction` | `true` | g4 and g5 (2026-10-28 and 2026-10-29 plans). g4 offered no correction, so its effect is unmeasured. It subsumes `citation_correction` (protocol owns the one opportunity), so that field is not set. |
| `findings.assessment.tokens` | `50000` | s1: every assessor overran its 20,000-token reservation; s2 sized 50,000 from s1, kept through g5. One g4 assessor still exceeded it by 8,769 tokens. |

### Left at their defaults, and why

| Option | Reason |
| --- | --- |
| `request_retries`, `request_retry_seconds` | No schedule ran with them (they arrived on 2026-11-11, after the campaigns); g5 ran with transient retries off under rule A. With retries on, usage becomes a lower bound. No count is measured. |
| `models.capacity` | The documented values are nare's defaults and have not been measured for reviews. No capacity refusal is in the public evidence. |
| `models.preflight.request`, `timeout_seconds` | The request check is a billed model call, and `request_failed` cannot yet tell an encoding refusal from a gateway failure (nare#314). No evidence for a bound other than 30 seconds. |
| `cache_breakpoints` | vLLM caches prefixes without a marker, and a server that does not know the field may refuse it. |
| `effort` | Never measured on a self-hosted model. |
| `findings.citation_check`, `max_corrections`, `cited_reads` | Unmeasured; the default stays `post_session` until the screen in the 2026-11-20 plan. |
| `findings.*.overrun`, `findings.discussion.tokens` | Never measured (`docs/config.md`). |

Two campaign settings are not configuration, so no profile can carry them:

- The session timeout. The campaigns ran 600 seconds (g2, s1) and then 1800
  (s2 onward); the review CLI runs 600 and has no option for it. At the 15 to
  19 output tokens per second g2 measured, one request at the 20,000 cap takes
  longer than 600 seconds. #135 covers the deadline.
- nare `--max-cutoffs`. Scrutare pins 0. c1 lost one stream to a repetition
  abort and g4 two requests to the cap, but nare's cutoff correction has not
  been measured on reviews and has no rail option yet.

### Recording

`config.to_dict()` (so `config.json`, every configuration hash taken from it
and evaluation snapshots) records `profile: self-hosted@1` and every field the profile
covers explicitly, `null` or `false` included, wherever its section is
recorded. Parsing the record again fills nothing and yields equal settings,
which `review_inputs` already checks, and `config.yaml` keeps the file's bytes.
Because a version's values never change, replay of either file resolves the
same values. Without a profile nothing is recorded differently.

### `scrutare config show`

A read-only, offline subcommand: it reads the file, resolves it, and prints one
JSON document with `profile`, `options` (each covered path, for each rail,
with its resolved value and `source`: `profile`, `file`, or `inactive` when the
condition does not hold) and the canonical `config`. No nare, GitHub or network.

## Task 1: Profile resolution

Files: src/scrutare/profiles.py, src/scrutare/config.py,
tests/test_self_hosted_profile.py.

- [x] Failing tests: each value filled field by field; each explicit override
  kept (including `null` and `false`), on the default rail and an override;
  conditions; unknown name, unknown version and malformed value refused without
  echoing the value; anthropic default or override refused; an error on a
  filled field names the profile.
- [x] The profile table and resolution.

## Task 2: Recording and replay

- [x] Failing tests: no profile records the same bytes and argv as before;
  profiled `to_dict` records the profile and explicit fields and round-trips,
  overrides included; the profiled argv equals the explicit configuration's;
  replay of a profiled run's `config.json` and `config.yaml`.
- [x] Explicit recording in `ModelSettings.record` and `ReviewConfig.to_dict`.

## Task 3: `scrutare config show`

Files: src/scrutare/interfaces/cli.py, tests/test_self_hosted_profile.py.

- [x] Failing tests: sources for profile, file and inactive; no profile; an
  invalid file exits 1 with the error on stderr and nothing on stdout.

## Task 4: Documentation

- [x] `docs/self-hosted.md`; the `profile` row and section in `docs/config.md`;
  `docs/cli.md`; README link.

## Task 5: Verification

- [x] Targeted tests on Python 3.10 and 3.14, `ruff check`, strict `mypy`.
- [ ] Full lanes on Linux CI.
- [ ] Disclosure guard on the pull request body.

## What stays unchanged

- A file without `profile` parses, records and launches exactly as before.
- No default changes. The profile is not a promotion: the #51 gate has not
  passed for any self-hosted model.
