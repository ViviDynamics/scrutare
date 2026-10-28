# Configuration reference

The approved configuration is one YAML mapping in `scrutare.yaml`. The
configuration loader validates it without calling a model or GitHub. Model
identifiers are always explicit: there is no default billable model.

A minimal valid file is:

```yaml
models:
  default:
    model: your-model
```

All other fields take the defaults below. Each section must be a mapping,
collections must be YAML lists, and strings must be nonempty. Unknown fields,
duplicate keys at any level, nonstring field keys, unsafe YAML tags, malformed
YAML, recursive aliases, and nonmapping documents fail with `ConfigError`.
Sections cannot be `null`. YAML booleans and floats do not count as integers.
Errors identify a field path or YAML location without echoing field values.

## Fields and defaults

| Field | Accepted values | Default |
| --- | --- | --- |
| `strategy` | `panel`, `iterative`, `debate` | `panel` |
| `rounds.max` | Positive integer | `3` |
| `personas` | Nonempty list of built-in names or inline definitions; unique names | `senior-dev`, `junior-dev`, `security`, `devops` |
| `budgets.per_persona_tokens` | Positive integer | `100000` |
| `budgets.review_max_tokens` | Positive integer | `500000` |
| `models.default.provider` | `anthropic`, `openai` | `anthropic` |
| `models.default.base_url` | HTTP or HTTPS URL with a host, or `null` | `null` |
| `models.default.model` | Explicit nonempty string | Required |
| `models.default.structured_output` | `json_schema` (requires `provider: openai`), or `null` | `null` (off) |
| `models.default.max_output_tokens` | Positive integer, or `null` | `null` (off) |
| `models.overrides` | Mapping from a configured persona name to partial model settings | `{}` |
| `verdict.blocking_categories` | List drawn from the fixed vocabulary | `correctness`, `security`, `regression` |
| `verdict.advisory_categories` | List drawn from the fixed vocabulary | `style`, `consistency`, `docs` |
| `github.post_mode` | `review` (blocking review), `comment` (never blocks) | `review` |
| `github.human_reviewers` | List of nonempty reviewer-name strings for escalation | `[]` |
| `github.paths.include` | List of nonempty glob strings | `[]` |
| `github.paths.exclude` | List of nonempty glob strings | `["docs/**", "*.md"]` |
| `findings.protocol_correction` | Boolean; `true` requires v2 evidence and OpenAI rails; takes precedence over citation correction | `false` (off) |
| `findings.citation_correction` | Boolean; `true` requires `findings.evidence: v2` and `provider: openai` on every rail | `false` (off) |
| `replies.enabled` | Boolean; `true` requires `strategy: iterative` | `false` |
| `replies.trust` | `write` (verified write access), `anyone` (any non-bot account) | `write` |

Validation only accepts settings here. It does not execute strategies, enforce
budgets, filter paths, derive a verdict, or post reviews. Configured capture uses
the typed path settings to prepare a filtered reviewer view. See the
[path filters guide](path-filters.md) for the glob dialect, rename exclusions,
artifact format, and persona input API. Configuration has no API-key,
credential, retry, or arbitrary extension fields.

## Personas

The built-in names are `senior-dev`, `junior-dev`, `security`, and `devops`.
Each list entry can instead define a persona with exactly two fields:

```yaml
personas:
  - senior-dev
  - name: accessibility
    system_prompt: |
      Review the changed interface for accessibility issues.
```

An inline `name` is a lowercase slug beginning with a letter. It contains
letters and digits separated by single hyphens, matching
`[a-z][a-z0-9]*(?:-[a-z0-9]+)*`. `system_prompt` is a nonempty string;
its whitespace is preserved. An inline definition can replace a built-in
name, but every name must occur only once in the configured panel.
An undefined custom name cannot be used as a bare string. See the
[persona authoring guide](writing-personas.md) for the built-in perspectives,
complete custom and replacement examples, prompt guidance, and registry API.

## Model inheritance

