"""Offline executable probes for the engine-owned nare subprocess boundary."""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

import pytest
from test_review_inputs import CONFIG
from test_review_inputs import capture as capture

from scrutare.config import BudgetSettings, ModelRail, parse_config
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.persona_inputs import prepare_persona_inputs
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_artifacts import create_attempt_directory
from scrutare.engine.session_models import NareCapability, NareRuntime, TokenUsage
from scrutare.personas import PersonaDefinition


def module():
    return importlib.import_module("scrutare.engine.nare_session")


def executable(tmp_path, *, version="2026.10.0", contract=1, body=""):
    path = tmp_path / "fake-nare"
    path.write_text(f"#!{sys.executable}\n" + f"""
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print({version!r})
    sys.exit(0)
if sys.argv[1:] == ["contract"]:
    print(json.dumps({{"contract": {contract!r}, "nare": {version!r}}}))
    sys.exit(0)
""" + body)
    path.chmod(0o700)
    return path


def test_inspection_accepts_supported_external_version_and_contract(tmp_path):
    from scrutare.engine.session_models import NareRuntime
    runtime = NareRuntime(executable(tmp_path, version="2026.10.2"))
    capability = asyncio.run(module().inspect_nare_runtime(runtime))
    assert (capability.version, capability.contract) == ("2026.10.2", 1)


@pytest.mark.parametrize("version,contract", [
    ("2026.9.9", 1), ("0.0.0+source", 1), ("2026.10.0rc1", 1),
    ("secret-provider-error", 1), ("2026.10.0", 2), ("2026.10.0", True),
])
def test_inspection_rejects_unsupported_version_or_contract_safely(tmp_path, version, contract):
    from scrutare.engine.session_models import NareRuntime
    with pytest.raises(module().SessionRuntimeError) as error:
        asyncio.run(module().inspect_nare_runtime(NareRuntime(
            executable(tmp_path, version=version, contract=contract))))
    assert "secret-provider-error" not in str(error.value)


def test_inspection_missing_executable_is_safe(tmp_path):
    from scrutare.engine.session_models import NareRuntime
    with pytest.raises(module().SessionRuntimeError, match="executable"):
        asyncio.run(module().inspect_nare_runtime(NareRuntime(tmp_path / "missing")))



def setup(capture, *, system="Review security.", limit=10):
    inputs = prepare_review_inputs(capture, parse_config(CONFIG))
    descriptor = prepare_persona_inputs(inputs, (PersonaDefinition("security", system),))[0]
    ledger = ReviewBudgetLedger(("security",), BudgetSettings(limit, limit))
    lease = ledger.admit("security", "attempt-0001")
    attempt = create_attempt_directory(capture, "security", prepared_root=inputs.root)
    return descriptor, ledger, lease, attempt


RUN_BODY = '''
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run"])
parser.add_argument("prompt")
for flag in ("system", "provider", "model", "base-url", "tools", "root", "contract", "schema",
             "budget-tokens", "session", "max-turns"):
    parser.add_argument("--" + flag)
parser.add_argument("--yes", action="store_true")
parser.add_argument("--jsonl", action="store_true")
a = parser.parse_args()
Path("observed.json").write_text(json.dumps({"argv": sys.argv[1:], "system": a.system,
    "model": a.model, "cwd": os.getcwd(), "environment": dict(os.environ),
    "stdin": sys.stdin.read(), "schema": json.loads(Path(a.schema).read_text())}))
USAGE = {"input": INPUT, "output": 2, "cache_read": 3, "cache_write": 1, "cost": None}
print(json.dumps({"type": "cost", "text": "", "detail": USAGE, "timestamp": "now"}), flush=True)
EXTRA
result = {"type": "result", "session_id": "stable-id", "status": STATUS,
    "questions": [], "usage": USAGE, "stop_reason": STOP, "turns": 1,
    "contract": 1, "nare": "2026.10.0", "output": OUTPUT,
    "budget": {"tokens": int(a.budget_tokens), "used_tokens": sum(USAGE[k] for k in
        ("input", "output", "cache_read", "cache_write")), "used_usd": None}, "error": None}
saved = {"id": "stable-id", "usage": USAGE, "output": OUTPUT, "status": STATUS,
    "stop_reason": STOP, "policy": {"tools": [a.tools], "root": a.root},
    "budget": {"tokens": int(a.budget_tokens)}, "contract": 1, "nare": "2026.10.0"}
Path(a.session).write_text(json.dumps(saved))
os.chmod(a.session, 0o600)
print(json.dumps(result), flush=True)
sys.exit(EXIT)
'''


