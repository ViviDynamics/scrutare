# Running Scrutare on a self-hosted model

Scrutare can review on any OpenAI-compatible backend, for example a model served
by vLLM behind a LiteLLM gateway. Every model call still goes through nare, on
its openai rail. This guide covers the configuration for that rail: the
`self-hosted@1` profile, each value it sets and the evidence behind it, the
route preflight, the App's model-origin allowlist, and what is not qualified.

## Status: unqualified

No self-hosted model has passed the [#51](https://github.com/ViviDynamics/scrutare/issues/51)
qualification gate. `onprem/glm-5.3-flash` failed every frozen schedule (g2, s1,
s2, c1, g3, g4 and g5), and the hosted reference passes. The profile is the
best-evidenced starting configuration from those campaigns, not a promotion:
no default changes, and a review on a self-hosted rail is not known to find
what a hosted review finds. Treat its verdicts as advisory (`github.post_mode:
comment`) until a model qualifies. Progress is tracked in
[#137](https://github.com/ViviDynamics/scrutare/issues/137).

## Configuration

```yaml
profile: self-hosted@1
models:
  default:
    provider: openai
    base_url: https://litellm.example/v1
    model: onprem/<model>
github:
  post_mode: comment
```

Set `OPENAI_API_KEY` to the gateway's key. To see what the profile resolved,
and which values came from the profile and which from the file, run:

```sh
scrutare config show --config scrutare.yaml
```

It reads the file and prints JSON: `profile`, `options` (each option the
profile covers, on each rail, with its `value` and `source`) and the canonical
`config` a review would record. `source` is `profile` when the profile filled
the value, `file` when the file sets it, and `inactive` when the value does not
apply to this review's shape. The command is read-only and offline: it starts
no nare process and sends nothing.

### How the profile resolves

- `profile` is top-level and names a version: `self-hosted@1`. A bare name, an
  unknown name and an unknown version are refused at load.
- It requires `provider: openai` on every rail, default and overrides. An
  anthropic rail is refused with
  `profile: self-hosted@1 requires provider openai on every rail; models.default
  is anthropic` (or the override's path). Its rail values include
  `proxy_num_retries`, which nare offers on the openai rail only, and
  `findings.protocol_correction`, which needs every rail openai.
- It fills a field only where the file has no key for it. Any field the file
  sets wins, `null` and `false` included, so `max_output_tokens: null` turns the
  cap off. Overrides inherit the filled default-rail fields like any other
  field, and an override that sets one keeps its own.
- Some values apply only to some review shapes (below). On a legacy-evidence
  review those fields keep their defaults, so adding the one `profile` line to
  an existing openai configuration is enough.
- A version's values never change. A new value is a new version in a reviewed
  change, so a repository that names `self-hosted@1` keeps its behavior.

### Values of `self-hosted@1`

The campaign names refer to the frozen schedules summarized in
[evaluation](evaluation.md) and the plans under [plans](plans/).

| Field | Value | Applies | Evidence |
| --- | --- | --- | --- |
| `models.default.structured_output` | `json_schema` | always | In s1 no session failed the findings schema (nine had in g2), and none of the 31 constrained corrections failed it. Every later schedule kept it. The other encodings have not been screened, and ties keep `json_schema` ([plan](plans/2026-11-20-openai-answer-encodings.md)). |
| `models.default.max_output_tokens` | `20000` | always | Sized in c1 from s2's largest completed request (12,387 tokens), after one s2 assessor request generated for 30 minutes without an answer. No c1 session timed out. Kept in g3, g4 and g5; two g4 requests reached it ([c1](plans/2026-10-22-glm-capped-c1.md), [g4](plans/2026-10-28-glm-g4-qualification.md)). |
| `models.default.proxy_num_retries` | `0` | always | The maintainer chose request-specific zero proxy retries, and the proxy honors the field ([plan](plans/2026-10-29-per-request-proxy-retries.md)). A retry inside the proxy is invisible to nare's accounting, and the operational rule A requires every provider request to be attributed. |
| `models.preflight.route` | `true` | always | Self-hosted runs died on gateway 502s, DNS timeouts and dropped connections that a review or campaign found by spending itself ([plan](plans/2026-11-20-route-preflight.md)), and the November gate attempts halted mostly on infrastructure faults (#137). The check sends no prompt and costs no tokens. |
| `context.line_counts` | `true` | `findings.evidence: v2` | On from g3 ([plan](plans/2026-10-25-glm-g3-qualification.md)), against citations past a file's end (one in s2, two in c1). The #91 measurement saw no citation refusal with or without it, so its effect is unmeasured. |
| `findings.protocol_correction` | `true` | evidence v2 with `citation_check: post_session` | On in g4 and g5 ([plan](plans/2026-10-28-glm-g4-qualification.md)). g4 offered no correction, so its effect is unmeasured. It owns the single correction opportunity, so `citation_correction` (also on in those schedules) changes nothing beside it and is not set. |
| `findings.assessment.tokens` | `50000` | `findings.assessment.enabled: true` | In s1 every assessor overran its 20,000-token reservation; s2 sized 50,000 from s1, and every later schedule kept it. One g4 assessor still exceeded it by 8,769 tokens. |

A review that chooses `findings.citation_check: session` gets no
`protocol_correction` from the profile, because the in-session check excludes
the correction turns.

### Left at their defaults

The profile does not set an option the evidence does not support. Set any of
these in the file when you have your own reason:

| Option | Why it is not in the profile |
| --- | --- |
| [`request_retries`, `request_retry_seconds`](config.md#request-retries-opt-in) | No schedule ran with them (they arrived after the campaigns), and g5 ran with transient retries off under rule A. With retries on, usage is a lower bound. No count has been measured. |
| [`models.capacity`](config.md#capacity-queueing-opt-in) | The documented values are nare's defaults and have not been measured for reviews. |
| [`models.preflight.request`](config.md#route-preflight-opt-in) | A billed model call. Since nare 2026.11.108 it reports a refused encoding as `encoding_refused` and gateway, credential and route failures by their own reasons, so it is a useful check on a new backend, but `self-hosted@1` leaves it to the operator. `timeout_seconds` stays 30. |
| [`cache_breakpoints`](config.md#prompt-cache-breakpoints-opt-in) | vLLM caches prefixes without a marker, and a server that does not know the field may refuse it. |
| [`effort`](config.md#reasoning-effort-opt-in) | Never measured on a self-hosted model. |
| [`findings.citation_check: session`](config.md#citation-check-in-the-session-opt-in) | Unmeasured; the default stays `post_session` until the screen in its [plan](plans/2026-11-20-session-citation-check.md). |
| [Phase overrun fallback and discussion allowance](config.md#phase-allowances-and-overrun-fallback-opt-in-unmeasured) | Never measured. |

Two campaign settings are not configuration, so no profile can carry them:

- **Session timeout.** The campaigns ran 600 seconds per session (g2, s1) and
  then 1800 (s2 onward). The review CLI runs 600 and has no option for it. At
  the 15 to 19 output tokens per second measured before g2
  ([plan](plans/2026-10-14-glm-qualification.md)), one request at the 20,000
  cap takes longer than 600 seconds, so a slow model can time out before the cap
  ends its request. Every review now records its
  [timing](config.md#review-timing), which is the evidence a session timeout
  and the App's job deadline should be sized from
  ([#135](https://github.com/ViviDynamics/scrutare/issues/135)). Since nare
  2026.11.109 reports per-request timing, a streamed session's record also
  carries its time to first token and decode rate, so the 15 to 19 tokens per
  second above can be measured on reviews rather than assumed. No live report
  exists yet: it needs real reviews on the self-hosted rail.
- **nare `--max-cutoffs`.** Scrutare pins 0, so a reply cut off at the cap or
  by repetition ends its session. c1 lost one stream to a repetition abort and
  g4 two requests to the cap, but nare's cutoff correction has not been
  measured on reviews and has no rail option yet.

### Records and replay

`config.json` records `profile: self-hosted@1` and every field the profile
covers, explicitly, `null` and `false` included, wherever its section is
recorded. `config.yaml` keeps the file's bytes. Both resolve to the same
settings, which a review checks before it starts, and [replay](replay.md)
audits each session's flags against them as for hand-written values. A
configuration without `profile` records exactly what it did before, and its
sessions send the same argv.

## Route preflight

The profile turns on `models.preflight.route`: before a review captures
anything, Scrutare sends `GET https://litellm.example/v1/models` with the rail's
key and requires the configured model in the list. A gateway that is down, a
key that is refused or a model the gateway no longer serves ends the command
with `status: not_started` and a typed reason, before any budget is spent or
anything is posted. A gateway that routes the model through a wildcard and does
not list it fails with `model_missing`; set `models.preflight.route: false` for
such a route. See [route preflight](config.md#route-preflight-opt-in) for every
reason.

## Under the GitHub App

The App reads `scrutare.yaml` from the default branch, so a profile takes effect
once merged. The operator's `SCRUTARE_APP_MODEL_ORIGINS` must list the
gateway's origin exactly, for example `https://litellm.example` (with its port
if it has one); an HTTP origin is always refused, and a route outside the
allowlist is never asked, by the preflight or by a review. The service
environment provides `OPENAI_API_KEY`. The App runs the preflight itself,
records each result on the job, and retries a failed one with a backoff.

A self-hosted review takes longer than a hosted one. The App's job deadline,
`SCRUTARE_APP_JOB_TIMEOUT` (default 3300 seconds, at most 21600), is an
operator setting, not configuration, so the profile does not change it. Size it
from the review times the job records carry. See
[the App guide](app.md#job-deadline-and-token-refresh).

## What remains unqualified

- The #51 gate: no self-hosted model has passed it, and the profile changes
  none of its criteria.
- The value of `line_counts` and `protocol_correction`: both ran in the latest
  schedules, and neither has fired often enough to measure.
- The answer encoding: `json_object` and `tool_call` have not been screened
  against `json_schema`.
- The options left at their defaults above, the session timeout and the cutoff
  correction.
