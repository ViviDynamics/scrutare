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
| `models.overrides` | Mapping from a configured persona name to partial model settings | `{}` |
| `verdict.blocking_categories` | List drawn from the fixed vocabulary | `correctness`, `security`, `regression` |
| `verdict.advisory_categories` | List drawn from the fixed vocabulary | `style`, `consistency`, `docs` |
| `github.post_mode` | `review` (blocking review), `comment` (never blocks) | `review` |
| `github.human_reviewers` | List of nonempty reviewer-name strings for escalation | `[]` |
| `github.paths.include` | List of nonempty glob strings | `[]` |
| `github.paths.exclude` | List of nonempty glob strings | `["docs/**", "*.md"]` |

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
model override. A review has one assessor batch with the same runtime turn and
timeout limits. After-turn token overshoot remains observable, and can prevent
assessment admission; it never creates a second ledger or extra token ceiling.
