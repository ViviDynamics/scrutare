"""Offline proof through an explicitly installed external nare console script."""

import asyncio
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest
from test_fanout import configure
from test_review_inputs import capture as capture

from scrutare.config import BudgetSettings
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.fanout import fan_out
from scrutare.engine.nare_session import inspect_nare_runtime, run_persona_session
from scrutare.engine.persona_inputs import prepare_persona_inputs
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_artifacts import create_attempt_directory
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.engine.session_output import decode_session
from scrutare.personas import load_persona

SETTING = "SCRUTARE_TEST_NARE_EXECUTABLE"
WORKER = Path(__file__).parent / "helpers" / "nare_offline_worker.py"
FINDING = {
    "findings": [
        {
            "file": "src/app.py",
            "line": 1,
            "side": "RIGHT",
            "category": "correctness",
            "problem": "Candidate",
            "reason": "Review",
        }
    ]
}


def text(document=FINDING, *, usage=None):
    reply = {"content": [{"type": "text", "text": json.dumps(document)}]}
    if usage is not None:
        reply["usage"] = usage
    return reply


def tool(name="read", args=None, *, document=None, call_id="call"):
    args = {"path": "diff.patch"} if args is None else args
    content = [] if document is None else [{"type": "text", "text": json.dumps(document)}]
    content.append({"type": "tool_use", "id": call_id, "name": name, "input": args})
    return {
        "content": content,
        "tool_calls": [{"id": call_id, "name": name, "args": args}],
        "stop_reason": "tool_use",
    }