The default rail resolves omitted `provider` to `anthropic` and omitted
`base_url` to `null`. Its `model` must be supplied even when every persona has
an override. The `openai` provider supports compatible gateways and local
endpoints. HTTP URLs such as `http://localhost:8000/v1` and
`http://127.0.0.1:11434` are accepted alongside HTTPS URLs.

An override can supply any subset of `provider`, `base_url`, and `model`:

```yaml
models:
  default:
    provider: openai
    base_url: http://localhost:8000/v1
    model: your-model
  overrides:
    security:
      model: your-security-model
    devops:
      base_url: null
    junior-dev: {}
```

Omitted fields inherit the review-wide default. Explicit `base_url: null`
clears an inherited URL. Empty override mappings inherit the whole default.
`provider` and `model` cannot be `null` or empty, and a URL must have a host
and a valid optional port. Override keys must name personas present in the
configured panel, including inline personas.

## Schema-constrained output (opt-in)

Self-hosted models can end a session with a final answer that is not valid
JSON for the required schema. A rail can ask the backend to enforce the schema
at decode time instead:

```yaml
models:
  default:
    provider: openai
    base_url: https://litellm.example/v1
    model: onprem/glm-5.3-flash
    structured_output: json_schema
```

Every nare session on that rail (discovery findings, assessment, discussion
positions and chair selections, anchor corrections) is launched with nare's
`--structured-output json_schema`. nare then sends the OpenAI-compatible
`response_format` `json_schema` with the session's own output schema on the
turns where it can: the single schema correction turn, after a final answer
fails the schema, which is sent without tools. Tool-using turns stay
unconstrained, because a constrained request cannot call `read` (measured on
vLLM behind LiteLLM: the model invents an answer instead). A valid first answer
is accepted as before. The setting is a transport aid only: every answer is
still decoded by the same findings, assessment, discussion, identity and
citation validation, and an answer that fails it fails the session as before.

The setting is off by default and inherited by overrides like the other rail
fields; an override can set it alone or clear an inherited value with `null`.
It is refused at configuration load for a rail whose provider is not `openai`,
and refused before any session launches when the nare runtime does not
advertise the capability (nare 2026.10.12 or newer). Use it only with a
backend that enforces `response_format` json_schema, such as vLLM; an
OpenAI-compatible server that ignores the field answers unconstrained, and
validation still decides.

When no rail sets it, the canonical configuration, rail records,
`invocation.json` and `result.json` are byte-identical to earlier releases.
When any rail sets it, every rail in `config.to_dict()` records
`structured_output` (with `null` where it is off), each session's
`invocation.json` records it in `rail` and its argv, and `result.json` records
`{"mode": "json_schema", "constrained_turns": N}` (`null` when the session never
reported). [Replay](replay.md) reports `structured_output_unrecorded` or
`structured_output_result_disagrees` as invalid when a session's record does not
match a configured rail, and `structured_output_evidence_missing` as incomplete
when a launched session's record is missing or an opted-in run has no session
attempt.

## Per-request output token cap (opt-in)

Without a cap, a single model request can generate until the session timeout
kills it: nare's OpenAI transport sends no `max_tokens` unless it is asked to,
and an OpenAI-compatible server such as vLLM then bounds the request only by
the context window. A rail can cap every request instead:

```yaml
models:
  default:
    provider: openai
    base_url: https://litellm.example/v1
    model: onprem/glm-5.3-flash
    max_output_tokens: 12000
```

Every nare session on that rail is launched with `--max-tokens 12000`, and nare
sends `max_tokens: 12000` on every request of the session, tool turns included.
The session's token budget (`budgets`) still bounds the whole session; the cap
bounds each request.

