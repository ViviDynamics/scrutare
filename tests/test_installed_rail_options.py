"""Installed nare proofs: cache breakpoints, effort and the anthropic schema constraint."""

import asyncio
import json
import subprocess

import pytest
from test_fanout import configure
from test_installed_request_retries import FINAL, READ, http_review
from test_installed_structured_output import MALFORMED
from test_nare_cli_integration import FINDING, load, offline_runtime
from test_nare_cli_integration import installed as installed
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.engine.reanchor import reanchor_schema
from scrutare.engine.session_output import findings_schema
from scrutare.engine.strategy import run_review
from scrutare.replay import replay_run

TURNS = [{"frames": READ}, {"frames": FINAL}]


def marked(value):
    """Every prompt-cache marker anywhere in one request body."""
    if isinstance(value, dict):
        own = [value["cache_control"]] if "cache_control" in value else []
        return own + [mark for item in value.values() for mark in marked(item)]
    if isinstance(value, list):
        return [mark for item in value for mark in marked(item)]
    return []


@pytest.mark.parametrize("enabled", [None, False, True])
def test_cache_breakpoints_mark_the_openai_requests_only_when_enabled(
        capture, tmp_path, installed, enabled):
    rail = {} if enabled is None else {"cache_breakpoints": enabled}
    result, outcome, observed = http_review(capture, tmp_path, installed, TURNS, **rail)
    assert result.status == "complete"
    payloads = [call["payload"] for call in observed["calls"]]
    assert len(payloads) == 2
    for body in payloads:
        # The newest block and the last tool, as on the anthropic rail; nothing without it.
        assert marked(body) == ([{"type": "ephemeral"}] * 2 if enabled else [])
    attempt = outcome.artifact_directory
    invocation, recorded = load(attempt / "invocation.json"), load(attempt / "result.json")
    assert invocation["argv"].count("--cache-breakpoints") == int(bool(enabled))
    assert invocation["rail"].get("cache_breakpoints") == enabled
    assert recorded.get("cache_breakpoints") == (None if enabled is None
                                                 else {"enabled": enabled})
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


