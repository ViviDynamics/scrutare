"""qare-shaped stdio MCP protocol and production review parity."""

import asyncio
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed
from test_panel import install
from test_post_review import REF, CaptureClient, FakePoster
from test_review_run import RAW


def request(method, params=None, id=1):
    return json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def call(line):
    module = importlib.import_module("scrutare.interfaces.mcp")
    return asyncio.run(module.handle_line(line))


def test_discovery_and_notifications():
    result = call(request("initialize", {"protocolVersion": "2024-11-05"}))
    assert result["result"]["protocolVersion"] == "2024-11-05"
    assert result["result"]["capabilities"] == {"tools": {}}
    tool = call(request("tools/list"))["result"]["tools"][0]
    assert tool["name"] == "review"
    assert tool["inputSchema"]["required"] == ["pr"]
    assert set(tool["inputSchema"]["properties"]) == {"pr", "config", "nare_executable"}
    assert call('{"jsonrpc":"2.0","method":"notifications/initialized"}') is None
    assert call('{"jsonrpc":"2.0","method":"tools/call","params":{"name":"review"}}') is None
    assert call("  ") is None


@pytest.mark.parametrize("line,code", [
    ("{", -32700), ("[]", -32600), ("{}", -32600),
    ('{"id":1,"method":"tools/list"}', -32600),
    (request("unknown"), -32601),
    (request("tools/call", {"name": "unknown"}), -32602),
    (request("tools/call", {"name": "review", "arguments": {}}), -32602),
    (request("tools/call", {"name": "review", "arguments": {"pr": 1}}), -32602),
    (request("tools/call", {"name": "review", "arguments": {"pr": "1", "config": ""}}), -32602),
    (request("tools/call", {"name": "review", "arguments": {"pr": "1", "extra": True}}), -32602),
])
def test_protocol_errors(line, code):
    assert call(line)["error"]["code"] == code


def test_review_matches_shared_service_and_artifacts(tmp_path, monkeypatch):
    module = importlib.import_module("scrutare.interfaces.mcp")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scrutare.yaml").write_bytes(RAW)
    install(monkeypatch, {})
    monkeypatch.setattr(module, "resolve_pr", lambda pr: REF)
    from scrutare.engine import review
    monkeypatch.setattr(review, "GitHubClient", CaptureClient)
    monkeypatch.setattr(review, "post_review", lambda run, verdict, **kw: __import__(
        "scrutare.poster", fromlist=["post_review"]).post_review(run, verdict, client=FakePoster()))
    response = call(request("tools/call", {"name": "review", "arguments": {
        "pr": "1", "nare_executable": sys.executable,
    }}))
    assert "isError" not in response["result"]
    output = response["result"]["content"][0]["text"]
    data = json.loads(output)
    run = Path(data["run_dir"])
    assert output.encode() == (run / "result.json").read_bytes()
    assert data["status"] == "posted" and data["verdict"] == "approve"
    manifest = json.loads((run / "artifacts.json").read_bytes())
    paths = {entry["path"] for entry in manifest["artifacts"]}
    assert {"result.json", "posting.json", "verdict.json", "config.yaml"} <= paths
    assert "panel.json" in paths
    from scrutare.replay import replay_run
    assert replay_run(run).exit_code == 0


def test_config_failure_is_tool_error_and_server_survives(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = call(request("tools/call", {"name": "review", "arguments": {"pr": "1"}}))
    assert response["result"]["isError"] is True
    assert "cannot read config file" in response["result"]["content"][0]["text"]
    assert not (tmp_path / ".scrutare").exists()
    assert "result" in call(request("tools/list"))


def test_stdio_module_outputs_only_json_and_handles_final_unterminated_line():
    process = subprocess.run([sys.executable, "-m", "scrutare.interfaces.mcp"],
                             input=request("initialize") + "\n" + request("tools/list", id=2),
                             capture_output=True, text=True, check=True)
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2]
    assert process.stderr == ""


def test_installed_mcp_matches_cli_real_sessions(tmp_path, installed, wheel_cli):
    from test_installed_review import GH_WORKER
    from test_nare_cli_integration import offline_runtime, text
    from test_review_inputs import SOURCE

    _, python, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    (tmp_path / "offline-spec.json").write_text(json.dumps({
        "default": {"replies": [text({"findings": []})]},
    }))
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_WORKER.read_text())
    gh.chmod(0o700)
    spec = tmp_path / "gh-spec.json"
    spec.write_text(json.dumps({
        "case": "approval", "diff": SOURCE.decode(),
        "files": [{"filename": "src/app.py", "status": "modified"}],
        "metadata": {"number": 12, "state": "open", "merged": False,
                     "head": {"sha": "a" * 40}, "changed_files": 1,
                     "body": "", "base": {"ref": "main", "sha": "base123",
                     "repo": {"full_name": "owner/repo"}}},
    }))
    env = {"PATH": f"{binary}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8",
           "SCRUTARE_GH_SPEC": str(spec), "SCRUTARE_GH_LOG": str(tmp_path / "gh.jsonl")}
    outputs = []
    runs = []
    for name in ("cli", "mcp"):
        working = tmp_path / name
        working.mkdir()
        (working / "scrutare.yaml").write_text(
            "personas: [senior-dev]\nmodels: {default: {model: offline-model}}\n")
        if name == "cli":
            process = subprocess.run([str(console), "review", "--pr", "12", "--nare-executable",
                                      str(runtime.executable)], cwd=working, env=env,
                                     capture_output=True, text=True, timeout=60)
            assert process.returncode == 0, process.stderr
            output = process.stdout
        else:
            process = subprocess.run([str(python.parent / "scrutare-mcp")],
                                     input=request("tools/call", {"name": "review", "arguments": {
                                         "pr": "12", "nare_executable": str(runtime.executable),
                                     }}) + "\n", cwd=working, env=env,
                                     capture_output=True, text=True, timeout=60)
            assert process.returncode == 0, process.stderr
            response = json.loads(process.stdout)["result"]
            assert not response.get("isError"), response
            output = response["content"][0]["text"]
        data = json.loads(output)
        run = Path(data.pop("run_dir"))
        assert output.encode() == (run / "result.json").read_bytes()
        assert (run / "sessions/senior-dev/attempt-0001/session.json").is_file()
        posting = json.loads((run / "posting.json").read_bytes())
        marker = posting["run_id"]
        outputs.append(json.loads(json.dumps(data).replace(marker, "RUN_ID")))
        runs.append(run)
    assert outputs[0] == outputs[1]
    markers = [json.loads((run / "posting.json").read_bytes())["run_id"] for run in runs]
    for artifact in ("findings.json", "verdict.json", "review-payload.json",
                     "config.yaml", "diff.patch"):
        assert (runs[0] / artifact).read_text().replace(markers[0], "RUN_ID") == (
            runs[1] / artifact).read_text().replace(markers[1], "RUN_ID")
