"""Replay audits a recorded citation correction against the retained streams and inputs."""

import json

import pytest
from test_citation_correction_defects import PLACEHOLDER, answer, citation, finding
from test_citation_correction_session import GOOD, PAST_END, PLACEHOLDER_HASH, fix, launch, load
from test_review_inputs import capture as capture
from test_review_inputs import save_json

from scrutare.replay.audit import _citation_correction_issues


def issues(capture):
    return [(issue.code, issue.path) for issue in
            _citation_correction_issues(capture, load(capture / "config.json"))]


@pytest.mark.parametrize("first,correction,outcome", [
    (PAST_END, {"output": fix(("c0001", GOOD, 1, 3))}, "corrected"),
    (PLACEHOLDER_HASH, {"output": fix(("c0001", GOOD, 1, 3))}, "corrected"),
    (PAST_END, {"output": fix(("c0001", "0" * 64, 1, 3))}, "refused"),
    (PLACEHOLDER_HASH, {"output": {"citations": []}}, "refused"),
    (PAST_END, {"status": "error", "stop": "budget", "output": None}, "no_answer"),
    (answer(finding(citation())), None, None),
    (answer(finding(citation(), category="opinion")), None, None),
], ids=["corrected", "hash-corrected", "refused", "unanswered", "no-answer", "valid",
        "not-offered"])
def test_every_recorded_outcome_audits_clean(capture, tmp_path, first, correction, outcome):
    _, _, attempt = launch(capture, tmp_path, first, correction)
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == outcome
    assert issues(capture) == []


def test_runs_that_never_opted_in_are_not_audited(capture, tmp_path):
    launch(capture, tmp_path, PAST_END, enabled=False)
    assert issues(capture) == []


def where(attempt, capture, name):
    return f"{attempt.relative_to(capture).as_posix()}/{name}"


def corrected(capture, tmp_path, first=PAST_END):
    _, _, attempt = launch(capture, tmp_path, first, {"output": fix(("c0001", GOOD, 1, 3))})
    return attempt


def edit(path, change):
    document = load(path)
    change(document)
    path.write_text(json.dumps(document))


def test_a_changed_prompt_or_defect_list_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    edit(attempt / "citation-correction.json",
         lambda d: d.update(prompt=d["prompt"].replace("one correction turn", "a turn")))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "citation-correction.json"))]
    edit(attempt / "citation-correction.json", lambda d: d["defects"][0].update(last_line=4))
    assert ("citation_correction_disagrees",
            where(attempt, capture, "citation-correction.json")) in issues(capture)


def test_a_correction_that_widened_the_turn_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    edit(attempt / "citation-correction.json",
         lambda d: d["argv"].__setitem__(d["argv"].index("none"), "read"))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "citation-correction.json"))]


@pytest.mark.parametrize("name", ["citation-correction.json", "correction.stdout.jsonl",
                                  "citation-correction.schema.json"])
def test_missing_correction_evidence_is_incomplete(capture, tmp_path, name):
    attempt = corrected(capture, tmp_path)
    (attempt / name).unlink()
    assert issues(capture) == [("citation_correction_evidence_missing",
                                where(attempt, capture, name))]


def test_a_recorded_outcome_the_stream_does_not_support_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    edit(attempt / "result.json", lambda d: d["citation_correction"].update(outcome="refused"))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "result.json"))]


def test_corrected_findings_must_be_exactly_what_the_correction_produced(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    edit(attempt / "result.json",
         lambda d: d["findings"][0]["evidence"]["citations"][0].update(start_line=2))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "result.json"))]


def test_a_tampered_correction_stream_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    path = attempt / "correction.stdout.jsonl"
    lines = path.read_text().splitlines()
    terminal = json.loads(lines[-1])
    terminal["output"]["citations"][0]["sha256"] = "0" * 64
    path.write_text("\n".join([*lines[:-1], json.dumps(terminal)]) + "\n")
    assert ("citation_correction_disagrees", where(attempt, capture, "result.json")) in issues(
        capture)


def test_an_eligible_session_recorded_as_not_offered_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    for name in ("citation-correction.json", "citation-correction.schema.json",
                 "correction.stdout.jsonl", "correction.stderr.txt", "correction-session.json"):
        (attempt / name).unlink()
    edit(attempt / "result.json", lambda d: d.update(citation_correction={
        "offered": False, "defects": 0, "outcome": None, "exit_code": None}))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "result.json"))]


