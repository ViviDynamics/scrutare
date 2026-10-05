"""Run an installed console script, replacing only nare.cli.make_transport.

This helper runs under nare's own interpreter, never the project's interpreter.
All CLI, loop, tool, schema, usage, event and persistence behavior remains real.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import runpy
import sys
import time
from dataclasses import asdict
from pathlib import Path

console, specification, *arguments = sys.argv[1:]
spec = json.loads(Path(specification).read_bytes())
# No ambient provider credentials enter the real CLI, even on developer machines.
environment = {
    name: value
    for name, value in os.environ.items()
    if name in ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ")
    or name.startswith("NARE_")
}
os.environ.clear()
os.environ.update(environment)
os.environ.update(spec.get("environment", {}))
sys.dont_write_bytecode = True


def option(name, default=None):
    for index, value in enumerate(arguments):
        if value == name:
            return arguments[index + 1]
        if value.startswith(name + "="):
            return value[len(name) + 1 :]
    return default


session_path = option("--session") or option("--resume")
artifact = Path(session_path).parent.resolve() if session_path else None
observations = {
    "pid": os.getpid(),
    "python": sys.version,
    "factory_calls": [],
    "calls": [],
    "guard_violations": [],
    "writes": [],
    "version": None,
    "credential_names": [n for n in os.environ if "KEY" in n or "TOKEN" in n],
    "nare_controls": {n: v for n, v in os.environ.items() if n.startswith("NARE_")},
    "arguments": arguments,
}


def audit(event, args):
    if event in {
        "socket.connect",
        "socket.connect_ex",
        "socket.getaddrinfo",
        "socket.bind",
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.fork",
    }:
        observations["guard_violations"].append(event)
        raise AssertionError("Forbidden network or secondary process: " + event)
    targets = []
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, bytes)) and flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC
        ):
            targets = [path]
    elif event in {
        "os.rename",
        "os.remove",
        "os.mkdir",
        "os.rmdir",
        "os.chmod",
        "os.link",
        "os.symlink",
    }:
        targets = args[:2] if event in {"os.rename", "os.link", "os.symlink"} else args[:1]
    for target in targets:
        if isinstance(target, (str, bytes)):
            path = Path(os.fsdecode(target)).resolve()
            if artifact is None or not path.is_relative_to(artifact):
                observations["guard_violations"].append(event)
                raise AssertionError("Mutation outside this invocation's artifacts: " + event)
            observations["writes"].append(str(path))


sys.addaudithook(audit)

nare_cli = importlib.import_module("nare.cli")
Usage = importlib.import_module("nare.session").Usage
Reply = importlib.import_module("nare.transport").Reply
ToolCall = importlib.import_module("nare.transport").ToolCall

observations["version"] = nare_cli.__version__


class FakeProvider:
    """A deterministic transport implemented at the vendor factory seam only."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.replies = list(scenario.get("replies", []))

    async def context_window(self):
        return None

    async def turn(self, messages, tools):
        observations["calls"].append(
            {"messages": [asdict(m) for m in messages], "tools": tools, "start": time.monotonic()}
        )
        if len(observations["calls"]) == 1:
            (artifact / "transport-ready.json").write_text(json.dumps({"pid": os.getpid()}))
            participants = self.scenario.get("barrier_participants", 0)
            deadline = time.monotonic() + 10
            while (
                participants
                and len(list(artifact.parent.parent.glob("*/attempt-0001/transport-ready.json")))
                < participants
            ):
                if time.monotonic() >= deadline:
                    raise AssertionError("Concurrent transport barrier timed out")
                await asyncio.sleep(0.01)
        await asyncio.sleep(self.scenario.get("delay_seconds", 0))
        if self.scenario.get("error"):
            raise RuntimeError(self.scenario["error"])
        if not self.replies:
            raise AssertionError("Offline transport exhausted scripted replies")
        reply = self.replies.pop(0)
        if reply.get("error"):
            raise RuntimeError(reply["error"])
        observations["calls"][-1]["end"] = time.monotonic()
        return Reply(
            content=reply["content"],
            tool_calls=[ToolCall(**call) for call in reply.get("tool_calls", [])],
            usage=Usage(**reply.get("usage", {"input": 10, "output": 5})),
            stop_reason=reply.get("stop_reason", "end_turn"),
            cost=None,
        )


def make_transport(provider, **kwargs):
    observations["factory_calls"].append({"provider": provider, **kwargs})
    invocation_path = artifact / "invocation.json" if artifact is not None else None
    invocation = (json.loads(invocation_path.read_bytes())
                  if invocation_path is not None and invocation_path.is_file() else {})
    selection = {
        "purpose": invocation.get("purpose", "review"),
        "attempt": artifact.name if artifact is not None else None,
        "prompt": arguments[1] if arguments and arguments[0] == "run" else None,
    }
    observations["selection"] = selection
    scenario = spec.get("systems", {}).get(option("--system"), spec.get("default", {}))
    for candidate in spec.get("scenarios", []):
        if (candidate["purpose"] == selection["purpose"]
                and candidate["attempt"] == selection["attempt"]
                and (selection["prompt"] or "").startswith(candidate["prompt_prefix"])
                and candidate.get("system", option("--system")) == option("--system")):
            scenario = candidate["scenario"]
            break
    return FakeProvider(scenario)


nare_cli.make_transport = make_transport
sys.argv = [console, *arguments]
try:
    runpy.run_path(console, run_name="__main__")
finally:
    if artifact is not None:
        (artifact / spec.get("observation_name", "offline-observations.json")).write_text(
            json.dumps(observations, indent=2) + "\n"
        )