def running_executable(tmp_path, *, input_tokens=4, status="done", stop="end_turn",
                       output=None, extra="", exit_code=0):
    # Literal contract-shaped terminal and snapshot; no SDK, provider or credentials.
    body = (RUN_BODY.replace("INPUT", repr(input_tokens)).replace("STATUS", repr(status))
            .replace("STOP", repr(stop)).replace("OUTPUT", repr(
                {"findings": []} if output is None else output))
            .replace("EXTRA", extra).replace("EXIT", repr(exit_code)))
    return executable(tmp_path, body=body)


def run(descriptor, ledger, lease, attempt, path, *, rail=None, timeout=5):
    return asyncio.run(module().run_persona_session(
        descriptor, rail or ModelRail("openai", None, "fixture-model"), lease,
        ledger=ledger, artifact_directory=attempt, runtime=NareRuntime(path, 7, timeout),
        capability=NareCapability("2026.10.0", 1)))


@pytest.mark.parametrize("input_tokens,status,stop,exit_code,want", [
    (3, "done", "end_turn", 0, "complete"),
    (4, "done", "end_turn", 0, "complete"),
    (5, "done", "end_turn", 0, "partial"),
    (5, "error", "budget", 1, "partial"),
    (3, "error", None, 1, "failed"),
    (3, "blocked", "tool_use", 0, "failed"),
])
def test_terminal_classification_and_disjoint_actual_accounting(
        capture, tmp_path, input_tokens, status, stop, exit_code, want):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(
        tmp_path, input_tokens=input_tokens, status=status, stop=stop, exit_code=exit_code))
    assert outcome.status == want
    assert outcome.usage == TokenUsage(input_tokens, 2, 3, 1)
    assert ledger.usage == outcome.usage
    assert outcome.overshoot_tokens == max(0, input_tokens - 4)
    assert outcome.session_id == "stable-id"
    assert ledger.snapshot()["active_reservations"] == []
    assert outcome.accounting_complete == (status != "error" or stop == "budget")
    result = json.loads((attempt / "result.json").read_bytes())
    assert result["usage"] == {"input": input_tokens, "output": 2, "cache_read": 3,
                                "cache_write": 1, "total": input_tokens + 6}
    assert result["invocation_limit"] == 10
    assert result["allocated_tokens"] == 10
    assert (attempt / "stdout.jsonl").read_bytes().endswith(b"\n")
    assert (attempt / "session.json").is_file()


