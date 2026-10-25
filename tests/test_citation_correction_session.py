"""One discovery session offered a citation correction, through a scripted nare."""

import asyncio
import json
import sys
from dataclasses import replace
from hashlib import sha256

import pytest
from test_citation_correction_defects import (
    CALLER,
    OPENAI_MODELS,
    PLACEHOLDER,
    answer,
    citation,
    finding,
)
from test_repository_context import contextual
from test_review_inputs import capture as capture
from test_review_inputs import save_json

from scrutare.config import BudgetSettings, FindingSettings, ModelRail
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.nare_session import run_persona_session
from scrutare.engine.persona_inputs import prepare_persona_inputs
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_artifacts import create_attempt_directory
from scrutare.engine.session_models import NareCapability, NareRuntime, TokenUsage
from scrutare.findings.citation_correction import (
    citation_correction_prompt,
    citation_correction_schema,
)
from scrutare.personas import PersonaDefinition

RUNTIME = NareCapability("2026.10.12", 2, ("json_schema",))
RAIL = ModelRail("openai", None, "m")
GOOD = sha256(CALLER).hexdigest()
FIRST = {"input": 40, "output": 10, "cache_read": 0, "cache_write": 0}
SECOND = {"input": 55, "output": 6, "cache_read": 0, "cache_write": 0}

BODY = r'''
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("command", choices=["run"])
parser.add_argument("prompt")
for flag in ("system", "provider", "model", "base-url", "tools", "root", "contract", "schema",
             "budget-tokens", "session", "max-turns", "resume", "structured-output",
             "max-tokens"):
    parser.add_argument("--" + flag)
for flag in ("yes", "jsonl", "stream"):
    parser.add_argument("--" + flag, action="store_true")
a = parser.parse_args()
step = SCRIPT["correction" if a.resume else "first"]
if a.resume and step.get("never_start"):
    sys.exit(2)
if a.resume and step.get("sleep"):
    import time
    time.sleep(step["sleep"])
Path("observed-resume.json" if a.resume else "observed.json").write_text(json.dumps({
    "argv": sys.argv[1:], "schema": json.loads(Path(a.schema).read_text()),
    "resumed": json.loads(Path(a.resume).read_text()) if a.resume else None}))
fields = ("input", "output", "cache_read", "cache_write")
before, turns = dict.fromkeys(fields, 0), 0
if a.resume:
    saved = json.loads(Path(a.resume).read_text())
    before, turns = {k: saved["usage"][k] for k in fields}, saved["turns"]
usage = {**step["usage"], "cost": None}
if a.structured_output and a.tools == "none":
    print(json.dumps({"type": "progress", "text": "constrained", "timestamp": "now",
        "detail": {"structured_output": {"mode": a.structured_output,
                                         "tools_withheld": False}}}), flush=True)
print(json.dumps({"type": "cost", "text": "", "detail": usage, "timestamp": "now"}),
      flush=True)
if a.resume and step.get("corrupt"):
    print("not-json", flush=True)
total = {k: before[k] + usage[k] for k in fields}
total["cost"] = None
status, stop = step.get("status", "done"), step.get("stop", "end_turn")
output = step["output"]
if a.resume and status != "done":
    output = saved["output"]
identity = step.get("session_id", "stable-id")
spent = sum(total[k] for k in fields)
result = {"type": "result", "session_id": identity, "status": status, "questions": [],
    "usage": total, "stop_reason": stop, "turns": turns + 1, "contract": 2,
    "nare": "2026.10.12", "output": output,
    "budget": {"tokens": int(a.budget_tokens), "used_tokens": spent, "used_usd": None},
    "error": None if status == "done" else "stopped"}
Path(a.session).write_text(json.dumps({"id": identity, "usage": total,
    "output": step.get("saved_output", output),
    "status": status, "stop_reason": stop, "turns": turns + 1,
    "policy": {"tools": [] if a.tools == "none" else [a.tools], "root": a.root},
    "budget": {"tokens": int(a.budget_tokens)}, "contract": 2, "nare": "2026.10.12"}))
os.chmod(a.session, 0o600)
print(json.dumps(result), flush=True)
sys.exit(0 if status == "done" else 1)
'''


