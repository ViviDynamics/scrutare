# scrutare specification

Status: design. Approved through discovery on 2026-10-03.

## 1. Goal

Scrutare is a standalone code review meta harness. It reviews a pull request
as a panel of reviewer agents, converges their findings into one verdict, and
posts the verdict on the pull request with inline comments. If the panel
cannot converge within its bound, it escalates to human reviewers instead of
approving.

Scrutare is a nare-based harness: every model call goes through
[nare](https://github.com/ViviDynamics/nare), the same rule qare holds. It
fills the reviewer performer role for
[conductor](https://github.com/ViviDynamics/conductor) and
[coordinare](https://github.com/ViviDynamics/coordinare), and works as a
standalone reviewer for any repository, in the shape of a GitHub App plus the
qare-style programmatic surface (CLI, GitHub Action, MCP server).

### Non-goals

- Scrutare does not write code, fix findings, or boot applications. It
  reviews, posts, and escalates. Repair is the implementer's job, QA is
  [qare](https://github.com/ViviDynamics/qare)'s job.
- Scrutare does not orchestrate boards or cards. It reviews one pull request
  per run.
- The verdict is never model-declared. A model cannot approve a PR; code
  derives the verdict from code-verified findings.

## 2. Architecture

Approach: one nare session per perspective, orchestrated by code.

The engine runs one nare session per persona in parallel, each with the
persona's system prompt (`--system`), a read-only tool policy, a token budget,
and the persona's model rail (`--provider`, `--base-url`, `--model`). Sessions
emit typed JSONL; the engine collects findings, verifies anchors, dedupes,
applies the configured convergence strategy, derives the verdict by rule, and
posts one review.

Why this shape: the three convergence strategies are real code paths rather
than prompt behavior, the verdict rule is auditable in code, and it mirrors
the family pattern of a harness orchestrating nare sessions (qare orchestrates
boots and proofs; scrutare orchestrates perspectives).

### Repository layout

```
scrutare/
  src/scrutare/
    engine/        orchestrator: strategy runner, session fan-out, budgets
    personas/      persona registry + built-in persona system prompts
    findings/      anchor verification, dedupe, verdict deriver (pure code)
    poster/        GitHub review posting, inline comment anchoring
    interfaces/    cli.py, action entrypoint, mcp server, (m4: app webhook)
  docs/            SPEC.md, config reference, writing-personas.md
  .agents/skills/  PR-review skills (diff survey, anchored findings, severity)
  action.yaml      reusable GitHub Action
```

### Engine flow (one review run)

1. Fetch the pull request: diff, changed files, head SHA, prior reviews and
   comments, into a run directory.
2. Fan out: one nare session per persona in parallel, read-only, budget
   capped, emitting typed JSONL findings.
3. Verify: code drops any finding whose file and line anchor does not exist
   in the diff, asking once for re-anchored findings (the conductor spec 169
   rule).
4. Converge: apply the configured strategy.
5. Derive the verdict by rule: any blocking finding requests changes, none
   approves. Findings are never model-declared.
6. Post one review with inline comments anchored to diff lines, a short
   summary, and persona attribution on each comment, noting the head SHA the
   run reviewed.
7. Persist `findings.json`, `verdict.json`, and the session files.

## 3. Convergence strategies

`strategy` is a top-level config setting. All three are first-class behaviors
of the application; the implementer of a repository chooses.

- **panel** (milestone 1): the perspectives review independently, in one
  round. Code dedupes findings and derives the verdict. The cheap default.
- **iterative** (milestone 2): the findings pool persists across pushes. Each
  new push is re-reviewed for what is new or contested, within the round
  bound, so an author pushing fixes is re-reviewed rather than re-litigated.
- **debate** (milestone 3): the perspectives see each other's findings, and a
  chair session (senior developer) arbitrates disputes, dedupes, downgrades
  nitpicks, and accepts the final finding set. A deadlock that outlasts the
  bound escalates.

Each strategy has its own round bound before escalation; `rounds.max` is the
default.

## 4. Personas

The built-in persona set ships as registry entries: senior developer, junior
developer, security, and devops, matching the perspectives the panel is
expected to cover, plus any further perspectives worth mirroring. Personas
are data, not code: built-ins ship in the same format as inline config
overrides, a custom persona is a name and a system prompt (carried to nare via
`--system`), and adding a perspective is a config diff.

## 5. Config surface

One YAML file, `scrutare.yaml`, committed to the reviewed repo or passed by
flag:

```yaml
strategy: panel            # panel | iterative | debate
rounds:
  max: 3

personas:                  # built-ins by name, or inline definitions
  - senior-dev
  - junior-dev
  - security
  - devops

budgets:
  per_persona_tokens: 100000
  review_max_tokens: 500000

models:
  default:                 # review-wide default rail
    provider: anthropic    # anthropic | openai (openai covers LiteLLM, vLLM,
                           # llama.cpp, Ollama)
    base_url: null
    model: <model>
  overrides:               # per-persona, wins over default
    security:
      provider: openai
      base_url: https://litellm.internal/v1
      model: <model>

verdict:
  blocking_categories: [correctness, security, regression]
  advisory_categories: [style, consistency, docs]   # never blocks

github:
  post_mode: review        # review (block) | comment (never block)
  human_reviewers: []      # escalation targets
  paths:
    include: []
    exclude: ["docs/**", "*.md"]
```

Design choices:

- Finding categories are a fixed set, with a code-enforced mapping to
  blocking and advisory, so the verdict rule stays auditable. Personas cannot
  invent a category that flips the verdict.
- Budgets are config, not vibes: per-persona and whole-review caps, enforced
  by the engine, and the run aborts (and says so) rather than overspending.
- Path filters exclude files from review entirely.

## 6. Verdict, anchoring, and posting

- Structured findings name a file and line the persona saw, a category from
  the fixed set, the problem, and why it blocks. Code drops any finding that
  points at nothing, asking once for re-anchored findings.
- The verdict is derived by rule from the surviving findings: any blocking
  finding means `changes_requested`, none means `approve`.
- One review per run: inline comments anchored to diff lines, persona
  attribution, a summary, and the head SHA the run reviewed.
- `post_mode: comment` posts the same content as a comment that never blocks.

## 7. Escalation

When the strategy's rounds are exhausted without convergence:

- The harness never approves. It posts a comment summarizing the unresolved
  findings, requests review from `github.human_reviewers`, and @-mentions
  them, the conductor break-case pattern.
- The run's verdict is `escalated`, distinct from `approved` and
  `changes_requested`, so callers can branch on it.

## 8. Failure paths

- A persona session fails (model or provider error): bounded retries from
  config, then the run fails without posting a verdict. Artifacts name the
  failed persona, and an Action run goes red.
- Findings unanchorable after one re-anchor round: dropped, and the verdict is
  derived from the survivors only.
- A budget cap hit mid-persona: the persona's result is marked partial, its
  findings still count, and the artifacts record the cap.
- The pull request closes or merges mid-run: abort, discard, post nothing.
- A force-push during a run: the run reviewed the captured head SHA and says
  so. The iterative strategy owns re-review on new pushes.
- A human review lands mid-run: the MVP posts anyway. Refinement deferred
  (conductor already learned this as specs 127 and 128).

## 9. Replay and audit

`scrutare replay <dir>` recomputes the verdict from the run's stored
artifacts, in code alone, with no model and no network, and says whether it
is byte-identical with the verdict the run posted. This is qare's audit
pattern: anyone can re-check a published verdict after the fact.

## 10. Interfaces and milestones

- **M1 (MVP)**: engine, persona registry, panel strategy, findings pipeline,
  verdict deriver, poster, CLI, config, path filters, replay, escalation,
  failure paths, reusable GitHub Action. Releases publish a wheel and a
  container image; the version is stamped into artifacts.
- **M2**: iterative strategy; MCP server mirroring qare's surface.
- **M3**: debate strategy with the chair session.
- **M4**: GitHub App front door: a webhook listener for `pull_request`
  events, its own review identity, deployed to the garden cluster, with
  conductor/coordinare integrating through the App or its API. Chat-style
  replies to PR comments (CodeRabbit parity) follow the App.

## 11. Nare dependency policy

Every capability scrutare needs from nare is tested against nare's actual CLI
at design time. The M1 needs are met by existing flags: `--system`, `--tools`,
`--provider`, `--base-url`, `--model`, `--max-tokens`, `--session`,
`--resume`, and the JSONL result line that carries usage. A capability nare
lacks is filed as an issue on nare's repository, and the dependent scrutare
feature is marked blocked by it, in the issue body and on the board. Scrutare
does not work around nare gaps silently.

## 12. Risks

1. Cost: N personas times rounds multiplies tokens. Budgets are config,
   diffs are chunked per file, and `panel` is the cheap default.
2. Review loops fighting the implementer: bounded rounds, then escalation to
   humans, never unbounded.
3. Hallucinated anchors: code-verified anchoring with one retry, then drop.
4. Secrets in prompts: PR text feeds model prompts. SECURITY.md exists from
   day one, and the checkout holds no credentials beyond what the review
   needs.
5. GitHub rate limits and review identity: the App token strategy lands in
   M4; the Action uses `GITHUB_TOKEN` until then.