@pytest.mark.parametrize("system,model", [
    ("Review spaces.\nPreserve newline.", "space model"),
    ("--flag leading\nexact text", "--model-looking-value"),
])
def test_exact_argv_private_environment_and_no_auth_capture(capture, tmp_path, monkeypatch,
                                                           system, model):
    for key in ("NARE_PROVIDER", "NARE_MODEL", "NARE_BASE_URL", "NARE_BUDGET_USD",
                "NARE_INPUT_PRICE", "GITHUB_TOKEN", "GH_TOKEN", "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY", "PYTHONPATH"):
        monkeypatch.setenv(key, "ambient-secret-sentinel")
    # Credentials are deliberately absent for the selected fixture provider.
    monkeypatch.delenv("OPENAI_API_KEY")
    descriptor, ledger, lease, attempt = setup(capture, system=system)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(tmp_path),
                  rail=ModelRail("openai", "https://example.invalid/v1", model))
    assert outcome.status == "complete"
    observed = json.loads((attempt / "cwd" / "observed.json").read_bytes())
    assert observed["system"] == system
    assert observed["model"] == model
    args = observed["argv"]
    assert args[:7] == ["run", descriptor.prompt, "--tools", "read", "--root",
                        str(descriptor.inputs.root), "--system=" + system]
    for flag, value in (("--provider", "openai"), ("--base-url", "https://example.invalid/v1"),
                        ("--contract", "1"), ("--budget-tokens", "10"),
                        ("--session", str(attempt / "session.json")), ("--max-turns", "7")):
        assert args[args.index(flag) + 1] == value
    assert "--jsonl" in args and "--yes" in args
    assert observed["schema"]["required"] == ["findings"]
    assert observed["stdin"] == ""
    env = observed["environment"]
    assert env["HOME"] == str(attempt / "home")
    assert env["TMPDIR"] == str(attempt / "tmp")
    assert not any(key.startswith("NARE_") for key in env)
    assert "ambient-secret-sentinel" not in json.dumps(env)
    assert "ambient-secret-sentinel" not in (attempt / "invocation.json").read_text()
    assert "ambient-secret-sentinel" not in (attempt / "result.json").read_text()
    assert all(p.stat().st_mode & 0o077 == 0 for p in attempt.iterdir())