def test_an_ineligible_session_recorded_as_offered_disagrees(capture, tmp_path):
    _, _, attempt = launch(capture, tmp_path, answer(finding(citation(sha256="0" * 64))))
    edit(attempt / "result.json", lambda d: d.update(citation_correction={
        "offered": True, "defects": 1, "outcome": "no_answer", "exit_code": 1}))
    assert ("citation_correction_disagrees", where(attempt, capture, "result.json")) in issues(
        capture)


@pytest.mark.parametrize("record", [
    None, {"offered": False}, "yes",
    {"offered": False, "defects": 0, "outcome": "corrected", "exit_code": None},
    {"offered": False, "defects": 0, "outcome": None},
    {"offered": False, "defects": 0, "outcome": None, "exit_code": "0"}])
def test_a_discovery_session_without_a_well_formed_record_is_unrecorded(
        capture, tmp_path, record):
    _, _, attempt = launch(capture, tmp_path, answer(finding(citation())))
    edit(attempt / "result.json", lambda d: d.pop("citation_correction") if record is None
         else d.update(citation_correction=record))
    assert issues(capture) == [("citation_correction_unrecorded",
                                where(attempt, capture, "result.json"))]


def test_a_record_in_a_run_that_never_opted_in_is_unconfigured(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    config = load(capture / "config.json")
    del config["findings"]["citation_correction"]
    save_json(capture / "config.json", config)
    assert issues(capture) == [("citation_correction_unconfigured",
                                where(attempt, capture, "result.json"))]


def test_the_placeholder_refusal_is_recomputed_as_protocol(capture, tmp_path):
    attempt = corrected(capture, tmp_path, PLACEHOLDER_HASH)
    edit(attempt / "citation-correction.json", lambda d: d.update(refusal={
        "reason": "citation_validation", "citation_reason": "content_hash_mismatch"}))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "citation-correction.json"))]
    assert PLACEHOLDER not in (attempt / "citation-correction.json").read_text()


def test_a_correction_stream_from_another_session_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    edit(attempt / "correction-session.json", lambda d: d.update(id="other"))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "result.json"))]


@pytest.mark.parametrize("flag", ["--structured-output", "--max-tokens", "--budget-tokens"])
def test_a_correction_turn_off_its_session_rail_disagrees(capture, tmp_path, flag):
    from test_citation_correction_session import ModelRail

    rail = ModelRail("openai", None, "m", "json_schema", 900)
    _, _, attempt = launch(capture, tmp_path, PAST_END, {"output": fix(("c0001", GOOD, 1, 3))},
                           rail=rail)
    assert issues(capture) == []

    def drop(document):
        argv = document["argv"]
        index = argv.index(flag)
        del argv[index:index + 2]

    edit(attempt / "citation-correction.json", drop)
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "citation-correction.json"))]


def test_recorded_usage_must_be_the_cumulative_session_usage_for_any_answer(capture, tmp_path):
    _, _, attempt = launch(capture, tmp_path, PAST_END, {"output": fix(("c0001", "0" * 64, 1, 3))})
    assert issues(capture) == []
    edit(attempt / "result.json", lambda d: d["usage"].update(input=40))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "result.json"))]


def test_a_recorded_exit_code_the_stream_contradicts_disagrees(capture, tmp_path):
    attempt = corrected(capture, tmp_path)
    edit(attempt / "result.json", lambda d: d["citation_correction"].update(exit_code=1))
    assert issues(capture) == [("citation_correction_disagrees",
                                where(attempt, capture, "result.json"))]


@pytest.mark.parametrize("correction,outcome", [
    ({"never_start": True}, "no_answer"),
    ({"sleep": 30}, "failed"),
    ({"corrupt": True, "output": fix(("c0001", GOOD, 1, 3))}, "failed"),
], ids=["never-started", "timeout", "corrupt"])
def test_unfinished_corrections_audit_clean(capture, tmp_path, correction, outcome):
    _, _, attempt = launch(capture, tmp_path, PAST_END, correction, timeout=3)
    assert load(attempt / "result.json")["citation_correction"]["outcome"] == outcome
    assert issues(capture) == []


def test_first_evidence_that_does_not_reconcile_owed_no_correction(capture, tmp_path):
    # The runtime refuses such a session as protocol before considering a correction, and
    # replay reconciles it the same way rather than reading the terminal record alone.
    outcome, _, attempt = launch(capture, tmp_path, PAST_END,
                                 {"output": fix(("c0001", GOOD, 1, 3))},
                                 first_step={"saved_output": answer(finding(citation()))})
    assert outcome.reason == "protocol"
    assert load(attempt / "result.json")["citation_correction"]["offered"] is False
    assert issues(capture) == []
