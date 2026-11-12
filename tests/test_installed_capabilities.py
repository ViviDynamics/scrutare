"""The pinned console's own contract data drives what a session is sent."""

import asyncio
import json
import shutil
from dataclasses import replace

from test_fanout import configure
from test_installed_structured_output import http_scenario
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import load, observations, offline_runtime, single, text
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine import fanout
from scrutare.engine.nare_session import inspect_nare_runtime
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

SYSTEM = "--lead with a dash.\r\nKeep CRLF, a tab\t and \u2713 \u00e9.\n\n"


def test_installed_runtime_advertises_every_option_scrutare_gates(tmp_path, installed):
    capability = asyncio.run(inspect_nare_runtime(offline_runtime(tmp_path, installed)))
    assert (capability.version, capability.contract) == ("2026.11.99", 3)
    for switch in ("--stream", "--jsonl", "--yes", "--cache-breakpoints"):
        flag = capability.flag(switch)
        assert flag is not None and not flag.takes_value
    for option in ("--max-cutoffs", "--request-retries", "--request-retry-seconds",
                   "--max-corrections", "--proxy-num-retries", "--system-file",
                   "--prompt-file", "--compaction-trigger", "--compaction-target"):
        flag = capability.flag(option)
        assert flag is not None and flag.takes_value and flag.choices is None
    assert capability.advertises("--compaction", "elide")
    assert capability.advertises("--structured-output", "json_schema")
    assert capability.advertises("--effort", "high")
    assert not capability.advertises("--effort", "extreme")
    assert capability.structured_output == ("json_schema",)
    assert capability.structured_output_providers == ("anthropic", "openai")
    assert capability.proxy_num_retries


def test_installed_session_accepts_the_explicit_recovery_and_compaction_values(
        capture, tmp_path, installed):
    outcome, ledger, _ = single(capture, tmp_path, installed, {"replies": [text()]})
    assert outcome.status == "complete" and ledger.accounting_complete
    argv = load(outcome.artifact_directory / "invocation.json")["argv"]
    for flag, value in (("--max-cutoffs", "0"), ("--request-retries", "0"),
                        ("--max-corrections", "1"), ("--compaction", "elide"),
                        ("--compaction-trigger", "0.8"), ("--compaction-target", "0.6")):
        assert argv.count(flag) == 1 and argv[argv.index(flag) + 1] == value
    # The console received exactly the recorded arguments.
    assert observations(outcome)["arguments"] == argv[1:]


def exact_review(capture, tmp_path, installed):
    document = configure(capture, personas=["senior-dev"], review=200, per=200).to_dict()
    document["personas"] = [{"name": "exact", "system_prompt": SYSTEM}]
    document["models"]["default"] = {
        "provider": "openai", "model": "offline-model",
        "base_url": "https://offline.invalid/v1"}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    tmp_path.mkdir(exist_ok=True)
    runtime = offline_runtime(tmp_path, installed, default=http_scenario())
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    spec["environment"] = {"OPENAI_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    assert result.status == "complete"
    outcome = result.initial.outcomes[0]
    assert load(capture / "fanout.json")["personas"][0]["system_prompt"] == SYSTEM
    payloads = [call["payload"] for call in observations(outcome, http_sse=True)["calls"]]
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0
    return outcome.artifact_directory, payloads


def test_installed_file_inputs_send_the_request_argv_sent(
        capture, tmp_path, installed, monkeypatch):
    # Two identical captures: one reviewed through the files, one through argv.
    second = tmp_path / "argv-route" / capture.name
    shutil.copytree(capture, second)
    attempt, by_file = exact_review(capture, tmp_path, installed)
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[2:4] == ["--prompt-file", str(attempt / "task-prompt.txt")]
    assert argv[argv.index("--system-file") + 1] == str(attempt / "system-prompt.txt")
    assert (attempt / "system-prompt.txt").read_bytes() == SYSTEM.encode("utf-8")
    assert not any(SYSTEM in item for item in argv)
    task = (attempt / "task-prompt.txt").read_bytes().decode("utf-8")
    assert task.startswith("Review the captured changes") and task not in argv

    # The same console, inspected as a runtime from before the file flags existed.
    inspect = fanout.inspect_nare_runtime

    async def without_file_flags(runtime):
        found = await inspect(runtime)
        return replace(found, flags=tuple(
            flag for flag in found.flags
            if flag.name not in ("--system-file", "--prompt-file")))

    monkeypatch.setattr(fanout, "inspect_nare_runtime", without_file_flags)
    attempt, by_argv = exact_review(second, second.parent, installed)
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[2] == task and "--system=" + SYSTEM in argv
    assert "--prompt-file" not in argv and not (attempt / "task-prompt.txt").exists()

    # Every request the real transport built is the same, byte for byte, on both routes.
    assert len(by_file) == 3 and by_file == by_argv
    assert json.dumps(by_file, sort_keys=True) == json.dumps(by_argv, sort_keys=True)
    for body in by_file:
        assert body["messages"][0] == {"role": "system", "content": SYSTEM}
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"].startswith(task)