A request cut off at the cap (OpenAI `finish_reason: length`, which nare reports
as stop reason `max_tokens`) fails its session with reason `output_cap`. When
the cut-off turn also exhausts the session's token budget, nare's after-turn
budget check replaces the terminal stop reason with `budget`; Scrutare then
reads the cut-off from nare's error event "the model's turn was cut off at
max_tokens" in the stream, and still records `output_cap` (with `reached: true`;
`stop_reason` stays nare's `budget`, and `overshoot_tokens` still measures the
overrun). nare
does not run tool calls from a cut-off turn, and Scrutare never uses any answer
from a capped session, including a schema-valid document an earlier tool turn
carried. The cut-off response reported its usage, so the session's tokens stay
in the review's shared accounting in full (`accounting_complete: true`). A
capped session is then handled like any other failed session of its phase
(for discovery or assessment, the review ends without a verdict). It is not a
budget overrun, so an opt-in overrun
`fallback` never applies to it. It is never a silent partial answer.

The setting is off by default, accepted on either provider, and inherited by
overrides like the other rail fields; an override can set it alone or clear an
inherited value with `null`. Every supported nare release (2026.10.0 and newer)
accepts `--max-tokens`, so no capability check is needed. With the cap off, a
backend's own length stop remains a `provider` failure, as before.

When no rail sets it, the canonical configuration, rail records,
`invocation.json` and `result.json` are byte-identical to earlier releases.
When any rail sets it, every rail in `config.to_dict()` and `fanout.json`
records `max_output_tokens` (with `null` where it is off), each session's
`invocation.json` records it in `rail` and its argv, and `result.json` records
`{"limit": N, "reached": true|false}`. [Replay](replay.md) audits these records.

## Citation correction turn (opt-in)

With [evidence v2](findings.md), one citation that does not match the captured
bytes fails a reviewer's whole session (`citation_validation`), or, for a
malformed revision or content hash, `protocol`. A review can instead give such
a session one bounded chance to fix it:

```yaml
context:
  enabled: true
findings:
  evidence: v2
  citation_correction: true
```

It requires `provider: openai` on every model rail. The correction resumes a
transcript that holds the session's tool calls with no tools offered; nare's
OpenAI rail then omits the tool list, which OpenAI-compatible servers such as
vLLM accept, while Anthropic's API refuses tool blocks in a request that defines
no tools. It applies only to discovery sessions (the reviewers of the initial
wave), and only to these deterministic defects:

- `line_range_outside_capture` on a captured file with at least one line. The
  correction prompt states the file's last citeable line, which is the bound the
  unchanged validator enforces.
- A `revision` that is not 40 lowercase hexadecimal characters, or a `sha256`
  that is not 64, including an empty one or a non-hexadecimal placeholder. The
  prompt states the expected form and where `repository-context.json` records
  the value; it never states the value. (A placeholder of the right shape, such
  as 64 zeros, is well-formed and wrong, so it is not offered.)