@pytest.mark.parametrize("effort", [None, "high"])
def test_effort_reaches_every_openai_request_and_the_saved_session(
        capture, tmp_path, installed, effort):
    rail = {} if effort is None else {"effort": effort}
    result, outcome, observed = http_review(capture, tmp_path, installed, TURNS, **rail)
    assert result.status == "complete"
    payloads = [call["payload"] for call in observed["calls"]]
    assert [body.get("reasoning_effort") for body in payloads] == [effort] * 2
    attempt = outcome.artifact_directory
    assert load(attempt / "session.json")["effort"] == effort
    assert load(attempt / "result.json").get("effort") == (
        None if effort is None else {"level": effort})
    argv = load(attempt / "invocation.json")["argv"]
    assert ("--effort" in argv) is (effort is not None)
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def message(content, stop, usage):
    return {"message": {"content": content, "stop_reason": stop, "usage": {
        "input_tokens": usage[0], "output_tokens": usage[1],
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}


def anthropic_review(capture, tmp_path, installed, *, structured, effort=None):
    document = configure(capture, personas=["senior-dev"], review=200, per=200).to_dict()
    document["models"]["default"] = {
        "provider": "anthropic", "model": "offline-model",
        **({"structured_output": "json_schema"} if structured else {}),
        **({"effort": effort} if effort else {})}
    config = parse_config(json.dumps(document))
    (capture / "config.yaml").write_text(json.dumps(document))
    (capture / "config.json").write_text(json.dumps(config.to_dict()))
    turns = [
        message([{"type": "tool_use", "id": "toolu_read", "name": "read",
                  "input": {"path": "diff.patch"}}], "tool_use", (12, 3)),
        message([{"type": "text", "text": MALFORMED}], "end_turn", (20, 9)),
        message([{"type": "text", "text": json.dumps(FINDING)}], "end_turn", (30, 9)),
    ]
    runtime = offline_runtime(tmp_path, installed, default={"http_anthropic": {"turns": turns}})
    specification = tmp_path / "offline-spec.json"
    spec = load(specification)
    # A public dummy credential lets the actual nare factory initialize. No live key is read.
    spec["environment"] = {"ANTHROPIC_API_KEY": "offline-test-key"}
    specification.write_text(json.dumps(spec))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    outcome = result.initial.outcomes[0]
    observed = load(outcome.artifact_directory / "offline-observations.json")
    assert observed["guard_violations"] == [] and observed["nare_controls"] == {}
    assert observed["credential_names"] == ["ANTHROPIC_API_KEY"]
    assert observed["version"] == "2026.11.94"
    return result, outcome, [call["payload"] for call in observed["calls"]]


@pytest.mark.parametrize("structured", [True, False])
def test_the_real_anthropic_rail_constrains_only_the_correction(
        capture, tmp_path, installed, structured):
    result, outcome, payloads = anthropic_review(capture, tmp_path, installed,
                                                 structured=structured)
    assert result.status == "complete", outcome.reason
    assert result.verdict.verdict == "changes_requested"
    assert len(payloads) == 3
    formats = [body.get("output_config", {}).get("format") for body in payloads]
    attempt = outcome.artifact_directory
    if structured:
        # Anthropic's structured outputs, on the schema correction turn alone and tool-free.
        assert formats[:2] == [None, None] and formats[2]["type"] == "json_schema"
        # nare withholds the tools by sending an empty list: the earlier read call and its
        # result are still in the transcript of that request.
        assert [bool(body.get("tools")) for body in payloads] == [True, True, False]
        assert payloads[2]["tools"] == [] and "tool_choice" not in payloads[2]
        assert any(block.get("type") == "tool_result" for item in payloads[2]["messages"]
                   if isinstance(item["content"], list) for block in item["content"])
        sent = formats[2]["schema"]
        assert sent["required"] == ["findings"] and sent["additionalProperties"] is False
        assert set(sent["properties"]["findings"]["items"]["properties"]) == set(
            findings_schema()["properties"]["findings"]["items"]["properties"])
    else:
        assert formats == [None, None, None]
        assert [bool(body.get("tools")) for body in payloads] == [True, True, True]
    assert outcome.usage.to_dict()["total"] == 83 and outcome.accounting_complete
    invocation, recorded = load(attempt / "invocation.json"), load(attempt / "result.json")
    assert ("--structured-output" in invocation["argv"]) is structured
    assert "--stream" not in invocation["argv"]
    assert recorded.get("structured_output") == (
        {"mode": "json_schema", "constrained_turns": 1} if structured else None)
    assert load(capture / "fanout.json")["personas"][0]["rail"].get(
        "structured_output") == ("json_schema" if structured else None)
    replayed = replay_run(capture)
    assert replayed.saved_identical and replayed.issues == () and replayed.exit_code == 0


def test_effort_and_the_constraint_share_the_anthropic_output_config(
        capture, tmp_path, installed):
    result, outcome, payloads = anthropic_review(capture, tmp_path, installed,
                                                 structured=True, effort="high")
    assert result.status == "complete", outcome.reason
    assert [body["output_config"].get("effort") for body in payloads] == ["high"] * 3
    assert [body["thinking"] for body in payloads] == [{"type": "adaptive"}] * 3
    assert [("format" in body["output_config"]) for body in payloads] == [False, False, True]
    assert load(outcome.artifact_directory / "session.json")["effort"] == "high"
    assert replay_run(capture).issues == ()


def test_every_scrutare_schema_is_one_the_anthropic_rail_can_constrain(installed):
    from scrutare.engine.assessment_inputs import AssessmentInput
    from scrutare.engine.discussion_inputs import DiscussionInput
    from scrutare.findings.citation_correction import CitationDefect, citation_correction_schema

    _, interpreter = installed
    schemas = {
        "findings": findings_schema(), "findings-v2": findings_schema(evidence_version=2),
        "reanchor": reanchor_schema(), "assessment": AssessmentInput.output_schema(),
        "discussion": DiscussionInput.output_schema(),
    }
    try:
        defect = CitationDefect("c0001", 1, 1, "head", "src/app.py",
                                "line_range_outside_capture", end_line=4, last_line=3)
        schemas["citation-correction"] = citation_correction_schema((defect,))
    except TypeError:
        pytest.fail("citation defect fixture no longer matches CitationDefect")
    checked = subprocess.run(
        [str(interpreter), "-B", "-c",
         "import json, sys\n"
         "from nare.transport.anthropic import check_response_schema\n"
         "for name, schema in json.load(sys.stdin).items():\n"
         "    check_response_schema(schema)\n"
         "    print(name)\n"],
        input=json.dumps(schemas), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin"})
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.split() == list(schemas)
