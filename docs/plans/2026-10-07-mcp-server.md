# MCP server (M2)

Issue #16

## Scope
In: qare-style stdio MCP server with a real review tool, CLI input defaults, identical engine results and artifacts, packaging and usage documentation.
Out: replay/evidence tools, HTTP transport, and alternative strategy implementation (separate tickets).

## Assumptions
- Mirror qare's newline JSON-RPC protocol version 2024-11-05 and text JSON tool results without adding an SDK dependency.
- Review posts to GitHub exactly as the CLI does; paths resolve from the server working directory.
- Process calls sequentially to avoid concurrent GitHub posting; notifications do not execute tools.

## Tasks
- [x] 1. Protocol lifecycle and invalid requests: failing protocol tests.
- [x] 2. Shared production review execution: parity test checks result, manifest, findings, sessions and posting artifacts.
- [x] 3. Installed server entry point and documented client configuration: subprocess stdio tests.