def test_bad_stream_keeps_capture_and_usage_lower_bound(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = running_executable(tmp_path, extra='print("not-json", flush=True)')
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "failed" and not outcome.accounting_complete
    assert outcome.usage == TokenUsage(4, 2, 3, 1)
    assert ledger.usage == outcome.usage
    assert b"not-json" in (attempt / "stdout.jsonl").read_bytes()
    assert ledger.admit("security", "another") is None
    assert ledger.snapshot()["active_reservations"] == []


def test_streams_drain_independently_without_deadlock(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    extra = 'sys.stderr.write("private-provider-sentinel" + "x" * 300000); sys.stderr.flush()'
    outcome = run(descriptor, ledger, lease, attempt,
                  running_executable(tmp_path, extra=extra))
    assert outcome.status == "complete"
    assert (attempt / "stderr.txt").stat().st_size > 300000
    assert "private-provider-sentinel" not in (attempt / "result.json").read_text()


def test_missing_terminal_retains_cost_and_closes_admission(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = running_executable(tmp_path, extra='sys.exit(1)')
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "failed" and not outcome.accounting_complete
    assert outcome.usage.total == 10
    assert not (attempt / "session.json").exists()
    assert ledger.snapshot()["active_reservations"] == []


def test_never_started_exit2_is_known_zero_with_raw_stderr(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = executable(tmp_path, body='sys.stderr.write("credential-sentinel"); sys.exit(2)')
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "failed" and outcome.accounting_complete
    assert outcome.usage == TokenUsage()
    assert ledger.admit("security", "another") is not None
    assert (attempt / "stderr.txt").read_text() == "credential-sentinel"
    assert "credential-sentinel" not in (attempt / "result.json").read_text()


@pytest.mark.parametrize("moment", ["before", "after"])
def test_revalidates_prepared_artifacts_before_launch_and_after_completion(capture, tmp_path,
                                                                         moment):
    descriptor, ledger, lease, attempt = setup(capture)
    extra = 'Path(a.root, "diff.patch").write_text("changed")' if moment == "after" else ""
    path = running_executable(tmp_path, extra=extra)
    if moment == "before":
        (descriptor.inputs.root / "diff.patch").write_bytes(b"changed")
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "failed"
    assert outcome.reason == "inputs"
    assert outcome.accounting_complete
    assert outcome.usage.total == (0 if moment == "before" else 10)
    assert (attempt / "cwd" / "observed.json").exists() == (moment == "after")


@pytest.mark.parametrize("destination", ["stdout.jsonl", "session.json", "cwd"])
def test_existing_or_symlinked_attempt_destinations_are_never_overwritten(capture, tmp_path,
                                                                         destination):
    descriptor, ledger, lease, attempt = setup(capture)
    outside = tmp_path / "outside"
    outside.write_text("preexisting-sentinel")
    (attempt / destination).symlink_to(outside)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(tmp_path))
    assert outcome.status == "failed" and outcome.accounting_complete
    assert outside.read_text() == "preexisting-sentinel"
    assert (attempt / destination).is_symlink()
    assert not (attempt / "cwd" / "observed.json").exists()


def test_done_with_budget_stop_at_exact_limit_is_unsupported(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = run(descriptor, ledger, lease, attempt,
                  running_executable(tmp_path, stop="budget"))
    assert outcome.status == "failed" and outcome.reason == "protocol"


def test_capture_write_failure_after_spawn_is_uncertain(capture, tmp_path, monkeypatch):
    descriptor, ledger, lease, attempt = setup(capture)
    original = module()._new_capture

    class BrokenCapture:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            raise OSError("disk-secret-sentinel")

    def open_capture(directory, name):
        stream = original(directory, name)
        return BrokenCapture(stream) if name == "stdout.jsonl" else stream

    monkeypatch.setattr(module(), "_new_capture", open_capture)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(tmp_path))
    assert outcome.status == "failed" and not outcome.accounting_complete
    assert ledger.admit("security", "another") is None
    assert "disk-secret-sentinel" not in (attempt / "result.json").read_text()


def test_turn_usage_observed_before_terminal_and_reconciled_once(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture, limit=20)
    path = running_executable(tmp_path, extra='import time; time.sleep(0.15)')

    async def watch():
        task = asyncio.create_task(module().run_persona_session(
            descriptor, ModelRail("openai", None, "fixture"), lease, ledger=ledger,
            artifact_directory=attempt, runtime=NareRuntime(path),
            capability=NareCapability("2026.10.0", 1)))
        for _ in range(200):
            if ledger.usage.total:
                break
            await asyncio.sleep(0.005)
        assert ledger.usage == TokenUsage(4, 2, 3, 1)
        assert not task.done()
        outcome = await task
        assert outcome.usage == ledger.usage == TokenUsage(4, 2, 3, 1)

    asyncio.run(watch())


def test_budget_stop_can_preserve_nullable_output(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = running_executable(tmp_path, status="error", stop="budget", exit_code=1)
    path.write_text(path.read_text().replace("{'findings': []}", "None"))
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "partial"
    assert not outcome.output_available and outcome.findings == ()
    assert outcome.accounting_complete and outcome.usage.total == 10


@pytest.mark.parametrize("change", ["usage", "version", "contract", "output"])
def test_conflicting_terminal_evidence_keeps_incremental_lower_bound(capture, tmp_path, change):
    descriptor, ledger, lease, attempt = setup(capture)
    path = running_executable(tmp_path)
    text = path.read_text()
    if change == "usage":
        text = text.replace('print(json.dumps(result), flush=True)',
                            'result["usage"]["input"] = 3; print(json.dumps(result), flush=True)')
    elif change == "output":
        text = text.replace("{'findings': []}", "{'findings': [{'problem': 'secret-value'}]}")
    else:
        text = text.replace('"2026.10.0"' if change == "version" else '"contract": 1',
                            '"2026.10.1"' if change == "version" else '"contract": 2')
    path.write_text(text)
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "failed" and not outcome.accounting_complete
    assert outcome.usage == TokenUsage(4, 2, 3, 1)
    assert ledger.snapshot()["active_reservations"] == []
    assert "secret-value" not in (attempt / "result.json").read_text()


@pytest.mark.parametrize("rail", [ModelRail("unknown", None, "model"),
    ModelRail("openai", None, ""), ModelRail("openai", "file:///private", "model"),
    ModelRail("openai", "https://invalid:bad", "model"),
    ModelRail("openai", None, "model\x00")])
def test_invalid_direct_model_rail_never_starts(capture, tmp_path, rail):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(tmp_path), rail=rail)
    assert outcome.status == "failed" and outcome.accounting_complete
    assert outcome.usage.total == 0
    assert not (attempt / "cwd").exists()
    assert ledger.snapshot()["active_reservations"] == []


HANG_BODY = '''
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = os.fork()
if child == 0:
    while True:
        time.sleep(1)
with Path("pids.json.tmp").open("w") as publication:
    Path("pids-writing.marker").touch()
    time.sleep(0.0)  # PID_PUBLICATION_DELAY: expose open-before-complete readiness.
    publication.write(json.dumps([os.getpid(), child]))
Path("pids.json.tmp").replace("pids.json")
while True:
    time.sleep(1)
'''


def alive(pid):
    # Killed grandchildren may await the host init's reap; they cannot run or retain pipes.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


async def fixture_pids(path, task, *, spawned=None):
    """Wait for the fixture's atomically published PID marker, with a clear bound."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while True:
        if path.exists() and (spawned is None or spawned):
            return json.loads(path.read_text())
        if task.done():
            pytest.fail("Fixture session ended before publishing its complete PID marker")
        if loop.time() >= deadline:
            pytest.fail("Fixture did not publish its complete PID marker within 1 second")
        await asyncio.sleep(0.005)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("publication_delay", [0.0, 0.05])
def test_timeout_and_cancellation_kill_owned_group_and_settle(capture, tmp_path, cancel,
                                                            publication_delay):
    descriptor, ledger, lease, attempt = setup(capture)
    body = RUN_BODY.split("EXTRA")[0].replace("INPUT", "4") + HANG_BODY
    body = body.replace("time.sleep(0.0)", f"time.sleep({publication_delay})")
    path = executable(tmp_path, body=body)

    async def lifecycle():
        task = asyncio.create_task(module().run_persona_session(
            descriptor, ModelRail("openai", None, "fixture"), lease,
            ledger=ledger, artifact_directory=attempt,
            runtime=NareRuntime(path, timeout_seconds=5 if cancel else 0.2),
            capability=NareCapability("2026.10.0", 1)))
        try:
            pids = await fixture_pids(attempt / "cwd" / "pids.json", task)
            assert (attempt / "cwd" / "pids-writing.marker").exists()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                outcome = await task
                assert outcome.status == "failed" and outcome.reason == "timeout"
            assert all(not alive(pid) for pid in pids)
            assert not Path(f"/proc/{pids[0]}").exists()
            assert ledger.usage == TokenUsage(4, 2, 3, 1)
            assert not ledger.accounting_complete
            assert ledger.snapshot()["active_reservations"] == []
            result = json.loads((attempt / "result.json").read_bytes())
            assert result["reason"] == ("cancelled" if cancel else "timeout")
            assert b'"type": "cost"' in (attempt / "stdout.jsonl").read_bytes()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(lifecycle())


def test_inspection_contract_rejection_names_safe_field(tmp_path):
    with pytest.raises(module().SessionRuntimeError, match="^contract:"):
        asyncio.run(module().inspect_nare_runtime(NareRuntime(executable(tmp_path, contract=2))))


def test_inspection_rejects_duplicate_contract_fields(tmp_path):
    path = executable(tmp_path)
    path.write_text(path.read_text().replace(
        'print(json.dumps({"contract": 1, "nare": \'2026.10.0\'}))',
        'print(\'{"contract":2,"contract":1,"nare":"2026.10.0"}\')'))
    with pytest.raises(module().SessionRuntimeError):
        asyncio.run(module().inspect_nare_runtime(NareRuntime(path)))


def test_inspection_has_private_environment_and_only_metadata_commands(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "NARE_BASE_URL"):
        monkeypatch.setenv(key, "credential-sentinel")
    path = executable(tmp_path)
    log = tmp_path / "probes.jsonl"
    path.write_text(path.read_text().replace('if sys.argv[1:]',
        f'with Path({str(log)!r}).open("a") as stream:\n'
        '    stream.write(json.dumps({"args": sys.argv[1:], "env": dict(os.environ), '
        '"cwd": os.getcwd(), "stdin": sys.stdin.read()}) + "\\n")\n'
        'if sys.argv[1:]', 1))
    capability = asyncio.run(module().inspect_nare_runtime(NareRuntime(path)))
    assert capability.version == "2026.10.0"
    probes = [json.loads(line) for line in log.read_text().splitlines()]
    assert [p["args"] for p in probes] == [["--version"], ["contract"]]
    assert all(p["cwd"] == p["env"]["HOME"] and p["stdin"] == "" for p in probes)
    assert "credential-sentinel" not in log.read_text()


def test_cancellation_during_spawn_still_reaps_the_owned_group(capture, tmp_path, monkeypatch):
    import signal

    descriptor, ledger, lease, attempt = setup(capture)
    path = executable(tmp_path, body=HANG_BODY)
    original = module().asyncio.create_subprocess_exec
    spawned = []

    async def slow_spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        spawned.append(process)
        await asyncio.sleep(0.05)
        return process

    monkeypatch.setattr(module().asyncio, "create_subprocess_exec", slow_spawn)

    async def cancel_spawn():
        task = asyncio.create_task(module().run_persona_session(
            descriptor, ModelRail("openai", None, "fixture"), lease, ledger=ledger,
            artifact_directory=attempt, runtime=NareRuntime(path),
            capability=NareCapability("2026.10.0", 1)))
        try:
            pids = await fixture_pids(attempt / "cwd" / "pids.json", task, spawned=spawned)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert all(not alive(pid) for pid in pids)
            assert not Path(f"/proc/{pids[0]}").exists()
            assert not ledger.accounting_complete
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A failed assertion must also leave no active external process behind.
            for process in spawned:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()

    asyncio.run(cancel_spawn())


def test_late_result_write_failure_keeps_session_and_capture(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    path = running_executable(tmp_path,
                              extra='Path(a.session).with_name("result.json").write_text("existing")')
    outcome = run(descriptor, ledger, lease, attempt, path)
    assert outcome.status == "failed" and outcome.reason == "artifacts"
    assert outcome.accounting_complete and outcome.usage.total == 10
    assert (attempt / "result.json").read_text() == "existing"
    assert (attempt / "session.json").is_file()
    assert (attempt / "stdout.jsonl").is_file()
    assert ledger.snapshot()["active_reservations"] == []


@pytest.fixture(autouse=True)
def credential_free_environment(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_nonregular_saved_session_fails_without_blocking(tmp_path):
    import multiprocessing

    directory = tmp_path / "attempt"
    directory.mkdir(mode=0o700)
    os.mkfifo(directory / "session.json", 0o600)

    def read_fifo():
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(module().SessionArtifactError):
                module()._session_document(descriptor)
        finally:
            os.close(descriptor)

    process = multiprocessing.get_context("fork").Process(target=read_fifo)
    process.start()
    process.join(0.3)
    try:
        assert not process.is_alive(), "nonregular session must not block the event loop"
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
        process.join()


def test_untyped_base_url_fails_safely_before_process_and_settles(capture, tmp_path):
    descriptor, ledger, lease, attempt = setup(capture)
    outcome = run(descriptor, ledger, lease, attempt, running_executable(tmp_path),
                  rail=ModelRail("openai", 42, "model"))
    assert outcome.status == "failed" and outcome.accounting_complete
    assert ledger.snapshot()["active_reservations"] == []
    assert not (attempt / "cwd").exists()


def test_cancelled_spawn_failure_preserves_cancellation_and_known_no_start(capture, tmp_path,
                                                                        monkeypatch):
    descriptor, ledger, lease, attempt = setup(capture)
    path = running_executable(tmp_path)

    async def failed_spawn(*args, **kwargs):
        await asyncio.sleep(0.05)
        raise OSError("private-executable-diagnostic")

    monkeypatch.setattr(module().asyncio, "create_subprocess_exec", failed_spawn)

    async def cancel():
        task = asyncio.create_task(module().run_persona_session(
            descriptor, ModelRail("openai", None, "fixture"), lease, ledger=ledger,
            artifact_directory=attempt, runtime=NareRuntime(path),
            capability=NareCapability("2026.10.0", 1)))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ledger.accounting_complete
        assert ledger.usage == TokenUsage()
        assert ledger.snapshot()["active_reservations"] == []

    asyncio.run(cancel())
