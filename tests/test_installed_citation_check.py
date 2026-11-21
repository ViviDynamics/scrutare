"""Installed wheel and native nare 2026.11.107: the citation check inside the session (#130)."""

import json

import pytest
from test_installed_citation_correction import first_answer, load, review
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import text, tool

from scrutare.engine.repository_context import _artifact

SESSION = ", citation_check: session"
NEW_FLAGS = ("--check-command", "--check-timeout", "--require-cited-reads", "--cited-read-map")


def answered(**changes):
    return lambda provider: text(first_answer(provider, **changes))


def observed(attempt):
    found = load(attempt / "offline-observations.json")
    assert found["guard_violations"] == [] and found["credential_names"] == []
    assert found["version"] == "2026.11.107"
    return found


def corrections_told(call):
    return " ".join(block.get("text", "") for block in call["messages"][-1]["content"])


def replayed_clean(replay):
    assert replay.returncode == 0, replay.stderr.decode()
    replayed = json.loads(replay.stdout)
    assert replayed["saved_verdict"]["byte_identical"] is True
    assert replayed["issues"] == []


@pytest.mark.parametrize("structured", [False, True], ids=["plain", "structured-output"])
def test_installed_a_past_end_citation_is_corrected_in_the_same_session(
        tmp_path, installed, wheel_cli, structured):
    result, run, attempt, replay, provider = review(
        tmp_path, installed, wheel_cli, enabled=False, findings=SESSION, structured=structured,
        answer=lambda p: first_answer(p, end_line=2), later=[answered(end_line=1)])
    envelope = json.loads(result.stdout)
    assert (envelope["verdict"], envelope["panel_status"]) == ("changes_requested", "complete")
    [cited] = load(run / "verdict.json")["findings"][0]["sources"][0]["evidence"]["citations"]
    assert (cited["end_line"], cited["validation"]) == (1, "valid")
    # One nare process: no resumed transcript and no correction turn's artifacts.
    assert not (attempt / "offline-observations.resume.json").exists()
    assert not list(attempt.glob("*correction*"))
    found = observed(attempt)
    assert found["check_commands"] == 2  # the refused answer and the corrected one
    calls = found["calls"]
    assert len(calls) == 4
    # The correction is the next turn of the same session, with the read tool offered
    # (nare 2026.11.107 restores approved tools after a domain correction).
    assert [tool["name"] for tool in calls[-1]["tools"]] == ["read"]
    told = corrections_told(calls[-1])
    assert ("findings[0].evidence.citations[0].end_line: line_range_outside_capture: the "
            "cited file's last line is 1, got end_line 2") in told
    assert f"Read {_artifact('head', 'src/caller.py')} again" in told
    invocation = load(attempt / "invocation.json")
    record, argv = invocation["citation_check"], invocation["argv"]
    assert json.loads(argv[argv.index("--check-command") + 1]) == record["argv"]
    assert record["argv"][7] == argv[argv.index("--root") + 1]
    assert argv[argv.index("--max-corrections") + 1] == "2" == str(record["max_corrections"])
    assert argv[argv.index("--check-timeout") + 1] == "30"
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("complete", "done")
    assert outcome["citation_check"] == {
        "outcome": "corrected", "refusal": None, "corrections": [
            {"kind": "domain", "charged": True, "codes": ["line_range_outside_capture"]}]}
    saved = load(attempt / "session.json")
    assert saved["schema_correction_log"] == [{"kind": "domain", "charged": True, "codes": [
        "findings[0].evidence.citations[0].end_line: line_range_outside_capture: the cited "
        "file's last line is 1"]}]
    assert set(saved["policy"]) == {"tools", "root"}  # nare saves no check: never resumed
    replayed_clean(replay)


def test_installed_an_unread_citation_spends_the_budget_as_a_protocol_failure(
        tmp_path, installed, wheel_cli):
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli, enabled=False, succeeds=False, read_caller=False,
        findings=SESSION + ", max_corrections: 1, cited_reads: true",
        answer=lambda p: first_answer(p), later=[answered()])
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("failed", "protocol")
    assert outcome["citation_check"] == {"outcome": "reads_required", "refusal": None,
                                         "corrections": [{"kind": "reads", "charged": True}]}
    from scrutare.replay.audit import _citation_check_issues
    assert _citation_check_issues(run, load(run / "config.json")) == ()


