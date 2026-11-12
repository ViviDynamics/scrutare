"""The pinned console's own contract data drives what a session is sent."""

import asyncio

from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import load, observations, offline_runtime, single, text
from test_review_inputs import capture as capture

from scrutare.engine.nare_session import inspect_nare_runtime


def test_installed_runtime_advertises_every_option_scrutare_gates(tmp_path, installed):
    capability = asyncio.run(inspect_nare_runtime(offline_runtime(tmp_path, installed)))
    assert (capability.version, capability.contract) == ("2026.11.94", 3)
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
