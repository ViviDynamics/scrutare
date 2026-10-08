# Request reviews over MCP

Install scrutare and the separately installed nare runtime described in
[session-fanout.md](session-fanout.md). Run `scrutare-mcp` from the repository
whose PRs you want to review, with a valid `scrutare.yaml` in that working directory.
The equivalent module entry is `python -m scrutare.interfaces.mcp`.

A client configuration can use:

```json
{
  "mcpServers": {
    "scrutare": {
      "command": "/absolute/path/to/scrutare-mcp"
    }
  }
}
```

Set the client's server working directory to the repository root. Relative paths
and numeric PR references resolve from that directory, exactly as in the CLI.
Authenticate `gh` and configure the model provider for the same environment you
would use for `scrutare review`.

The server mirrors qare's stdio surface: one UTF-8 JSON-RPC object per line,
protocol version `2024-11-05`, `initialize`, `ping`, `tools/list`, and `tools/call`.
It advertises one tool, `review`, with the CLI's inputs:

| Input | CLI equivalent | Default |
| --- | --- | --- |
| `pr` | `--pr` | Required GitHub PR URL or positive number |
| `config` | `--config` | `scrutare.yaml` |
| `nare_executable` | `--nare-executable` | `nare` on PATH |

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"review","arguments":{"pr":"https://github.com/owner/repo/pull/12","nare_executable":"/absolute/path/to/nare"}}}
```

Calling `review` performs the full review **and posts to GitHub**. It uses the
same production engine as the CLI, preserving captured-head binding, budgets,
findings, verdict, session evidence, posting journal, and artifact manifest under
`.scrutare/runs`. The text content in a successful tool result is the exact
`result.json` bytes decoded as UTF-8, including its absolute `run_dir`.
Independent runs have different run directories and delivery identifiers.

Invalid JSON-RPC requests and invalid tool arguments return protocol errors.
Configuration, GitHub, and review execution failures return an MCP tool result
with `isError: true` and the CLI's safe diagnostic, including a saved run path
when one exists. A subsequent request can still be processed. Notifications
never execute reviews or receive responses. Requests execute sequentially;
this transport does not support progress or cancellation notifications. Interrupt
the server process to invoke the engine's normal child cleanup.
