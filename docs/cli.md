# Review CLI

`scrutare review` captures a PR, executes the panel through a separate nare
process, and posts one captured-head GitHub review. `python -m scrutare` provides
the same interface. See [releases](releases.md) for installation.

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
The tested runtime is nare 2026.10.4; the engine requires at least 2026.10.0 and
contract 1. Unsupported `iterative` or `debate` strategies and a missing runtime
are rejected before repository lookup, capture or model calls. Runtime version
and protocol inspection remain engine checks. Built-in personas and custom
systems receive the filtered read-only root described in
[session fan-out](session-fanout.md).

## Output and exits

| Exit | Meaning |
| --- | --- |
| 0 | Review delivery confirmed, result and manifest persisted. Applies to approval and changes requested. |
| 1 | Configuration, runtime, capture, execution, posting or required persistence failed. |
| 2 | Command-line usage error. |
| 130 | Interrupted; owned session processes are cleaned up. |

Success writes exactly the UTF-8 bytes in `result.json` to stdout. The JSON
includes schema/application versions, `status: posted`, absolute `run_dir`,
captured `head_sha`, code-derived `verdict` and `rule`, `panel_status`, reported
`usage`, `accounting_complete`, and the validated review receipt. An applicable
reviewer-request receipt is included separately. Success is emitted only after
the result and manifest are installed.

Failures leave stdout empty and explain the failure on stderr. Once capture has
completed, the diagnostic identifies its retained run. A later persistence
failure can follow confirmed posting; its diagnostic says delivery was confirmed.
An uncertain delivery has no success result. Inspect `posting.json` and the
[posting recovery contract](posting.md) before attempting another review.
Starting a fresh CLI review creates a new run and is not a recovery command for
an earlier uncertain POST.
See [failure paths](failure-paths.md) for coverage, lifecycle and delivery
diagnostics, and shell caller behavior. Reusable GitHub Action wiring and
verification are tracked separately in
[#14](https://github.com/ViviDynamics/scrutare/issues/14).

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