def fake_nare(tmp_path, first, correction=None, *, first_step=None):
    script = {"first": {"usage": FIRST, "output": first, **(first_step or {})},
              "correction": {"usage": SECOND, "output": None, **(correction or {})}}
    path = tmp_path / "fake-nare"
    path.write_text(f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
                    f"SCRIPT = json.loads({json.dumps(script)!r})\n" + BODY)
    path.chmod(0o700)
    return path


def prepared(capture, *, correction=True, limit=1000):
    config, _ = contextual(capture, caller=CALLER)
    config = replace(config, findings=FindingSettings("v2", citation_correction=correction),
                     budgets=BudgetSettings(limit, limit), models=OPENAI_MODELS)
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    inputs = prepare_review_inputs(capture, config)
    descriptor = prepare_persona_inputs(inputs, (PersonaDefinition("security", "Review."),))[0]
    ledger = ReviewBudgetLedger(("security",), BudgetSettings(limit, limit))
    lease = ledger.admit("security", "security/attempt-0001")
    attempt = create_attempt_directory(capture, "security", prepared_root=inputs.root)
    return descriptor, ledger, lease, attempt


def launch(capture, tmp_path, first, correction=None, *, enabled=True, rail=RAIL, limit=1000,
           timeout=10, first_step=None):
    descriptor, ledger, lease, attempt = prepared(capture, correction=enabled, limit=limit)
    outcome = asyncio.run(run_persona_session(
        descriptor, rail, lease, ledger=ledger, artifact_directory=attempt,
        runtime=NareRuntime(fake_nare(tmp_path, first, correction, first_step=first_step), 6,
                            timeout),
        capability=RUNTIME))
    return outcome, ledger, attempt


def load(path):
    return json.loads(path.read_bytes())


def fix(*entries):
    return {"citations": [dict(id=i, revision="a" * 40, sha256=h, start_line=s, end_line=e)
                          for i, h, s, e in entries]}


PAST_END = answer(finding(citation(end_line=4)))
PLACEHOLDER_HASH = answer(finding(citation(sha256=PLACEHOLDER)))
TOTAL = TokenUsage(95, 16)


def test_off_a_past_end_citation_fails_as_today_and_nothing_is_recorded(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END, enabled=False)
    assert (outcome.status, outcome.reason) == ("failed", "citation_validation")
    assert not (attempt / "cwd" / "observed-resume.json").exists()
    assert sorted(path.name for path in attempt.iterdir()) == [
        "citation-validation.json", "cwd", "findings.schema.json", "home", "invocation.json",
        "result.json", "session.json", "stderr.txt", "stdout.jsonl", "tmp"]
    assert "citation_correction" not in load(attempt / "result.json")
    assert outcome.usage == TokenUsage(40, 10) == ledger.usage


