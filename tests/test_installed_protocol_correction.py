"""Installed wheel plus real nare 2026.10.12: protocol replacement and replay."""

import copy
import json

import pytest
from test_installed_citation_correction import first_answer, load, resumed, review
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import installed as installed


def placeholder(provider):
    output = first_answer(provider)
    extra = copy.deepcopy(output["findings"][0])
    extra.update(file="placeholder-unused", line=0)
    extra["evidence"]["citations"] = []
    output["findings"].append(extra)
    return output


@pytest.mark.parametrize("structured", [False, True])
def test_installed_g3_placeholder_corrects_and_replays(tmp_path, installed, wheel_cli, structured):
    result, run, attempt, replay, _ = review(
        tmp_path,
        installed,
        wheel_cli,
        answer=placeholder,
        correction=first_answer,
        structured=structured,
        policy="protocol_correction",
    )
    assert json.loads(result.stdout)["panel_status"] == "complete"
    record = load(attempt / "protocol-correction.json")
    outcome = load(attempt / "result.json")
    assert outcome["protocol_correction"]["outcome"] == "corrected"
    assert len(load(attempt / "session.json")["output"]["findings"]) == 2
    assert len(outcome["findings"]) == 1
    observed = resumed(attempt)
    assert len(observed["calls"]) == 1 and observed["calls"][0]["tools"] == []
    if structured:
        assert observed["calls"][0]["response_schema"] == load(
            attempt / "protocol-correction.schema.json"
        )
    assert observed["calls"][0]["messages"][-1]["content"][-1]["text"] == record["prompt"]
    assert replay.returncode == 0, replay.stderr.decode()
    assert json.loads(replay.stdout)["issues"] == []
    assert json.loads(replay.stdout)["saved_verdict"]["byte_identical"] is True


@pytest.mark.parametrize("enabled,second", [(False, first_answer), (True, placeholder)])
def test_installed_off_and_refused_still_fail(tmp_path, installed, wheel_cli, enabled, second):
    _, run, attempt, _, _ = review(
        tmp_path,
        installed,
        wheel_cli,
        answer=placeholder,
        correction=second,
        enabled=enabled,
        succeeds=False,
        policy="protocol_correction",
    )
    result = load(attempt / "result.json")
    assert result["status"] == "failed"
    if enabled:
        assert result["protocol_correction"]["outcome"] == "refused"
    else:
        assert "protocol_correction" not in result
        assert not (attempt / "correction-session.json").exists()
    from scrutare.replay.audit import _protocol_correction_issues

    assert _protocol_correction_issues(run, load(run / "config.json")) == ()


def four_findings(provider):
    output = first_answer(provider)
    source = output["findings"][0]
    output["findings"] = [dict(copy.deepcopy(source), problem=f"Failure {i}") for i in range(4)]
    return output


def test_installed_g3_unknown_fifth_assessor_is_corrected_and_applied(
    tmp_path, installed, wheel_cli
):
    result, run, _, replay, _ = review(
        tmp_path,
        installed,
        wheel_cli,
        answer=four_findings,
        policy="protocol_correction",
        structured=True,
        assessment=True,
    )
    assert json.loads(result.stdout)["panel_status"] == "complete"
    attempts = list(run.glob("sessions/*/attempt-0001"))
    (assessor,) = [a for a in attempts if (a / "protocol-correction.json").exists()]
    assert len(load(assessor / "session.json")["output"]["assessments"]) == 5
    assert len(load(assessor / "correction-session.json")["output"]["assessments"]) == 4
    assert len(load(run / "assessment.json")["assessments"]) == 4
    assert load(assessor / "result.json")["protocol_correction"]["outcome"] == "corrected"
    assert len(resumed(assessor)["calls"]) == 1
    assert resumed(assessor)["calls"][0]["tools"] == []
    assert replay.returncode == 0, replay.stderr.decode()
    assert json.loads(replay.stdout)["issues"] == []


def test_installed_provider_failure_is_not_offered_a_protocol_correction(
    tmp_path, installed, wheel_cli
):
    _, run, attempt, _, _ = review(
        tmp_path,
        installed,
        wheel_cli,
        answer=placeholder,
        correction=first_answer,
        policy="protocol_correction",
        provider_error=True,
        succeeds=False,
    )
    assert load(attempt / "result.json")["protocol_correction"]["offered"] is False
    assert not (attempt / "correction-session.json").exists()
    from scrutare.replay.audit import _protocol_correction_issues

    assert _protocol_correction_issues(run, load(run / "config.json")) == ()


def test_installed_no_valid_replacement_keeps_original_refusal(tmp_path, installed, wheel_cli):
    _, run, attempt, _, _ = review(
        tmp_path,
        installed,
        wheel_cli,
        answer=placeholder,
        correction=lambda p: {"wrong": []},
        policy="protocol_correction",
        succeeds=False,
    )
    assert load(attempt / "result.json")["protocol_correction"]["outcome"] == "no_answer"
    assert len(resumed(attempt)["calls"]) == 1
    from scrutare.replay.audit import _protocol_correction_issues

    assert _protocol_correction_issues(run, load(run / "config.json")) == ()
