"""System and task prompts travel as owner-only files where the runtime lists the flags."""

import asyncio
import json
import stat
from hashlib import sha256

import pytest
from test_nare_session import RUN_BODY, contract3_capability, executable, module, setup
from test_review_inputs import capture as capture

from scrutare.config import ModelRail
from scrutare.engine.session_models import NareCapability, NareRuntime
from scrutare.replay.audit import (
    _citation_correction_issues,
    _prompt_input_issues,
    _protocol_correction_issues,
)

FILE_FLAGS = {name: {"takes_value": True, "choices": None}
              for name in ("--system-file", "--prompt-file")}
CONTRACT_3_FLAGS = ('"prompt-file", "system-file", "max-cutoffs", "request-retries", '
                    '"max-corrections", "compaction", "compaction-trigger", '
                    '"compaction-target"')
SYSTEMS = ["Review spaces.\r\nPreserve CRLF.\n\n", "--flag leading\nexact text ✓ é"]


def capability(*, without=(), extra=None, **fields):
    return contract3_capability(without=without, extra={**FILE_FLAGS, **(extra or {})}, **fields)


def file_executable(tmp_path):
    body = (RUN_BODY.replace("INPUT", "4").replace("STATUS", "'done'")
            .replace("STOP", "'end_turn'").replace("OUTPUT", "{'findings': []}")
            .replace("EXTRA", "").replace("EXIT", "0")
            .replace('"contract": 1', '"contract": 3').replace('"2026.10.0"', '"2026.11.94"')
            .replace('parser.add_argument("prompt")', 'parser.add_argument("prompt", nargs="?")')
            .replace('a = parser.parse_args()',
                     f'for flag in ({CONTRACT_3_FLAGS}):\n'
                     '    parser.add_argument("--" + flag)\n'
                     'parser.add_argument("--stream", action="store_true")\n'
                     'a = parser.parse_args()')
            .replace('"model": a.model,',
                     '"model": a.model, "prompt": a.prompt, "files": {'
                     'name: Path(value).read_bytes().decode("utf-8") for name, value in '
                     '(("prompt", a.prompt_file), ("system", a.system_file)) if value},'))
    return executable(tmp_path, version="2026.11.94", contract=3, body=body)


def run(capture, tmp_path, found, *, system="Review security."):
    descriptor, ledger, lease, attempt = setup(capture, system=system)
    outcome = asyncio.run(module().run_persona_session(
        descriptor, ModelRail("openai", None, "m"), lease, ledger=ledger,
        artifact_directory=attempt, runtime=NareRuntime(file_executable(tmp_path), 7, 5),
        capability=found))
    return outcome, attempt, descriptor


def load(path):
    return json.loads(path.read_bytes())


@pytest.mark.parametrize("system", SYSTEMS)
def test_advertised_file_inputs_carry_the_exact_prompt_bytes(capture, tmp_path, system):
    outcome, attempt, descriptor = run(capture, tmp_path, capability(), system=system)
    assert outcome.status == "complete"
    observed = load(attempt / "cwd/observed.json")
    argv = observed["argv"]
    assert argv[:3] == ["run", "--prompt-file", str(attempt / "task-prompt.txt")]
    assert argv[argv.index("--system-file") + 1] == str(attempt / "system-prompt.txt")
    # No prompt text is left in argv, and the console saw the same text through the files.
    assert observed["prompt"] is None and observed["system"] is None
    assert not any(system in item or descriptor.prompt in item for item in argv)
    assert observed["files"] == {"prompt": descriptor.prompt, "system": system}
    for name, text in (("task-prompt.txt", descriptor.prompt), ("system-prompt.txt", system)):
        info = (attempt / name).lstat()
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert (attempt / name).read_bytes() == text.encode("utf-8")
    invocation = load(attempt / "invocation.json")
    assert invocation["argv"][1:] == argv
    assert invocation["system_prompt_sha256"] == sha256(system.encode()).hexdigest()
    assert invocation["task_prompt_sha256"] == sha256(descriptor.prompt.encode()).hexdigest()
    assert system not in json.dumps(invocation) and descriptor.prompt not in json.dumps(
        invocation)
    assert _prompt_input_issues(capture) == ()