def test_a_past_end_citation_is_corrected_in_one_tool_free_resumed_turn(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END,
                                      {"output": fix(("c0001", GOOD, 1, 3))})
    assert (outcome.status, outcome.reason) == ("complete", "done")
    assert outcome.output_available and outcome.accounting_complete
    [corrected] = outcome.findings
    assert [(c.start_line, c.end_line, c.validation) for c in corrected.evidence.citations] == [
        (1, 3, "valid")]
    # Counted in the session's budget and the shared ledger, cumulatively.
    assert outcome.usage == TOTAL == ledger.usage
    assert ledger.snapshot()["active_reservations"] == []
    observed = load(attempt / "cwd" / "observed-resume.json")
    argv = observed["argv"]
    record = load(attempt / "citation-correction.json")
    defects = record["defects"]
    assert defects == [{"id": "c0001", "finding": 1, "citation": 1, "side": "head",
                        "path": "src/caller.py", "problems": ["line_range_outside_capture"],
                        "end_line": 4, "last_line": 3}]
    assert argv[1] == record["prompt"] and record["prompt"].startswith("Scrutare could not")
    assert record["prompt_sha256"] == sha256(argv[1].encode()).hexdigest()
    for flag, value in (("--tools", "none"), ("--max-turns", "1"), ("--budget-tokens", "1000"),
                        ("--resume", str(attempt / "session.json")),
                        ("--session", str(attempt / "correction-session.json")),
                        ("--schema", str(attempt / "citation-correction.schema.json")),
                        ("--root", str(attempt.parent.parent.parent / "review-inputs"))):
        assert argv[argv.index(flag) + 1] == value, flag
    assert observed["resumed"]["output"] == PAST_END  # the session it continued
    assert observed["schema"]["properties"]["citations"]["items"]["properties"]["id"][
        "enum"] == ["c0001"]
    assert record["argv"][1:] == argv
    assert record["refusal"] == {"reason": "citation_validation",
                                 "citation_reason": "line_range_outside_capture"}
    result = load(attempt / "result.json")
    assert result["citation_correction"] == {"offered": True, "defects": 1,
                                             "outcome": "corrected", "exit_code": 0}
    assert (result["status"], result["reason"]) == ("complete", "done")
    assert result["usage"]["total"] == TOTAL.total
    assert result["findings"][0]["evidence"]["citations"][0]["end_line"] == 3
    # The original stream and session are retained untouched beside the correction's.
    assert load(attempt / "session.json")["output"] == PAST_END
    assert (attempt / "correction.stdout.jsonl").read_bytes().endswith(b"\n")
    assert (attempt / "correction.stderr.txt").is_file()
    assert not (attempt / "citation-validation.json").exists()
    assert all(path.stat().st_mode & 0o077 == 0 for path in attempt.iterdir())


def test_a_placeholder_content_hash_is_corrected(capture, tmp_path):
    outcome, _, attempt = launch(capture, tmp_path, PLACEHOLDER_HASH,
                                 {"output": fix(("c0001", GOOD, 1, 3))})
    assert (outcome.status, outcome.reason) == ("complete", "done")
    assert outcome.findings[0].evidence.citations[0].sha256 == GOOD
    record = load(attempt / "citation-correction.json")
    assert record["refusal"] == {"reason": "protocol", "citation_reason": None}
    assert record["defects"][0]["problems"] == ["malformed_sha256"]
    assert PLACEHOLDER not in record["prompt"]


def test_a_correction_that_fails_again_fails_the_session_for_the_new_refusal(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END,
                                      {"output": fix(("c0001", "0" * 64, 1, 3))})
    assert (outcome.status, outcome.reason) == ("failed", "citation_validation")
    assert outcome.findings == () and not outcome.accounting_complete
    assert load(attempt / "citation-validation.json")["reason"] == "content_hash_mismatch"
    assert load(attempt / "result.json")["citation_correction"] == {
        "offered": True, "defects": 1, "outcome": "refused", "exit_code": 0}
    assert outcome.usage == TOTAL == ledger.usage


def test_a_correction_still_past_the_end_fails_and_never_clamps(capture, tmp_path):
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 4))})
    assert (outcome.status, outcome.reason) == ("failed", "citation_validation")
    assert load(attempt / "citation-validation.json")["reason"] == "line_range_outside_capture"


@pytest.mark.parametrize("value", [
    {"citations": []},
    {"citations": [{"id": "c0001", "revision": "a" * 40, "sha256": PLACEHOLDER,
                    "start_line": 1, "end_line": 3}]},
], ids=["unanswered", "still-malformed"])
def test_a_malformed_correction_answer_fails_as_protocol(capture, tmp_path, value):
    outcome, _, attempt = launch(capture, tmp_path, PLACEHOLDER_HASH, {"output": value})
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == "refused"