A correction is offered only when every refusal in the answer is one of these.
A well-formed but wrong revision or hash, an uncaptured path, a reversed range,
an empty file, a schema failure or any other refusal fails the session exactly
as before. To decide this, Scrutare checks the answer once with each named
defect hypothetically satisfied, and discards that check. The session must have
finished with an answer, with some of its token budget left (nare does not start
a turn once usage reaches the limit), and not been cut off at an
[output cap](#per-request-output-token-cap-opt-in).

The correction is one more turn of the same nare session: Scrutare resumes it
(`--resume`) with no tools (`--tools none`), `--max-turns 1`, the same
`--budget-tokens` and the rail's other settings. The model keeps everything it
read. nare's budget is cumulative, so the turn counts in the session's budget,
and its usage counts in the review's shared accounting. On a
[`structured_output: json_schema`](#schema-constrained-output-opt-in) rail,
nare constrains every turn of a run with no tools, so the correction answer is
decoded under its schema. The turn gets whatever is left of the session's
timeout.

The answer is citation-only, like anchor correction: exactly one entry per named
citation, with its `revision`, `sha256`, `start_line` and `end_line`; the prompt
spells out that shape, because nare does not restate a schema in a resumed
session. The
corrected answer is the original findings document with those values in those
citations, and nothing else changes. It goes through the same parser, identity,
hash and line checks. Scrutare never clamps a range or fills in a value. The
session's outcome is one of:

- `corrected`: the corrected answer is valid, and the session completes with it;
- `refused`: the corrected answer is refused, and the session fails for that
  refusal (`citation_validation` or `protocol`), as an uncorrected answer would;
- `no_answer`: nare ended without a correction answer (for example its budget
  or turn limit), and the session fails with its original refusal;
- `failed`: the turn did not finish (timeout, cancellation, unreadable or
  inconsistent evidence), and the session fails as such a session does today.

With the setting off, the canonical configuration and every artifact are
byte-identical to earlier releases. With it on, `config.to_dict()` records
`findings.citation_correction: true`, and every discovery session's
`result.json` records `{"offered": bool, "defects": N, "outcome": ...,
"exit_code": ...}` (`outcome` and the correction run's `exit_code` are `null`
when nothing was offered, and `exit_code` also when the run did not finish).
For a corrected session, the session's own `nare_status`, `stop_reason`,
`exit_code` and cumulative `usage` are those of the correction run; for a
refused or unanswered one they are `null`, as for any refused answer today.
The `structured_output` and `max_output_tokens` records count the first run's
stream only; the correction's constrained turn and any cut-off are in
`correction.stdout.jsonl`. An offered session's attempt
directory also keeps `citation-correction.json` (the original refusal, the named
defects, the exact prompt and its SHA-256, and the argv), the correction schema,
and the turn's `correction.stdout.jsonl`, `correction.stderr.txt` and
`correction-session.json`; nare's original `session.json` and `stdout.jsonl` are
left as they were. [Replay](replay.md) recomputes these records from the
retained streams and inputs. The evaluator
passes the setting to every evidence-v2 arm.

## Category partition

The fixed vocabulary is `correctness`, `security`, `regression`, `style`,
`consistency`, and `docs`. Both verdict lists together must contain each
category exactly once, with no overlap, duplicate, missing, or unknown
category. Categories can be reclassified, for example:

```yaml
verdict:
  blocking_categories: [security, docs]
  advisory_categories: [correctness, regression, style, consistency]
```

Configured advisory categories never block. Personas cannot invent new
categories. Either list may be empty if the other covers the complete
vocabulary. When only one list is supplied, the other keeps its default and
the combined partition must still be valid.

## Programmatic loading and normalization

`parse_config(data: bytes | str)` validates bytes or text, and
`load_config(path: Path)` reads and validates a file. Both return frozen
`ReviewConfig` dataclasses with typed nested settings and tuple collections.
Inline definitions become reusable `PersonaDefinition` objects. File read
failures also raise `ConfigError`.

`config.models.for_persona(name)` resolves the persona's override or the
default rail. `config.to_dict()` returns a fresh dictionary in the approved
YAML shape, including every effective default and fully resolved override
rails. It retains inline definition text and list order for personas,
reviewers, and globs. Verdict lists normalize to the fixed vocabulary order
above; override keys normalize by name. Serializing the dictionary with
`yaml.safe_dump` and passing it to `parse_config` produces equal settings.

For exhausted runs, [`post_escalation`](escalation.md) validates
`github.human_reviewers` as plain human GitHub login syntax before network
access. Case-insensitive duplicates keep their first spelling and order.
Team paths, `@` prefixes and literal `[bot]` suffixes are unsupported. Empty
targets still publish the escalation COMMENT and skip the request stage.
No additional escalation or retry settings are required.

## Opt-in inspection procedures

The default procedure profile is `baseline`; it retains the existing prompts
and four-persona roster. The profile is omitted from canonical configuration
when baseline is selected, preserving older captured configurations.

```yaml
models:
  default:
    model: your-model
inspection:
  procedures: v1
personas: [senior-dev, junior-dev, security, devops, testing-verification]
```

`inspection.procedures` accepts exactly `baseline` or `v1`. The versioned profile
resolves named built-ins to explicit inspection procedures. Inline definitions,
including overrides of built-in names, retain their exact supplied text.
`testing-verification` is an optional built-in available in either profile;
omitting `personas` still selects the original four reviewers. Selecting it
uses the same shared review budget and category/verdict policy as other reviewers.

Structured evidence is opt-in:

```yaml
context:
  enabled: true
findings:
  evidence: v2
```

`findings.evidence` accepts `legacy` (default) or `v2`; v2 requires context to be
enabled. Default/explicit legacy is omitted from canonical configuration
snapshots, preserving the established baseline. See [findings](findings.md) for
the citation contract and its mechanical-versus-semantic validity boundary.

Independent semantic assessment is separately opt-in and requires v2:

```yaml
context:
  enabled: true
findings:
  evidence: v2
  assessment:
    enabled: true
    tokens: 2000
```

`tokens` is a positive explicit assessor allocation (default 2000) below
`budgets.review_max_tokens`. The same review ledger reserves it before discovery,
then fairly divides remaining tokens among discovery/debate participants, subject
to `per_persona_tokens`. The assessor uses the default model rail in a separate,
fresh read-only nare session; its quota does not inherit a discovery persona's
model override. Assessment alone uses one assessor batch with the same runtime turn and
timeout limits. After-turn token overshoot remains observable, and can prevent
assessment admission; it never creates a second ledger or extra token ceiling.

Focused evidence discussion is a further opt-in for assessed v2 debate reviews:
`findings.discussion.enabled: true` requires `strategy: debate` and enabled
independent assessment. It uses `rounds.max` and the existing shared quotas;
reassessments spend the remaining original assessor reservation. See
[focused debate](debate.md#focused-evidence-discussion-opt-in).

### Phase allowances and overrun fallback (opt-in, unmeasured)

By default an assessor or discussion session that ends past its grant voids the
whole review: no verdict, even when discovery and assessment had completed. Two
opt-in settings change that; both are omitted from canonical configuration at
their defaults, so existing configurations produce the same artifacts.

```yaml
findings:
  evidence: v2
  assessment:
    enabled: true
    tokens: 30000
    overrun: fallback        # default: void
  discussion:
    enabled: true
    tokens: 20000            # default: unset, reuse discovery and assessor grants
    overrun: fallback        # default: void
```

- `findings.discussion.tokens` is a separate discussion allowance. Like the
  assessor reservation, the ledger carves it out of `budgets.review_max_tokens`
  before discovery is split, so discovery cannot spend it. Every discussion-round
  session draws on it alone: concurrent reviewer positions split what remains
  evenly, and a reassessment takes what is left after them. The assessor
  reservation then covers only the initial assessment. Assessor and discussion
  allowances together must leave discovery capacity.
- `overrun: fallback` answers a measured overrun with the last complete stage. An
  assessor overrun keeps the anchor-validated findings unassessed; a discussion
  overrun (a position or a reassessment) keeps the assessment from before that
  round. Only an admitted session that stopped for its budget with complete
  accounting qualifies, and every other session in that step must have completed.
  A failed, malformed or refused session, or incomplete or unknown accounting,
  still voids the review.
- A fallback is recorded as degraded and is never an approval. Status is
  `partial`; `verdict.json`, `panel.json` or `debate.json` carry a `degraded`
  record naming the phase, round, allowance, grant, actual usage, overrun and
  fallback stage. Blocking findings still request changes, unresolved potentially
  blocking claims still escalate under `unresolved_semantic_assessment`, and
  otherwise the verdict escalates under `phase_overrun_fallback`. Replay rebuilds
  the fallback from the retained outcomes without running a model.
- With any of these settings, one participant's overshoot past its own grant no
  longer shrinks a later reserved grant: each grant is bounded by its own share of
  the ceiling, not by what others overspent. Every grant still fits the ceiling's
  partition, but total actual usage can exceed `review_max_tokens` by overshoot
  already incurred, as after-turn enforcement always allowed.
- Discussion grants are shared by every round. Positions take an even share of
  what remains and a reassessment takes the rest, so a reassessment or a later
  round can find nothing left and is refused admission. With
  `findings.discussion.overrun: fallback` and complete accounting, that refusal also
  falls back to the assessment before the refused round; the round is recorded with
  `admission: refused` and a degraded record with zero grant and usage. Replay
  checks the refusal against the recorded allowance usage, or, when grants are
  reused, against the reviewers' and assessor's spent allocations. Without the
  fallback policy the review is void, as before.
- `result.json` and the CLI envelope carry the same `degraded` record. The reusable
  Action accepts an escalation under `phase_overrun_fallback` only with that record,
  and accepts escalations under `unresolved_semantic_assessment`.
- Under the fallback policy an overrun assessor's or reassessor's output is never
  kept: its assessment file records no assessments.
- `findings.assessment.overrun: fallback` requires `strategy: panel` or `debate`.
  Iterative reviews re-derive verdicts from assessment state kept across pushes,
  so the setting is refused there.

Neither setting has been measured; evaluation comparison sets do not enable them.

## Replies on review threads (opt-in)

`replies.enabled: true` lets a reply on one of Scrutare's inline threads contest
that finding and be answered in the thread. It is accepted only with
`strategy: iterative`; any other strategy fails with
`replies.enabled: requires strategy iterative`. `replies.trust` selects who may
trigger a reassessment. The default section is omitted from canonical
configuration, so existing captured configurations and iterative histories are
unchanged. See [iterative review](iterative-review.md#replies-on-scrutare-threads-opt-in).


## Protocol correction turn (opt-in)

`findings: {evidence: v2, protocol_correction: true}` offers a discovery reviewer,
evidence assessor or focused discussion perspective one tool-free resumed turn when
its entire answer is refused only for deterministic protocol defects. Like citation
correction, it requires OpenAI rails. It is off by default. When both correction
settings are on, protocol correction owns the single opportunity; the two never chain.
Ordinary legacy pool-selection/chair and anchor-only sessions retain their existing policy.

The defect inventory names field paths and exact requirements: missing/unknown fields,
wrong types (including booleans used as integers), enums, empty required strings,
non-positive lines, malformed paths/revisions/hashes, reversed or out-of-capture citation
ranges, captured revision/path/hash mismatches, missing required evidence and unknown,
missing or duplicate immutable candidate IDs. Every accessible field is checked, including
those hidden by an earlier parser refusal. Corrupt or inconsistent native evidence,
provider failure, output caps, exhausted budgets and changed/unreadable captured inputs
are ineligible; they retain their existing failure handling. No semantic judgement is retried.

The model returns a **complete replacement answer**, under the original answer schema,
using only the session's captured inputs. The engine never drops a finding, clamps a
line or removes/inserts a candidate ID. Both original and replacement are retained;
the replacement must pass the unchanged parser, immutable candidate coverage, citation
and evidence checks. A refused replacement fails the session with no second correction.
This broader setting is separate from the citation-only contract, which is unchanged.

The resumed process has `--tools none`, `--max-turns 1`, the same budget, model, cap and
structured-output mode, and only the remaining session timeout. Usage is cumulative in
the original lease and shared ledger. A turn can still overrun its after-turn budget;
existing overrun handling remains authoritative. The correction's stream retains its
own constraint/cap evidence.

Each eligible session records `protocol_correction: {offered, defects, outcome, exit_code}`
in `result.json`, with the same outcome vocabulary as citation correction: `corrected`,
`refused`, `no_answer`, `failed`, or null when unoffered. Offered attempts keep
`protocol-correction.json` (original refusal, complete defect inventory, exact prompt,
prompt hash and argv), `protocol-correction.schema.json`, `correction.stdout.jsonl`,
`correction.stderr.txt`, and `correction-session.json`. The original `session.json` and
`stdout.jsonl` remain untouched. Assessment/discussion consume the replacement only
after an accepted outcome. The producer provenance inventory covers all retained files.
Replay independently reconstructs the caller pool, defect inventory, prompt, schema,
arguments, two native streams, corrected findings and cumulative usage.