def test_installed_a_placeholder_hash_is_corrected_in_the_same_session(
        tmp_path, installed, wheel_cli):
    result, run, attempt, replay, provider = review(
        tmp_path, installed, wheel_cli, enabled=False, findings=SESSION,
        answer=lambda p: first_answer(p, sha256="334e0..placeholder"), later=[answered()])
    assert json.loads(result.stdout)["verdict"] == "changes_requested"
    told = corrections_told(observed(attempt)["calls"][-1])
    assert "finding_refused: citation: expected complete revision and content hash" in told
    assert "334e0..placeholder" not in told
    assert load(attempt / "result.json")["citation_check"]["corrections"] == [
        {"kind": "domain", "charged": True, "codes": ["finding_refused"]}]
    replayed_clean(replay)


def test_installed_a_spent_budget_fails_as_the_post_session_validator(
        tmp_path, installed, wheel_cli):
    _, run, attempt, replay, _ = review(
        tmp_path, installed, wheel_cli, enabled=False, succeeds=False,
        findings=SESSION + ", max_corrections: 1",
        answer=lambda p: first_answer(p, end_line=2), later=[answered(end_line=3)])
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("failed", "citation_validation")
    assert load(attempt / "citation-validation.json")["reason"] == "line_range_outside_capture"
    assert outcome["citation_check"] == {
        "outcome": "refused", "refusal": ["line_range_outside_capture"], "corrections": [
            {"kind": "domain", "charged": True, "codes": ["line_range_outside_capture"]}]}
    terminal = json.loads((attempt / "stdout.jsonl").read_text().splitlines()[-1])
    assert (terminal["status"], terminal["stop_reason"]) == ("error", "schema_violation")
    assert observed(attempt)["check_commands"] == 2
    assert not (run / "verdict.json").exists()
    from scrutare.replay.audit import _citation_check_issues, _native_recovery_issues
    config = load(run / "config.json")
    assert _citation_check_issues(run, config) == () == _native_recovery_issues(run)


def test_installed_the_default_sends_no_check_and_fails_as_before(tmp_path, installed, wheel_cli):
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli, enabled=False, succeeds=False,
        answer=lambda p: first_answer(p, end_line=2), later=[answered(end_line=1)])
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("failed", "citation_validation")
    argv = load(attempt / "invocation.json")["argv"]
    assert not set(NEW_FLAGS) & set(argv)
    assert argv[argv.index("--max-corrections") + 1] == "1"
    assert "citation_check" not in outcome and "citation_check" not in load(
        attempt / "invocation.json")
    assert "citation_check" not in load(run / "config.json")["findings"]
    found = observed(attempt)
    assert found["check_commands"] == 0 and len(found["calls"]) == 3


def test_installed_cited_reads_hold_an_answer_until_the_cited_file_is_read(
        tmp_path, installed, wheel_cli):
    artifact = _artifact("head", "src/caller.py")
    result, run, attempt, replay, _ = review(
        tmp_path, installed, wheel_cli, enabled=False, findings=SESSION + ", cited_reads: true",
        read_caller=False, answer=lambda p: first_answer(p),
        later=[lambda p: tool(args={"path": artifact}, call_id="caller"), answered()])
    assert json.loads(result.stdout)["verdict"] == "changes_requested"
    found = observed(attempt)
    assert found["check_commands"] == 2
    calls = found["calls"]
    assert len(calls) == 4
    # nare named the capture to read, through the map, and kept the tool offered.
    told = corrections_told(calls[2])
    assert "read one of " in told and artifact in told
    assert told.endswith("which the answer cites as src/caller.py.")
    assert [tool["name"] for tool in calls[2]["tools"]] == ["read"]
    mapped = load(attempt / "cited-read-map.json")
    assert artifact in mapped["src/caller.py"]
    assert load(attempt / "result.json")["citation_check"] == {
        "outcome": "passed", "refusal": None, "corrections": [
            {"kind": "reads", "charged": True}]}
    replayed_clean(replay)


def test_installed_runtime_advertises_the_check_command_and_the_map(tmp_path, installed):
    import asyncio

    from test_nare_cli_integration import offline_runtime

    from scrutare.config import FindingSettings
    from scrutare.engine.nare_session import inspect_nare_runtime, require_findings_options

    capability = asyncio.run(inspect_nare_runtime(offline_runtime(tmp_path, installed)))
    assert capability.version == "2026.11.107"
    assert capability.check_command is True and capability.cited_read_map is True
    require_findings_options(
        FindingSettings("v2", citation_check="session", cited_reads=True), capability)