@pytest.mark.parametrize("stop", ["budget", "max_turns"])
def test_no_correction_answer_fails_the_session_with_its_original_refusal(
        capture, tmp_path, stop):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END,
                                      {"status": "error", "stop": stop, "output": None})
    assert (outcome.status, outcome.reason) == ("failed", "citation_validation")
    assert outcome.findings == () and not outcome.output_available
    assert load(attempt / "citation-validation.json")["reason"] == "line_range_outside_capture"
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == "no_answer"
    assert outcome.usage == TOTAL == ledger.usage


def test_correction_evidence_from_another_session_is_refused(capture, tmp_path):
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 3)), "session_id": "other"})
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == "failed"


@pytest.mark.parametrize("document", [
    answer(finding(citation(), category="opinion")),
    answer(finding(citation(sha256="0" * 64))),
    answer(finding(citation(end_line=9)), finding(citation(revision="b" * 40))),
], ids=["category", "wrong-hash", "mixed"])
def test_a_non_citation_or_undescribable_refusal_is_not_offered(capture, tmp_path, document):
    outcome, _, attempt = launch(capture, tmp_path, document,
                                 {"output": fix(("c0001", GOOD, 1, 3))})
    assert outcome.status == "failed" and outcome.reason in ("protocol", "citation_validation")
    assert not (attempt / "cwd" / "observed-resume.json").exists()
    assert not (attempt / "citation-correction.json").exists()
    assert load(attempt / "result.json")["citation_correction"] == {
        "offered": False, "defects": 0, "outcome": None, "exit_code": None}


def test_a_valid_answer_records_that_nothing_was_offered(capture, tmp_path):
    outcome, _, attempt = launch(capture, tmp_path, answer(finding(citation())))
    assert outcome.status == "complete"
    assert not (attempt / "cwd" / "observed-resume.json").exists()
    assert load(attempt / "result.json")["citation_correction"] == {
        "offered": False, "defects": 0, "outcome": None, "exit_code": None}


def test_a_session_with_no_budget_left_is_not_offered(capture, tmp_path):
    # 50 tokens used of a 45-token limit: the answer is partial, and nare would refuse to run.
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 3))}, limit=45)
    assert outcome.reason == "citation_validation"
    assert not (attempt / "cwd" / "observed-resume.json").exists()


def test_the_correction_turn_keeps_the_rail_constraint_and_cap(capture, tmp_path):
    rail = ModelRail("openai", "https://example.invalid/v1", "m", "json_schema", 900)
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 3))}, rail=rail)
    assert outcome.status == "complete"
    argv = load(attempt / "cwd" / "observed-resume.json")["argv"]
    for flag, value in (("--structured-output", "json_schema"), ("--max-tokens", "900"),
                        ("--base-url", "https://example.invalid/v1"), ("--provider", "openai")):
        assert argv[argv.index(flag) + 1] == value, flag
    assert "--stream" in argv
    result = load(attempt / "result.json")
    # The first stream's constrained turns are counted as before; the correction is separate.
    assert result["structured_output"] == {"mode": "json_schema", "constrained_turns": 0}
    assert result["max_output_tokens"] == {"limit": 900, "reached": False}


def test_the_prompt_and_schema_are_the_published_functions_of_the_defects(capture, tmp_path):
    from scrutare.findings.citation_correction import CitationDefect

    _, _, attempt = launch(capture, tmp_path, PAST_END, {"output": fix(("c0001", GOOD, 1, 3))})
    record = load(attempt / "citation-correction.json")
    defects = tuple(CitationDefect(**{**item, "problems": tuple(item["problems"])})
                    for item in record["defects"])
    assert record["prompt"] == citation_correction_prompt(defects)
    assert load(attempt / "citation-correction.schema.json") == citation_correction_schema(
        defects)


def test_only_discovery_sessions_are_offered_a_correction(capture):
    from scrutare.engine.assessment_inputs import AssessmentInput, assessor_persona
    from scrutare.engine.discussion_inputs import DiscussionInput
    from scrutare.engine.nare_session import _offers_citation_correction

    descriptor, _, _, _ = prepared(capture)
    assert _offers_citation_correction(descriptor)
    assert not _offers_citation_correction(
        AssessmentInput(assessor_persona("evidence-assessor"), descriptor.inputs, ()))
    assert not _offers_citation_correction(
        DiscussionInput(descriptor.persona, descriptor.inputs, (), (), False, ()))


