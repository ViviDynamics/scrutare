# Review CLI

`scrutare review` captures a PR, executes the panel through a separate nare
process, and posts one captured-head GitHub review. `python -m scrutare` provides
the same interface.

## Install and set up

On macOS or Linux with Git and curl, run this from any directory:

```sh
curl -fsSL https://github.com/ViviDynamics/scrutare/releases/latest/download/install.sh | sh
```

The installer prints the executable path and both release versions. It reuses uv or installs a private copy, provisions Python 3.14, and pairs the release wheel with the Nare runtime pinned by that release.

**Copy this prompt into your coding agent in the checkout you want reviewed.**

```text
Set up the latest released Scrutare CLI for this repository. Run the installer above if needed:
curl -fsSL https://github.com/ViviDynamics/scrutare/releases/latest/download/install.sh | sh
Use its printed executable path. Reuse authorized GitHub CLI access and the existing provider configuration. Check gh auth status --hostname github.com with output suppressed. Check only whether the selected provider's credential variable is set. Never print, paste or store credential values. If access is missing, ask me to configure it privately. Ask only for missing inputs, including the full PR URL and any model, provider or compatible endpoint needed for a new configuration.
Run the installed executable's setup --pr command with the chosen full URL. Keep an existing scrutare.yaml. Only for a new file, pass --model and --provider, plus --base-url for a custom endpoint. Use shell quoting for each value. Report setup errors without exposing configuration contents. On success, print the exact review command setup returned and stop before posting. If I want GitHub Actions, show the literal uses: ViviDynamics/scrutare@ tag matching the installed version.
```

For manual setup, install the GitHub CLI and authorize access to read the PR and submit a review. Configure `ANTHROPIC_API_KEY` for Anthropic or `OPENAI_API_KEY` for an authenticated OpenAI-compatible endpoint in your shell or secret manager. If the target endpoint accepts unauthenticated requests, follow its configuration.

For example, in the target checkout with an existing configuration:

```sh
cli="${XDG_DATA_HOME:-$HOME/.local/share}/scrutare/bin/scrutare"
"$cli" setup --pr https://github.com/owner/repo/pull/12
```

For a new file, append `--model MODEL --provider anthropic` or `--model MODEL --provider openai`, using your model identifier. Supply `--base-url` for a compatible endpoint. Setup validates the YAML locally, preserves existing bytes, and prints a quoted command for the selected PR and paired runtime. Running that command calls the model and submits the review.

For GitHub Actions, generate the literal version-pinned line from the installed release and paste it into the [caller workflow](pipeline.md):

```sh
version=$("$cli" --version)
printf '      - uses: ViviDynamics/scrutare@%s\n' "${version#scrutare }"
```

## Review


```sh
scrutare review --pr https://github.com/owner/repo/pull/12 \
  --config scrutare.yaml --nare-executable /absolute/path/to/nare
scrutare review --pr 12
```

A numeric PR uses `gh repo view` in the current checkout. Full GitHub URLs do not
need repository inference. `gh` must have authorized read and review-write
access. `--config` defaults to `scrutare.yaml`, must be an accessible regular
file, and requires `models.default.model`. Configuration is read once and its
exact bytes are retained. See the [configuration reference](config.md).

`--nare-executable` is an operational option, not a YAML setting. It defaults to
`nare` on PATH and can select a separate environment with its own interpreter.
The tested runtime is nare 2026.11.109; the engine requires at least 2026.10.0 and
contract 1 (releases before 2026.10.7), contract 2 (before 2026.11.33),
or contract 3. The `panel`, [iterative](iterative-review.md), and
[debate](debate.md) strategies execute reviews. A missing runtime is rejected
before repository lookup, capture or model calls. Runtime version
and protocol inspection remain engine checks. Built-in personas and custom
systems receive the filtered read-only root described in
[session fan-out](session-fanout.md).

