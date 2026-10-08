"""qare-style newline JSON-RPC MCP server for full pull-request reviews."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from scrutare import __version__
from scrutare.config import ConfigError, parse_config
from scrutare.engine.github import GitHubError, resolve_pr
from scrutare.engine.review import ReviewRunError, preflight_review, review_pr
from scrutare.engine.session_models import NareRuntime

PROTOCOL_VERSION = "2024-11-05"
TOOLS = [{
    "name": "review",
    "description": (
        "Review a GitHub pull request and post the captured-head verdict, as scrutare review does. "
        "Returns the exact result.json, including the durable run directory."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "pr": {"type": "string", "minLength": 1,
                   "description": "GitHub PR URL or number in the server working repository"},
            "config": {"type": "string", "minLength": 1, "default": "scrutare.yaml",
                       "description": "YAML path relative to server working directory"},
            "nare_executable": {"type": "string", "minLength": 1, "default": "nare",
                                "description": "Separately installed nare executable path or name"},
        },
        "required": ["pr"],
        "additionalProperties": False,
    },
}]


class ProtocolError(Exception):
    """A JSON-RPC protocol or caller input error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _string(arguments: dict[str, Any], key: str, default: str | None = None) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(-32602, f"{key} must be a non-empty string")
    return value


async def _review(arguments: dict[str, Any]) -> str:
    if arguments.keys() - {"pr", "config", "nare_executable"}:
        raise ProtocolError(-32602, "unknown review argument")
    pr = _string(arguments, "pr")
    path = Path(_string(arguments, "config", "scrutare.yaml"))
    runtime = NareRuntime(Path(_string(arguments, "nare_executable", "nare")))
    try:
        if not path.is_file():
            raise OSError("not a regular file")
        raw = path.read_bytes()
    except OSError:
        raise ReviewRunError(
            "cannot read config file; provide an accessible regular file.") from None
    config = parse_config(raw)
    preflight_review(config, runtime)
    ref = resolve_pr(pr)
    result = await review_pr(ref, config, config_bytes=raw,
                             runs_root=Path(".scrutare/runs"), runtime=runtime)
    return result.to_bytes().decode("utf-8")


async def handle_line(line: str) -> dict[str, Any] | None:
    """Answer one request; notifications never execute tools or emit responses."""
    if not line.strip():
        return None
    identity: str | int | None = None
    try:
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            raise ProtocolError(-32700, "parse error: request is not valid JSON") from None
        if not isinstance(message, dict):
            raise ProtocolError(-32600, "invalid request: expected JSON-RPC object")
        candidate = message.get("id")
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            identity = candidate
        if (message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
                or ("id" in message and candidate is not None and identity is None)
                or ("params" in message and not isinstance(message["params"], dict))):
            raise ProtocolError(-32600, "invalid JSON-RPC request")
        if "id" not in message:
            return None
        method = message["method"]
        params = message.get("params", {})
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                "serverInfo": {"name": "scrutare-mcp", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            if params.get("name") != "review":
                raise ProtocolError(-32602, "unknown tool")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ProtocolError(-32602, "arguments must be an object")
            try:
                output = await _review(arguments)
                result = {"content": [{"type": "text", "text": output}]}
            except (ConfigError, GitHubError, ReviewRunError) as error:
                location = (f" Run: {error.run_dir}" if isinstance(error, ReviewRunError)
                            and error.run_dir is not None else "")
                result = {"content": [{"type": "text", "text": f"scrutare: {error}{location}"}],
                          "isError": True}
        else:
            raise ProtocolError(-32601, "unknown method")
        return {"jsonrpc": "2.0", "id": identity, "result": result}
    except ProtocolError as error:
        return {"jsonrpc": "2.0", "id": identity,
                "error": {"code": error.code, "message": str(error)}}


async def serve(reader: TextIO, writer: TextIO) -> None:
    """Process requests sequentially, flushing one JSON line per response."""
    for line in reader:
        response = await handle_line(line)
        if response is not None:
            writer.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
            writer.flush()


def main() -> int:
    """Run the stdio transport without banners or success output outside JSON-RPC."""
    try:
        asyncio.run(serve(sys.stdin, sys.stdout))
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