def test_a_correction_run_that_never_starts_keeps_the_first_usage(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END, {"never_start": True})
    assert (outcome.status, outcome.reason) == ("failed", "citation_validation")
    assert outcome.usage == TokenUsage(40, 10) == ledger.usage
    assert ledger.snapshot()["active_reservations"] == []
    assert load(attempt / "result.json")["citation_correction"] == {
        "offered": True, "defects": 1, "outcome": "no_answer", "exit_code": 2}


def test_a_correction_that_times_out_fails_the_session_as_a_timeout(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END,
                                      {"sleep": 30, "output": fix(("c0001", GOOD, 1, 3))},
                                      timeout=3)
    assert (outcome.status, outcome.reason) == ("failed", "timeout")
    assert not outcome.accounting_complete and outcome.findings == ()
    assert outcome.usage == TokenUsage(40, 10) == ledger.usage
    assert ledger.snapshot()["active_reservations"] == []
    assert load(attempt / "result.json")["citation_correction"] == {
        "offered": True, "defects": 1, "outcome": "failed", "exit_code": None}


def test_a_corrupt_correction_stream_fails_as_protocol_with_uncertain_usage(capture, tmp_path):
    outcome, ledger, attempt = launch(capture, tmp_path, PAST_END,
                                      {"corrupt": True, "output": fix(("c0001", GOOD, 1, 3))})
    assert (outcome.status, outcome.reason) == ("failed", "protocol")
    assert not outcome.accounting_complete
    assert outcome.usage == TOTAL == ledger.usage
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == "failed"


def test_a_session_exactly_at_its_budget_is_not_offered(capture, tmp_path):
    # nare refuses to start a turn once usage reaches the limit.
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 3))}, limit=50)
    assert outcome.reason == "citation_validation"
    assert not (attempt / "cwd" / "observed-resume.json").exists()


def test_the_outcome_is_corrected_only_once_the_session_completes(capture, tmp_path,
                                                                  monkeypatch):
    from scrutare.engine import nare_session

    calls = []
    original = nare_session.validate_prepared_inputs

    def changed(inputs):
        resumed = inputs.root.parent / "sessions/security/attempt-0001/correction-session.json"
        if resumed.exists():
            calls.append(inputs)
            if len(calls) == 2:  # the session's final check, after the corrected answer
                raise nare_session.ReviewInputError("inputs changed")
        return original(inputs)

    monkeypatch.setattr(nare_session, "validate_prepared_inputs", changed)
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 3))})
    assert (outcome.status, outcome.reason) == ("failed", "inputs")
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == "failed"


def test_the_prompt_spells_out_the_answer_shape_and_both_line_bounds(capture, tmp_path):
    _, _, attempt = launch(capture, tmp_path, answer(finding(citation(start_line=5,
                                                                      end_line=6))),
                           {"output": fix(("c0001", GOOD, 1, 3))})
    prompt = load(attempt / "citation-correction.json")["prompt"]
    assert '{"citations": [{"id": ' in prompt
    assert "start_line and end_line must both be at most 3" in prompt


def test_a_saved_correction_with_a_different_type_is_refused(capture, tmp_path):
    # bool/int equality would conceal it: the saved answer is parsed on its own as well.
    saved = {"citations": [dict(id="c0001", revision="a" * 40, sha256=GOOD, start_line=True,
                                end_line=3)]}
    outcome, _, attempt = launch(capture, tmp_path, PAST_END, {
        "output": fix(("c0001", GOOD, 1, 3)), "saved_output": saved})
    assert outcome.status == "failed" and outcome.findings == ()
    assert load(attempt / "result.json")["citation_correction"]["outcome"] != "corrected"