`--replies-only` is for callers triggered by a reply on a review thread rather
than a push. It requires `replies.enabled` and is refused otherwise before any
GitHub access. See
[replies on Scrutare threads](iterative-review.md#replies-on-scrutare-threads-opt-in).

## Output and exits

| Exit | Meaning |
| --- | --- |
| 0 | Review delivery confirmed, result and manifest persisted. Applies to approval and changes requested. |
| 1 | Configuration, runtime, capture, execution, posting or required persistence failed. |
| 2 | Command-line usage error. |
| 4 | The opt-in route preflight failed; nothing was started. Stdout is a `status: not_started` document with the `reason`. See [the configuration reference](config.md#route-preflight-opt-in). |
| 130 | Interrupted; owned session processes are cleaned up. |

Success writes exactly the UTF-8 bytes in `result.json` to stdout. The JSON
includes schema/application versions, `status: posted`, absolute `run_dir`,
captured `head_sha`, code-derived `verdict` and `rule`, `panel_status`, reported
`usage`, `accounting_complete`, the review's `cost` (`{"usd", "source"}`, with
`source` `provider_reported` or `unknown`; see
[session-fanout.md](session-fanout.md#admission-and-accounting)), and the
validated review receipt. An applicable
reviewer-request receipt is included separately. Success is emitted only after
the result and manifest are installed. With `replies.enabled` the document also
has `replies`, one entry per threaded response this run delivered. A
`--replies-only` run whose head and verdict the pull request already shows posts
no review: it exits 0 with `status: unchanged` and no `review` field.

Failures leave stdout empty and explain the failure on stderr. Once capture has
completed, the diagnostic identifies its retained run. A later persistence
failure can follow confirmed posting; its diagnostic says delivery was confirmed.
An uncertain delivery has no success result. Inspect `posting.json` and the
[posting recovery contract](posting.md) before attempting another review.
Starting a fresh CLI review creates a new run and is not a recovery command for
an earlier uncertain POST.
See [failure paths](failure-paths.md) for coverage, lifecycle and delivery
diagnostics, and shell caller behavior. The [pipeline guide](pipeline.md)
describes the published `2026.10.2` reusable Action, trusted configuration,
uploaded evidence and audited hosted success and invalid-config failure. See the
[dated acceptance record](action-acceptance.md) for those observed outcomes and
their evidence limits.

## Coverage and evidence

The default panel runs one wave and at most one anchor correction per affected
persona. Code derives the verdict. `github.post_mode: comment` sends a COMMENT
without changing a blocking code-derived verdict. Partial validated findings,
including an explicit empty array, can yield a posted verdict. Missing, failed
or uncertain initial coverage withholds a verdict and sends no review. Inspect
`panel.json` for partial coverage and dropped anchors. Reported token thresholds
apply after turns, can overshoot, and are not complete provider billing.
Failure and budget stops do not manufacture nonconvergence or escalation.

Runs live under `.scrutare/runs/` in the working directory. They retain:

- Exact raw diff and configuration bytes, captured PR and discussion data,
  effective files, and the separate three-file `review-inputs/` root.
- Real nare sessions, streamed output and typed results under `sessions/`, plus
  `fanout.json` and `panel.json` with systems, rails, filtered roots and accounting.
- Bare `findings.json` and canonical schema-1 `verdict.json` when a verdict exists.
- `review-payload.json` and `posting.json` when posting is prepared, plus a
  successful `result.json` only after confirmed delivery.
- `artifacts.json`, a versioned inventory of sorted relative POSIX paths, SHA-256
  digests and byte sizes from the quiescent run.

The manifest excludes itself, `.posting.lock`, `.scrutare-*.tmp` and
`.posting-*.tmp` staging files. Symlinks and nonregular entries are refused.
Installation is exclusive and private. Failures can retain a diagnostic snapshot
without successful execution or delivery; artifact installation is not a
multi-file transaction. The manifest is unsigned local evidence, not proof of
authenticity, provider billing or remote delivery.

```sh
scrutare replay /absolute/path/to/run
```

Replay reads canonical findings, policy, verdict and recorded posting evidence
without model or network calls or artifact writes. Saved byte identity and
locally recorded posted identity are separate comparisons. Replay does not
validate the artifact manifest, and legacy runs need no manifest. Replay exits
0 for identity, 1 for differences, 2 for incomplete or invalid evidence. See
[replay](replay.md) for its trust boundary.

## Inspect a configuration

```sh
scrutare config show --config scrutare.yaml
```

`config show` resolves a configuration file exactly as `review` would and
prints one JSON document: `profile` (the `NAME@VERSION` the file names, or
`null`), `options` and `config`, the canonical configuration a review records
in `config.json`. Each entry of `options` is one field a
[profile](config.md#profiles) covers, on the default rail and on each override,
with its resolved `value` and `source`: `profile` when the profile filled it,
`file` when the file sets it, and `inactive` when the value does not apply to
the review's shape and the field keeps its default. Without a profile, `options`
is empty.

The command is read-only and offline: it starts no nare process, makes no
GitHub or model request and writes nothing. `--config` defaults to
`scrutare.yaml`. An unreadable or invalid file exits 1 with the configuration
error on stderr and nothing on stdout. See [self-hosted](self-hosted.md) for the
`self-hosted@1` profile.