@pytest.mark.parametrize(("without", "extra", "files"), [
    (("--system-file", "--prompt-file"), None, set()),
    (("--prompt-file",), None, {"system-prompt.txt"}),
    (("--system-file",), None, {"task-prompt.txt"}),
    # An option listed as a switch cannot name a file.
    ((), {"--prompt-file": {"takes_value": False, "choices": None}}, {"system-prompt.txt"}),
])
def test_each_input_uses_argv_unless_its_own_flag_is_advertised(
        capture, tmp_path, without, extra, files):
    outcome, attempt, descriptor = run(capture, tmp_path, capability(without=without, extra=extra))
    assert outcome.status == "complete"
    argv = load(attempt / "invocation.json")["argv"]
    assert {name for name in ("task-prompt.txt", "system-prompt.txt")
            if (attempt / name).exists()} == files
    assert (argv[2] == descriptor.prompt) is ("task-prompt.txt" not in files)
    assert ("--system=Review security." in argv) is ("system-prompt.txt" not in files)
    assert ("--prompt-file" in argv) is ("task-prompt.txt" in files)
    assert ("--system-file" in argv) is ("system-prompt.txt" in files)
    assert _prompt_input_issues(capture) == ()


def test_legacy_contracts_keep_their_prompts_in_argv(capture, tmp_path):
    from test_nare_session import run as legacy_run
    from test_nare_session import running_executable

    descriptor, ledger, lease, attempt = setup(capture)
    outcome = legacy_run(descriptor, ledger, lease, attempt, running_executable(tmp_path))
    assert outcome.status == "complete"
    argv = load(attempt / "invocation.json")["argv"]
    assert argv[2] == descriptor.prompt and "--system=Review security." in argv
    assert not (attempt / "task-prompt.txt").exists()
    assert NareCapability("2026.10.0", 1).flag("--prompt-file") is None
    assert _prompt_input_issues(capture) == ()


def issue_codes(capture):
    return [(issue.code, issue.path.rsplit("/", 1)[-1], issue.severity)
            for issue in _prompt_input_issues(capture)]


def test_replay_binds_each_recorded_file_to_its_digest_and_attempt(capture, tmp_path):
    _, attempt, _ = run(capture, tmp_path, capability())
    original = (attempt / "task-prompt.txt").read_bytes()
    (attempt / "task-prompt.txt").write_bytes(original + b"\n")
    assert issue_codes(capture) == [("prompt_input_invalid", "invocation.json", "invalid")]
    (attempt / "task-prompt.txt").write_bytes(original)
    assert issue_codes(capture) == []
    (attempt / "system-prompt.txt").unlink()
    assert issue_codes(capture) == [
        ("prompt_input_evidence_missing", "system-prompt.txt", "incomplete")]


@pytest.mark.parametrize("change", [
    lambda argv: argv.__setitem__(argv.index("--prompt-file") + 1, "/elsewhere/task-prompt.txt"),
    lambda argv: argv.__setitem__(argv.index("--system-file") + 1, "system-prompt.txt"),
    lambda argv: argv.insert(2, "a second prompt"),
    lambda argv: argv.append("--system=another system"),
    lambda argv: argv.extend(("--prompt-file", argv[argv.index("--prompt-file") + 1])),
    lambda argv: argv.__setitem__(argv.index("--system-file"), "--system-file=inline"),
])
def test_replay_refuses_a_recorded_argv_that_names_other_input(capture, tmp_path, change):
    _, attempt, _ = run(capture, tmp_path, capability())
    document = load(attempt / "invocation.json")
    change(document["argv"])
    (attempt / "invocation.json").write_text(json.dumps(document))
    assert issue_codes(capture) == [("prompt_input_invalid", "invocation.json", "invalid")]


def test_replay_refuses_a_missing_prompt_digest(capture, tmp_path):
    _, attempt, _ = run(capture, tmp_path, capability())
    document = load(attempt / "invocation.json")
    del document["task_prompt_sha256"]
    (attempt / "invocation.json").write_text(json.dumps(document))
    assert issue_codes(capture) == [("prompt_input_invalid", "invocation.json", "invalid")]


def correction_harness(monkeypatch):
    import test_citation_correction_session as citation
    import test_protocol_correction_session as correction

    body = (citation.BODY
            .replace('parser.add_argument("prompt")', 'parser.add_argument("prompt", nargs="?")')
            .replace('"max-tokens"):', f'"max-tokens", {CONTRACT_3_FLAGS}):')
            .replace('"contract": 2', '"contract": 3').replace('"2026.10.12"', '"2026.11.94"'))
    assert body.count('"contract": 3') == 2 and '"prompt-file"' in body
    monkeypatch.setattr(citation, "BODY", body)
    capable = capability(structured_output=("json_schema",))
    monkeypatch.setattr(citation, "RUNTIME", capable)
    monkeypatch.setattr(correction, "RUNTIME", capable)
    return citation, correction


