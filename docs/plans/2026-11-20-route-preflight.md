# Route preflight

## Goal and design

Self-hosted runs keep dying on infrastructure rather than on the model: a
gateway 502, a DNS timeout inside the cluster, a dropped connection. Each was
found by spending a review or a campaign (#132, part of #137). This plan adds
one preflight, shared by the CLI review path, the App worker and the evaluation
launcher, that checks the configured route before anything is claimed, charged
or posted.

The rules of the earlier plans stay: capabilities are read from `nare
contract`, new behavior is opt-in, and a default-configuration review sends the
same argv and request bytes as before and derives its verdict the same way.

No live model call, credential, holdout content or deployment is part of this
work. Every proof runs offline against a loopback HTTP route and the installed
2026.11.107 console.

### Why the route check is opt-in too

A `GET /models` is not a model request: it sends no prompt, costs no tokens and
changes no byte of what a review later sends, so it could arguably be on by
default. It is still opt-in, for three reasons:

1. It is a new outbound request carrying the rail's credential. The house rule
   is that a default-configuration review sends what it sent before, and that
   covers every request, not only model requests.
2. It can turn a review that works today into one that does not start. A proxy
   that routes a model through a wildcard, or a route without a `/models`
   endpoint, answers chat completions but does not list the model.
3. The anthropic rail has no equivalent check here (below), so a default would
   cover only some rails.

### Checks

`models.preflight` has `route`, `request` and `timeout_seconds` (default 30,
at most 300). Both checks off, or no block, is never recorded, so the canonical
configuration keeps its bytes.

- `route`: on each distinct openai route, `GET {base_url}/models`, rooted where
  nare's openai transport roots `/chat/completions` (`https://api.openai.com/v1`
  without `base_url`). The configured model must be among `data[].id`. The
  credential travels in the `Authorization` header only. Redirects are refused,
  so it never crosses an origin, and ambient proxy variables are ignored, since
  nare's sessions run without them.
- The anthropic rail is skipped with a recorded reason. Its route is the
  vendor's own API, never a self-hosted one, and its model list lives at a
  different root with different headers. `route: true` needs at least one
  openai rail.
- `request`: one tiny request per distinct rail through nare, `--tools none`,
  a one-field schema, `--max-turns 2`, and the rail's own argv and options
  (`_rail_argv` and `_rail_options`, so structured output, the output cap,
  retries, effort and `--stream` match a review session). Capacity flags are
  not sent: a preflight should fail fast, not queue. A tool-free run is
  constrained on every turn, so the rail's answer encoding is exercised. This
  is a model call, so it is opt-in. Its usage is recorded in the report and is
  not charged to any review budget.

### Reasons

`origin_refused`, `credential_missing`, `route_unreachable`, `gateway_error`
(5xx), `credential_refused` (401, 403), `route_refused` (other status or a
redirect), `route_invalid`, `model_missing`, `runtime_refused`,
`request_failed`, `request_timeout`, `answer_invalid`.

`encoding_refused` is not produced yet. When a backend refuses a constrained
request, nare ends the run with `stop_reason` null and says which status it
was only in the `error` text, which its contract tells callers not to parse.
Filed as ViviDynamics/nare#314; until then that case is `request_failed`.
No error text or remote body is recorded.

### Where it runs

- CLI: after configuration and runtime checks, before any GitHub access or
  capture. A failure prints a `not_started` document and exits 4.
- Evaluation `run`: before the output directory is created (the job's
  one-run claim). A failure prints the same document and exits 4. A pass is
  recorded under `preflight` in `snapshot.json`.
- App worker: after the model origin check and before the review CLI, with the
  operator's origin allowlist, so a credential only reaches allowlisted
  origins. Every result is appended to the job's `preflight` list. A failure
  defers the job (`not_before`) with a doubling backoff from
  `SCRUTARE_APP_PREFLIGHT_BACKOFF_SECONDS` (default 60, delay capped at one
  hour) until `SCRUTARE_APP_PREFLIGHT_ATTEMPTS` (default 4) have failed, then
  ends it `not_started` with `delivery: not_sent`. A pass runs the CLI with
  `--skip-preflight`, so the check is not repeated.

## Task 1: Configuration

Files: config.py, tests/test_preflight.py, docs/config.md.

- [x] `PreflightSettings(route, request, timeout_seconds)`, booleans checked,
  timeout positive, finite, at most 300 and only with a check on.
- [x] Off is never recorded; on records every value, so it round-trips.
- [x] `route: true` needs an openai rail.

## Task 2: Shared preflight

Files: engine/preflight.py, tests/test_preflight.py.

- [x] Route check with a typed reason for every failure, against a loopback
  fake route: pass, each HTTP class, unreadable list, redirect (the target sees
  nothing), closed port, slow route within the bound, missing credential,
  origin outside the allowlist (nothing sent), ambient proxy ignored, anthropic
  skipped, one request per distinct route, first failure stops.
- [x] Request check: argv shape everywhere (tool-free, rail options, no
  credential, no capacity flags); with the installed console, a pass with the
  rail's encoding, `request_failed` on a refused request with no remote body
  recorded, `answer_invalid`, `request_timeout`, and `runtime_refused` with no
  request sent.

## Task 3: Callers

Files: interfaces/cli.py, evaluation/runner.py, evaluation/__main__.py,
interfaces/app.py, interfaces/app_worker.py, interfaces/app_server.py, docs.

- [x] CLI exit 4 with the `not_started` document and no run directory or
  GitHub access; `--skip-preflight`.
- [x] Evaluation: no output directory on failure; exit 4 from the command.
- [x] App: `not_before` and `preflight` columns added to an existing store,
  `claim` honors `not_before`, `defer`, `record_preflight`, terminal
  `not_started`; bounded settings and environment variables.

## Task 4: Verification

- [x] Targeted tests on Python 3.10 and 3.14, `ruff check`, strict `mypy`.
- [ ] Full lanes on Linux CI.
- [ ] Disclosure guard on the pull request body.

## What stays unchanged

- Without `models.preflight`, or with both checks off: no request, the same
  argv, the same canonical configuration, the same run artifacts, the same App
  job flow and the same CLI exits.
- Replay: the preflight is not part of a review run, so nothing is audited.
- The quality-gate `launch.py` of #121 is not in this repository (it lives with
  the private campaign material), so its launch preflight is not here. The
  evaluation `run` command is the public launcher and is covered.

## Open questions

1. A passed CLI preflight is not written into the run, because it runs before
   the run directory exists. The App records it on the job and an evaluation in
   its snapshot. Recording it in `result.json` would change that document only
   when the block is on.
2. The request check's tokens are real spend outside the review budget. Its
   usage is in the report; whether it should count against a budget is open.
3. `encoding_refused` waits on nare#314.
