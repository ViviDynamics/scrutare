# Failure paths

`scrutare review` returns 0 only after confirmed delivery and required result
and manifest persistence. Approval and changes requested both use exit 0.
Execution or delivery failures return 1 with empty stdout and a diagnostic on
stderr. After capture, stderr identifies the retained `.scrutare/runs/` directory.
Usage errors return 2; interruption returns 130. See the [CLI reference](cli.md).

| Situation | Evidence to inspect | Outcome |
| --- | --- | --- |
| A required initial persona fails or cannot be admitted | `panel.json`, `fanout.json`, and `sessions/<persona>/attempt-*/result.json` identify coverage, status, reason and accounting. Raw `stdout.jsonl`, `stderr.txt` and `session.json` retain private execution detail. | Exit 1, no panel verdict or POST. A clean sibling or an earlier valid document cannot conceal later execution failure. |
| A budget-stopped or overshooting persona has a validated document | Session result and `panel.json` show partial status, available output, actual usage and overshoot. `findings.json` and `verdict.json` record the accepted evidence and derived verdict. | Eligible partial coverage can deliver with exit 0. An explicit empty findings array can approve; partial coverage alone does not mean failure. |
| Initial output is missing or invalid | Session result records unavailable validated output; raw session captures explain what was returned. | Exit 1, no verdict or POST. Missing output never becomes an empty review. |
| Accounting is missing, corrupt or uncertain | Session results and the `panel.json` ledger show accounting confidence and retained usage. | Exit 1, no verdict or POST. Uncertain allowance closes admission to further invocations. |
| The PR closes or merges after capture | Stderr reports the lifecycle failure. Captured `metadata.json` records the earlier identity; any existing panel/verdict artifacts describe local computation, not delivery. | Exit 1, abort delivery and post nothing when the lifecycle check sees closure. Retained evidence is not a posted review. |
| The live head changes after capture | `metadata.json`, payload `commit_id`, summary Head SHA, `posting.json` and the receipt/result identify the captured SHA. | An open PR may still receive the captured-head review with exit 0. A rejection of the old commit returns 1; the poster never silently switches heads. |
| Findings need anchor correction | `panel.json` records correction requests, outcomes and dropped anchors; session attempts retain the correction evidence. | At most one fresh correction invocation per affected persona. Denied or known budget-stopped corrections without output drop their originals; validated partial corrections count. Failed, protocol-invalid or uncertain corrections fail the panel with exit 1 and no verdict. |
| Posting is uncertain | `review-payload.json` is the exact attempted body; `posting.json` records `sending` or `unknown`. There is no success result. | Exit 1. Preserve the whole run and follow [posting recovery](posting.md); an absent match does not prove the write failed. |
| Required persistence fails after confirmed posting | Stderr says delivery was confirmed. Inspect the receipt, `posting.json`, `result.json` and available manifest evidence. | Exit 1, empty stdout, even though GitHub may already contain the review. Inspect delivery evidence before another action. |

The panel does not retry initial sessions or resume them. Correction invocations
repair anchors against the same live budget; they do not retry failed initial
coverage. The existing poster has separate bounded retry and reconciliation
rules. `sending` or `unknown` recovery reconciles the saved attempt without a
new POST. A fresh CLI review is a new capture and is not recovery for an
uncertain delivery. Keep journals and payloads intact.

Token budgets are admission grants and after-turn thresholds. Already admitted
turns can overshoot persona and whole-review allowances. Artifacts retain actual
usage without clipping, but provide no hard spending ceiling or complete
provider billing claim. Raw provider errors stay in private session captures;
normalized diagnostics identify the persona and reason without exposing those
errors. Treat captured inputs and raw sessions as private when sharing evidence.

For shell callers, a plain invocation under `bash -e` propagates exit 1 and skips
the next command:

```sh
bash -e -c '
  python -m scrutare review --pr 12 --nare-executable /absolute/path/to/nare
  printf "review delivery confirmed\n"
'
```

This caller behavior is proved with the installed module and an actual nare
provider failure. Reusable GitHub Action wiring and its verification belong to
[#14](https://github.com/ViviDynamics/scrutare/issues/14).
`artifacts.json` inventories local files; it does not prove successful execution,
provider billing or remote delivery. [Replay](replay.md) compares saved and
locally recorded posted verdict identity without model or network calls.