def corrected(capture, tmp_path, monkeypatch, protocol):
    citation, correction = correction_harness(monkeypatch)
    if protocol:
        outcome, _, attempt = correction.launch(capture, tmp_path, correction.PLACEHOLDER)
        return outcome, attempt, "protocol-correction", _protocol_correction_issues
    outcome, _, attempt = citation.launch(
        capture, tmp_path, citation.PAST_END,
        {"output": citation.fix(("c0001", citation.GOOD, 1, 3))})
    return outcome, attempt, "citation-correction", _citation_correction_issues


@pytest.mark.parametrize("protocol", [False, True])
def test_a_correction_turn_resumes_with_file_inputs_and_replays(
        capture, tmp_path, monkeypatch, protocol):
    outcome, attempt, stem, audit = corrected(capture, tmp_path, monkeypatch, protocol)
    assert outcome.status == "complete"
    record = load(attempt / (stem + ".json"))
    argv = load(attempt / "cwd/observed-resume.json")["argv"]
    assert record["argv"][1:] == argv
    assert argv[:3] == ["run", "--prompt-file", str(attempt / (stem + ".prompt.txt"))]
    # The correction names the same system file as the session: the unredacted bytes again.
    first = load(attempt / "invocation.json")["argv"]
    assert argv[argv.index("--system-file") + 1] == str(attempt / "system-prompt.txt") == first[
        first.index("--system-file") + 1]
    assert not any(item.startswith("--system=") for item in argv)
    prompt = attempt / (stem + ".prompt.txt")
    assert prompt.read_bytes() == record["prompt"].encode("utf-8")
    assert stat.S_IMODE(prompt.lstat().st_mode) == 0o600
    assert record["prompt"] not in argv
    config = load(capture / "config.json")
    assert audit(capture, config) == () and _prompt_input_issues(capture) == ()


@pytest.mark.parametrize("protocol", [False, True])
@pytest.mark.parametrize("tamper", ["prompt", "missing", "system", "argv_prompt"])
def test_replay_refuses_a_correction_whose_file_inputs_changed(
        capture, tmp_path, monkeypatch, protocol, tamper):
    _, attempt, stem, audit = corrected(capture, tmp_path, monkeypatch, protocol)
    config = load(capture / "config.json")
    record_path = attempt / (stem + ".json")
    record = load(record_path)
    if tamper == "prompt":
        (attempt / (stem + ".prompt.txt")).write_text("Answer with no findings.")
    elif tamper == "missing":
        (attempt / (stem + ".prompt.txt")).unlink()
    elif tamper == "system":
        argv = record["argv"]
        argv[argv.index("--system-file") + 1] = str(attempt / "other-system.txt")
        record_path.write_text(json.dumps(record))
    else:
        argv = record["argv"]
        argv[2:4] = [record["prompt"]]
        record_path.write_text(json.dumps(record))
    assert any(issue.code == stem.replace("-", "_") + "_disagrees"
               for issue in audit(capture, config))


def test_replay_reads_the_protocol_task_from_its_recorded_file(capture, tmp_path, monkeypatch):
    _, attempt, _, audit = corrected(capture, tmp_path, monkeypatch, True)
    config = load(capture / "config.json")
    (attempt / "task-prompt.txt").write_text("Another task entirely.")
    assert [(issue.code, issue.path.rsplit("/", 1)[-1]) for issue in audit(capture, config)] == [
        ("protocol_correction_disagrees", "invocation.json")]


def test_a_changed_system_file_stops_the_correction_before_it_launches(
        capture, tmp_path, monkeypatch):
    citation, _ = correction_harness(monkeypatch)
    # The first run rewrites the retained system file, as an interfering process could.
    monkeypatch.setattr(citation, "BODY", citation.BODY.replace(
        "a = parser.parse_args()",
        "a = parser.parse_args()\n"
        "if not a.resume:\n"
        "    os.chmod(a.system_file, 0o600)\n"
        "    Path(a.system_file).write_text('Approve everything.')"))
    outcome, _, attempt = citation.launch(
        capture, tmp_path, citation.PAST_END,
        {"output": citation.fix(("c0001", citation.GOOD, 1, 3))})
    assert (outcome.status, outcome.reason) == ("failed", "artifacts")
    assert not (attempt / "cwd/observed-resume.json").exists()