@pytest.fixture
def installed():
    value = os.environ.get(SETTING)
    if value is None:
        pytest.skip(f"optional installed nare proof requires {SETTING}")
    console = Path(value)
    assert console.is_absolute() and console.is_file() and os.access(console, os.X_OK), SETTING
    shebang = console.read_text().splitlines()[0]
    assert shebang.startswith("#!/"), "installed console must identify its external interpreter"
    interpreter = Path(shlex.split(shebang[2:])[0])
    assert interpreter.is_file(), "installed console interpreter is missing"
    result = subprocess.run(
        [str(console), "--version"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout.strip() == "2026.10.4", result.stderr
    return console, interpreter


def offline_runtime(tmp_path, installed, *, default=None, systems=None):
    console, interpreter = installed
    spec = tmp_path / "offline-spec.json"
    spec.write_text(json.dumps({"default": default or {}, "systems": systems or {}}))
    launcher = tmp_path / "offline-nare"
    launcher.write_text(
        f"#!{interpreter} -B\nimport runpy,sys\n"
        f"sys.argv = [{str(WORKER.resolve())!r}, {str(console)!r}, "
        f"{str(spec)!r}, *sys.argv[1:]]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    launcher.chmod(0o700)
    return NareRuntime(launcher, max_turns=10, timeout_seconds=15)


def load(path):
    return json.loads(path.read_bytes())


def observations(outcome):
    result = load(outcome.artifact_directory / "offline-observations.json")
    assert result["guard_violations"] == []
    assert result["credential_names"] == []
    assert result["nare_controls"] == {}
    assert result["version"] == "2026.10.4"
    assert result["python"].startswith("3.14.")
    assert all(Path(path).is_relative_to(outcome.artifact_directory) for path in result["writes"])
    return result


def single(capture, tmp_path, installed, scenario, *, limit=100, empty=False, timeout=15):
    config = configure(capture, personas=["senior-dev"], review=limit, per=limit, empty=empty)
    inputs = prepare_review_inputs(capture, config)
    descriptor = prepare_persona_inputs(inputs, config.personas)[0]
    ledger = ReviewBudgetLedger(("senior-dev",), config.budgets)
    lease = ledger.admit("senior-dev", "session")
    directory = create_attempt_directory(capture, "senior-dev", prepared_root=inputs.root)
    runtime = offline_runtime(tmp_path, installed, default=scenario)
    session_runtime = NareRuntime(runtime.executable, max_turns=10, timeout_seconds=timeout)

    async def execute():
        capability = await inspect_nare_runtime(runtime)
        return await run_persona_session(
            descriptor,
            config.models.default,
            lease,
            ledger=ledger,
            artifact_directory=directory,
            runtime=session_runtime,
            capability=capability,
        )

    return asyncio.run(execute()), ledger, inputs


def test_actual_wave_overlaps_processes_isolates_systems_and_rails(
    capture, tmp_path, installed, monkeypatch
):
    config = configure(capture, review=50, per=25)
    document = config.to_dict()
    document["models"]["overrides"] = {
        "custom": {
            "provider": "openai",
            "model": "custom-model",
            "base_url": "https://offline.invalid/v1",
        }
    }
    from scrutare.config import parse_config

    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    monkeypatch.setenv("NARE_BASE_URL", "https://ambient.invalid/v1")
    monkeypatch.setenv("NARE_TOOLS", "write,bash")
    monkeypatch.setenv("OPENAI_API_KEY", "offline-sentinel")
    systems = {
        load_persona("senior-dev").system_prompt: {
            "replies": [tool(document=FINDING), tool(call_id="second")],
            "barrier_participants": 2,
        },
        "Custom SYSTEM": {
            "replies": [tool(document={"findings": []}), tool(call_id="second")],
            "barrier_participants": 2,
        },
    }
    runtime = offline_runtime(tmp_path, installed, systems=systems)
    inputs = prepare_review_inputs(capture, config)
    before = {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()}
    result = asyncio.run(fan_out(capture, config, runtime=runtime))
    assert [o.status for o in result.outcomes] == ["partial", "partial"]
    assert result.usage.total == 60 and result.review_overshoot_tokens == 10
    assert result.partial and result.review_exhausted and not result.failed
    left, right = [observations(o) for o in result.outcomes]
    assert left["pid"] != right["pid"]
    assert len({o.session_id for o in result.outcomes}) == 2
    assert len({o.artifact_directory for o in result.outcomes}) == 2
    assert max(left["calls"][0]["start"], right["calls"][0]["start"]) < min(
        left["calls"][0]["end"], right["calls"][0]["end"]
    )
    for observed, system, model, provider, base in [
        (left, load_persona("senior-dev").system_prompt, "default-model", "anthropic", None),
        (right, "Custom SYSTEM", "custom-model", "openai", "https://offline.invalid/v1"),
    ]:
        factory = observed["factory_calls"][0]
        assert (factory["provider"], factory["model"], factory["base_url"]) == (
            provider,
            model,
            base,
        )
        assert factory["system"] == system
        assert all([t["name"] for t in call["tools"]] == ["read"] for call in observed["calls"])
        assert len(observed["calls"]) == 2
    assert result.outcomes[0].findings[0].persona == "senior-dev"
    assert result.outcomes[1].findings == () and result.outcomes[1].output_available
    visible = json.dumps(left["calls"] + right["calls"])
    assert all(
        sentinel not in visible
        for sentinel in (
            "EXCLUDED_SENTINEL",
            "RAW_BODY_SENTINEL",
            "DISCUSSION_SENTINEL",
            "HOSTILE_FIELD",
        )
    )
    assert all(p.read_bytes() == content for p, content in before.items())
    assert inputs.root not in result.outcomes[0].artifact_directory.parents
    manifest = load(capture / "fanout.json")
    assert sum(manifest["ledger"]["allocations"].values()) == 50
    assert manifest["ledger"]["accounting_complete"] is True


@pytest.mark.parametrize(
    "limit,replies,expected,calls,available",
    [
        (
            25,
            [tool(document=FINDING), tool(document={"findings": ["invalid"]}), text()],
            "partial",
            2,
            True,
        ),
        (25, [tool(), tool(call_id="second"), text()], "partial", 2, False),
        (25, [tool(document={"findings": []}), tool(call_id="second"), text()], "partial", 2, True),
        (1, [text()], "partial", 1, True),
        (31, [tool(), tool(call_id="second"), text()], "partial", 3, True),
        (15, [text()], "complete", 1, True),
        (
            18,
            [text(usage={"input": 2, "output": 3, "cache_read": 5, "cache_write": 8})],
            "complete",
            1,
            True,
        ),
    ],
)
def test_actual_after_turn_limits_latest_valid_output_and_disjoint_cache(
    capture, tmp_path, installed, limit, replies, expected, calls, available
):
    outcome, ledger, inputs = single(
        capture, tmp_path, installed, {"replies": replies}, limit=limit
    )
    observed = observations(outcome)
    assert len(observed["calls"]) == calls
    assert outcome.status == expected and outcome.output_available == available
    assert outcome.accounting_complete and ledger.accounting_complete
    expected_usage = TokenUsage(2, 3, 5, 8) if limit == 18 else TokenUsage(10 * calls, 5 * calls)
    assert outcome.usage == expected_usage == ledger.usage
    assert outcome.overshoot_tokens == max(0, expected_usage.total - limit)
    assert ledger.exhausted
    assert ledger.admit("senior-dev", "later") is None
    session = load(outcome.artifact_directory / "session.json")
    assert session["budget"]["tokens"] == limit
    terminal = json.loads(
        (outcome.artifact_directory / "stdout.jsonl").read_text().splitlines()[-1]
    )
    assert terminal["budget"]["used_tokens"] == expected_usage.total
    if limit == 25:
        assert outcome.exit_code == 1 and outcome.stop_reason == "budget"
    if available and limit != 25:
        assert outcome.findings[0].persona == "senior-dev"
        assert outcome.exit_code == 0 and outcome.nare_status == "done"
    elif limit == 25 and replies[0].get("content", [])[0].get("type") == "text":
        document = json.loads(replies[0]["content"][0]["text"])
        assert session["output"] == document
        assert len(outcome.findings) == len(document["findings"])
    else:
        assert outcome.findings == () and session["output"] is None
    assert inputs.root.is_dir()


def direct_cli(
    tmp_path,
    installed,
    replies,
    *,
    root,
    limit=200,
    resume=None,
    max_tokens=None,
    observation_name="offline-observations.json",
    schema_document=None,
):
    from scrutare.engine.session_output import findings_schema

    artifact = tmp_path / "direct-artifacts" if resume is None else resume.parent
    artifact.mkdir(exist_ok=True)
    schema = artifact / "schema.json"
    schema.write_text(json.dumps(findings_schema() if schema_document is None else schema_document))
    runtime = offline_runtime(tmp_path, installed, default={"replies": replies})
    spec = tmp_path / "offline-spec.json"
    document = load(spec)
    document["observation_name"] = observation_name
    spec.write_text(json.dumps(document))
    session = artifact / "session.json" if resume is None else resume
    argv = [
        str(runtime.executable),
        "run",
        "--jsonl",
        "--yes",
        "--contract",
        "1",
        "--tools",
        "read",
        "--root",
        str(root),
        "--provider",
        "openai",
        "--model",
        "offline-model",
        "--system",
        "Direct isolated proof",
        "--schema",
        str(schema),
        "--budget-tokens",
        str(limit),
        "--max-turns",
        "10",
    ]
    if max_tokens is not None:
        argv.extend(["--max-tokens", str(max_tokens)])
    argv.extend(
        ["Review copied inputs", "--session", str(session)]
        if resume is None
        else ["--resume", str(session)]
    )
    process = subprocess.run(argv, capture_output=True, env={"PATH": "/usr/bin:/bin"}, timeout=15)
    (artifact / "stdout.jsonl").write_bytes(process.stdout)
    (artifact / "stderr.txt").write_bytes(process.stderr)
    assert process.returncode in (0, 1), process.stderr.decode()
    observed = load(artifact / observation_name)
    assert observed["guard_violations"] == [] and observed["credential_names"] == []
    decoded = (
        None
        if resume is not None or schema_document is not None
        else decode_session(
            process.stdout,
            session.read_bytes(),
            persona="senior-dev",
            exit_code=process.returncode,
            expected_limit=limit,
            expected_root=root,
        )
    )
    return process, decoded, observed, session


CORRECTION_SCHEMA = {
    "type": "object",
    "properties": {"corrections": {
        "type": "array", "items": {
            "type": "object", "properties": {
                "request_id": {"type": "string"}, "file": {"type": "string"},
                "line": {"type": "integer"},
                "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
            }, "required": ["request_id", "file", "line", "side"],
            "additionalProperties": False,
        },
    }},
    "required": ["corrections"],
    "additionalProperties": False,
}


@pytest.mark.parametrize("document", [
    {"corrections": []},
    {"corrections": [{"request_id": "r0001", "file": "src/app.py", "line": 3,
                       "side": "RIGHT"}]},
])
def test_actual_correction_schema_accepts_anchor_only_documents(tmp_path, installed, document):
    root = tmp_path / "read-root"
    root.mkdir()
    process, _, observed, session = direct_cli(
        tmp_path, installed, [text(document)], root=root, schema_document=CORRECTION_SCHEMA,
    )
    terminal = json.loads(process.stdout.splitlines()[-1])
    assert process.returncode == 0 and terminal["status"] == "done"
    assert terminal["output"] == load(session)["output"] == document
    assert terminal["usage"] == load(session)["usage"]
    assert len(observed["calls"]) == 1
    assert load(session)["policy"] == {"tools": ["read"], "root": str(root)}


@pytest.mark.parametrize("mutation", [
    "boolean-line", "invalid-side", "missing-side", "extra-field", "extra-verdict",
])
def test_actual_correction_schema_rejects_invalid_model_output_then_accepts_empty(
    tmp_path, installed, mutation,
):
    item = {"request_id": "r0001", "file": "src/app.py", "line": 3, "side": "LEFT"}
    bad = {"corrections": [item]}
    if mutation == "boolean-line":
        item["line"] = True
    elif mutation == "invalid-side":
        item["side"] = "BOTH"
    elif mutation == "missing-side":
        del item["side"]
    elif mutation == "extra-field":
        item["problem"] = "Changed"
    else:
        bad["verdict"] = "approve"
    root = tmp_path / "read-root"
    root.mkdir()
    process, _, observed, session = direct_cli(
        tmp_path, installed, [text(bad), text({"corrections": []})], root=root,
        schema_document=CORRECTION_SCHEMA,
    )
    records = [json.loads(line) for line in process.stdout.splitlines()]
    assert process.returncode == 0 and records[-1]["status"] == "done"
    assert records[-1]["output"] == load(session)["output"] == {"corrections": []}
    assert len(observed["calls"]) == 2
    assert [r["detail"]["output"] for r in records if r["type"] == "output"] == [
        {"corrections": []},
    ]
    assert "did not satisfy the required JSON Schema" in json.dumps(
        observed["calls"][1]["messages"]
    )


def test_actual_read_only_tool_dispatch_refuses_fabricated_tools_and_path_escapes(
    capture, tmp_path, installed
):
    config = configure(capture, personas=["senior-dev"])
    inputs = prepare_review_inputs(capture, config)
    root = tmp_path / "tool-root"
    root.mkdir()
    source = root / "diff.patch"
    source.write_bytes((inputs.root / "diff.patch").read_bytes())
    outside = tmp_path / "outside-secret"
    outside.write_text("OUTSIDE_SENTINEL")
    (root / "escape-link").symlink_to(outside)
    before = {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()}
    replies = [
        tool("write", {"path": "diff.patch", "content": "changed"}, call_id="write"),
        tool("edit", {"path": "diff.patch", "old": "new", "new": "changed"}, call_id="edit"),
        tool("bash", {"command": "false"}, call_id="bash"),
        tool(args={"path": "../outside-secret"}, call_id="traversal"),
        tool(args={"path": str(outside)}, call_id="absolute"),
        tool(args={"path": "escape-link"}, call_id="symlink"),
        tool("ask", {"questions": ["unsafe"]}, call_id="ask"),
    ]
    process, decoded, observed, session = direct_cli(tmp_path, installed, replies, root=root)
    assert process.returncode == 0 and decoded.status == "blocked"
    assert len(observed["calls"]) == 7
    results = [
        block
        for message in load(session)["messages"]
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert len(results) == 7 and all(block["is_error"] for block in results)
    assert all("not allowed" in block["content"] for block in [*results[:3], results[-1]])
    assert all("outside the root" in block["content"] for block in results[3:6])
    assert load(session)["questions"] == ["unsafe"]
    assert "OUTSIDE_SENTINEL" not in json.dumps(observed["calls"])
    assert source.read_bytes() == (inputs.root / "diff.patch").read_bytes()
    assert all(p.read_bytes() == contents for p, contents in before.items())


def test_actual_empty_selection_reads_only_empty_prepared_artifacts(capture, tmp_path, installed):
    replies = [
        tool(args={"path": "files.json"}, call_id="files"),
        tool(args={"path": "diff.patch"}, call_id="diff"),
        tool(args={"path": "../diff.patch"}, call_id="raw"),
        text({"findings": []}),
    ]
    config = configure(capture, personas=["senior-dev"], review=100, per=100, empty=True)
    inputs = prepare_review_inputs(capture, config)
    runtime = offline_runtime(tmp_path, installed, default={"replies": replies})
    result = asyncio.run(fan_out(capture, config, runtime=runtime))
    outcome = result.outcomes[0]
    assert inputs.effective_files == () and (inputs.root / "diff.patch").read_bytes() == b""
    assert outcome.status == "complete" and outcome.output_available and outcome.findings == ()
    assert result.usage.total == 60
    observed = observations(outcome)
    result_blocks = [
        b
        for m in observed["calls"][-1]["messages"]
        for b in m["content"]
        if b["type"] == "tool_result"
    ]
    assert result_blocks[0]["content"] == "[]" and result_blocks[1]["content"] == ""
    assert result_blocks[2]["is_error"] and "outside the root" in result_blocks[2]["content"]
    assert "EXCLUDED_SENTINEL" not in json.dumps(observed["calls"])


def test_actual_resume_cumulative_delta_and_fresh_retry_remaining_bound(
    capture, tmp_path, installed
):
    config = configure(capture, personas=["senior-dev"], review=60, per=60)
    inputs = prepare_review_inputs(capture, config)
    replies = [tool(), tool(call_id="second", document=FINDING)]
    _, first, observed, session = direct_cli(
        tmp_path, installed, replies, root=inputs.root, limit=25
    )
    assert first.usage == TokenUsage(20, 10) and len(observed["calls"]) == 2
    ledger = ReviewBudgetLedger(("senior-dev",), BudgetSettings(60, 60))
    lease = ledger.admit("senior-dev", "cumulative")
    ledger.settle(lease, first.usage, True)
    resumed = ledger.admit("senior-dev", "cumulative", first.usage)
    assert resumed.allocated_tokens == 30 and resumed.limit_tokens == 60
    _, second, observed, _ = direct_cli(
        tmp_path,
        installed,
        [text()],
        root=inputs.root,
        limit=resumed.limit_tokens,
        resume=session,
        observation_name="resume-observations.json",
    )
    assert second is None and len(observed["calls"]) == 1
    persisted = load(session)
    terminal = json.loads((session.parent / "stdout.jsonl").read_text().splitlines()[-1])
    assert terminal["usage"] == persisted["usage"]
    assert terminal["status"] == persisted["status"] == "done"
    cumulative = TokenUsage(
        *(persisted["usage"][field] for field in ("input", "output", "cache_read", "cache_write"))
    )
    assert cumulative == TokenUsage(30, 15)
    ledger.observe(resumed, cumulative)
    assert ledger.usage.total == 45
    ledger.settle(resumed, cumulative, True)
    assert ledger.usage.total == 45
    retry = ledger.admit("senior-dev", "fresh-retry")
    assert retry.baseline == TokenUsage() and retry.allocated_tokens == retry.limit_tokens == 15
    descriptor = prepare_persona_inputs(inputs, config.personas)[0]
    attempt = create_attempt_directory(capture, "senior-dev", attempt=2, prepared_root=inputs.root)
    runtime = offline_runtime(tmp_path, installed, default={"replies": [text()]})

    async def execute():
        capability = await inspect_nare_runtime(runtime)
        return await run_persona_session(
            descriptor,
            config.models.default,
            retry,
            ledger=ledger,
            artifact_directory=attempt,
            runtime=runtime,
            capability=capability,
        )

    outcome = asyncio.run(execute())
    assert outcome.status == "complete" and outcome.usage.total == 15
    assert ledger.usage.total == 60 and ledger.admit("senior-dev", "later") is None
    observations(outcome)


def test_actual_response_max_tokens_is_separate_from_cumulative_budget(
    capture, tmp_path, installed
):
    config = configure(capture, personas=["senior-dev"])
    inputs = prepare_review_inputs(capture, config)
    _, decoded, observed, _ = direct_cli(
        tmp_path,
        installed,
        [tool(), tool(call_id="second"), text()],
        root=inputs.root,
        limit=31,
        max_tokens=5,
    )
    assert decoded.status == "done" and decoded.partial and decoded.usage.total == 45
    assert observed["factory_calls"][0]["max_tokens"] == 5
    assert len(observed["calls"]) == 3


def test_actual_provider_failure_preserves_sibling_outputs_and_logs(capture, tmp_path, installed):
    config = configure(capture, review=100, per=50)
    runtime = offline_runtime(
        tmp_path,
        installed,
        systems={
            load_persona("senior-dev").system_prompt: {
                "replies": [text()],
                "barrier_participants": 2,
            },
            "Custom SYSTEM": {"error": "offline provider failure", "barrier_participants": 2},
        },
    )
    result = asyncio.run(fan_out(capture, config, runtime=runtime))
    assert [o.status for o in result.outcomes] == ["complete", "failed"]
    assert result.failed and result.partial
    assert result.outcomes[0].findings and result.outcomes[0].output_available
    assert result.outcomes[1].reason == "provider" and not result.outcomes[1].accounting_complete
    assert load(capture / "fanout.json")["ledger"]["accounting_complete"] is False
    for outcome in result.outcomes:
        observations(outcome)
        assert (outcome.artifact_directory / "stdout.jsonl").is_file()
        assert (outcome.artifact_directory / "stderr.txt").is_file()
        assert (outcome.artifact_directory / "result.json").is_file()
    failed_text = (result.outcomes[1].artifact_directory / "stdout.jsonl").read_text()
    assert "offline provider failure" in failed_text


def assert_reaped(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_single_timeout_applies_to_session_after_runtime_inspection(capture, tmp_path, monkeypatch):
    inspected = []
    executed = []
    result = object()

    async def inspect(runtime):
        inspected.append(runtime.timeout_seconds)
        return "verified"

    async def run(*args, runtime, capability, **kwargs):
        executed.append((runtime.timeout_seconds, capability))
        return result

    monkeypatch.setattr(__import__(__name__), "inspect_nare_runtime", inspect)
    monkeypatch.setattr(__import__(__name__), "run_persona_session", run)
    outcome, _, _ = single(
        capture, tmp_path, (Path("/offline/nare"), "/usr/bin/python3"), {}, timeout=0.7
    )
    assert outcome is result
    assert inspected == [15]
    assert executed == [(0.7, "verified")]


def test_actual_timeout_reaps_runtime_and_marks_accounting_uncertain(capture, tmp_path, installed):
    outcome, ledger, _ = single(
        capture, tmp_path, installed, {"replies": [text()], "delay_seconds": 30}, timeout=5
    )
    assert outcome.status == "failed" and outcome.reason == "timeout"
    assert not outcome.accounting_complete and not ledger.accounting_complete
    assert ledger.admit("senior-dev", "later") is None
    marker = load(outcome.artifact_directory / "transport-ready.json")
    assert_reaped(marker["pid"])
    assert (outcome.artifact_directory / "stdout.jsonl").exists()
    assert (outcome.artifact_directory / "stderr.txt").exists()


def test_actual_caller_cancellation_reaps_every_overlapping_runtime(capture, tmp_path, installed):
    config = configure(capture)
    runtime = offline_runtime(
        tmp_path,
        installed,
        default={"replies": [text()], "barrier_participants": 2, "delay_seconds": 10},
    )

    async def scenario():
        task = asyncio.create_task(fan_out(capture, config, runtime=runtime))
        deadline = time.monotonic() + 5
        while len(list(capture.glob("sessions/*/attempt-0001/transport-ready.json"))) < 2:
            if task.done():
                await task
                pytest.fail("wave finished before both runtimes entered transport")
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        pids = [
            load(p)["pid"] for p in capture.glob("sessions/*/attempt-0001/transport-ready.json")
        ]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(set(pids)) == 2
        for pid in pids:
            assert_reaped(pid)

    asyncio.run(scenario())
    assert not (capture / "fanout.json").exists()
    results = [load(p) for p in capture.glob("sessions/*/attempt-0001/result.json")]
    assert len(results) == 2 and all(r["reason"] == "cancelled" for r in results)
    assert all(r["accounting_complete"] is False for r in results)


def test_actual_zero_quota_personas_create_records_without_processes(capture, tmp_path, installed):
    config = configure(capture, personas=["senior-dev", "security", "devops"], review=1)
    runtime = offline_runtime(tmp_path, installed, default={"replies": [text()]})
    result = asyncio.run(fan_out(capture, config, runtime=runtime))
    assert [o.status for o in result.outcomes] == ["partial", "not_started", "not_started"]
    assert result.usage.total == 15 and result.review_overshoot_tokens == 14
    assert result.partial and result.review_exhausted and not result.failed
    assert len(observations(result.outcomes[0])["calls"]) == 1
    for outcome in result.outcomes[1:]:
        assert outcome.reason == "review_budget" and outcome.invocation_limit == 0
        assert outcome.session_id is None and outcome.usage == TokenUsage()
        assert {p.name for p in outcome.artifact_directory.iterdir()} == {"result.json"}
        assert load(outcome.artifact_directory / "result.json")["status"] == "not_started"


def test_actual_latest_valid_empty_replaces_earlier_candidates(capture, tmp_path, installed):
    replies = [tool(document=FINDING), tool(document={"findings": []}, call_id="replacement")]
    outcome, _, _ = single(capture, tmp_path, installed, {"replies": replies}, limit=25)
    assert outcome.status == "partial" and outcome.output_available
    assert outcome.findings == ()
    assert load(outcome.artifact_directory / "session.json")["output"] == {"findings": []}
    assert len(observations(outcome)["calls"]) == 2
