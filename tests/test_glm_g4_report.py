"""Offline report invariants before any qualification calls; no measured data required."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "g4_report_template",
    Path(__file__).resolve().parents[1]
    / "evaluations/reports/2026-10-28-glm-g4-qualification/reproduce.py",
)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def evidence():
    defects = [
        {"path": "$.findings[1].line", "type": "non_positive_line", "requirement": "positive"},
        {
            "path": "$.findings[1].evidence.citations",
            "type": "missing_evidence",
            "requirement": "cited",
        },
    ]
    row = {
        "cell": "bug/aggregate/1",
        "persona": "junior-dev",
        "attempt": "attempt-0001",
        "status": "complete",
        "reason": "done",
        "tokens": 111,
        "first_run_tokens": 50,
        "protocol_correction": {
            "offered": True,
            "defects": 2,
            "outcome": "corrected",
            "exit_code": 0,
        },
        "protocol_correction_request": {"defects": defects},
        "correction_request_output_tokens": [6],
    }
    first = {
        "session": "bug/aggregate/1/sessions/junior-dev/attempt-0001",
        "kind": "discovery",
        "status": "complete",
        "reason": "done",
        "first_answer": "protocol",
        "correctable": defects,
    }
    return row, first


def test_multiple_defect_types_are_one_session_and_one_turn():
    row, first = evidence()
    summary = report.correction_summary([row], [first])
    assert summary["offered"] == 1 and summary["tokens"] == 61
    assert summary["sessions_by_type_and_outcome"] == {
        "non_positive_line": {"corrected": 1},
        "missing_evidence": {"corrected": 1},
    }


@pytest.mark.parametrize("mutation", ["lost_refusal", "lost_request", "extra_turn", "lost_usage"])
def test_mutation_cannot_hide_a_correction_or_its_cost(mutation):
    row, first = evidence()
    if mutation == "lost_refusal":
        first["first_answer"] = "valid"
    if mutation == "lost_request":
        row["protocol_correction_request"] = None
    if mutation == "extra_turn":
        row["correction_request_output_tokens"] = [6, 7]
    if mutation == "lost_usage":
        row["tokens"] = 49
    with pytest.raises(ValueError):
        report.correction_summary([row], [first])


def test_spark_trailing_slash_is_self_hosted_and_paid_fallback_is_refused():
    row = {
        "model_group": "spark/glm-5.3-flash",
        "model": "openai/glm-5.3-flash",
        "api_base": "http://192.168.3.40:8045/v1/",
        "attempted_fallbacks": 0,
    }
    report.verify_serving_rows([row])
    for field, value in [("api_base", "https://api.openai.com/v1"), ("attempted_fallbacks", 1)]:
        with pytest.raises(ValueError):
            report.verify_serving_rows([dict(row, **{field: value})])


def test_actual_discussion_and_reassessment_are_not_counted_as_initial_sessions(tmp_path):
    import json
    from types import SimpleNamespace

    cell = "bug/focused3/1"
    rows = []
    answers = []
    for persona, attempt, kind in [
        ("senior-dev", "attempt-0001", "discovery"),
        ("senior-dev", "attempt-0003", "discussion"),
        ("evidence-assessor", "attempt-0001", "assessment"),
        ("evidence-assessor", "attempt-0002", "assessment"),
    ]:
        rows.append(
            {
                "cell": cell,
                "persona": persona,
                "attempt": attempt,
                "status": "complete",
                "reason": "done",
                "tokens": 10,
                "output_tokens": 5,
                "max_output_tokens": {"limit": 20000, "reached": False},
                "structured_output_mode": "json_schema",
                "request_output_tokens": [5],
                "constrained_turns": 1,
                "protocol_correction": {
                    "offered": False,
                    "defects": 0,
                    "outcome": None,
                    "exit_code": None,
                },
            }
        )
        answers.append(
            {
                "session": f"{cell}/sessions/{persona}/{attempt}",
                "kind": kind,
                "status": "complete",
                "reason": "done",
                "first_answer": "valid",
                "correctable": None,
            }
        )
    mechanical = {
        "cells": [
            {
                "run_id": cell,
                "attempts": [{"attempt": 1, "tokens": 40}],
                "session_statuses": [
                    {"persona": "senior-dev", "status": "complete", "reason": "done", "tokens": 10}
                ],
                "assessor_actual": True,
                "assessor_reason": "done",
                "assessor_tokens": 10,
                "assessor_complete": True,
            }
        ]
    }
    for name, value in [
        ("mechanical-qualification.json", mechanical),
        ("session-diagnosis.json", {"sessions": rows}),
        ("first-answers.json", {"sessions": answers}),
    ]:
        (tmp_path / name).write_text(json.dumps(value))
    summary = report.sessions_summary(tmp_path, SimpleNamespace(cause=lambda r: "complete"))
    assert summary["sessions"] == summary["complete"] == 4
    assert summary["corrections"]["by_phase"] == {"discovery": 1, "discussion": 1, "assessment": 2}


def test_native_failure_keeps_unreconciled_usage_visible_instead_of_dropping_it():
    usage = {"input": 10, "output": 5}
    provider = {
        "evaluator_rows": 2,
        "evaluator_prompt_tokens": 20,
        "evaluator_completion_tokens": 15,
        "evaluator_failed_rows": [],
    }
    with pytest.raises(ValueError):
        report.reconcile_usage(usage, {"requests": 1, "failed_sessions": []}, provider)
    failed = [{"cell": "bug/focused1/1", "persona": "junior-dev", "cause": "session_timeout"}]
    summary = report.reconcile_usage(usage, {"requests": 1, "failed_sessions": failed}, provider)
    assert summary["exact_match"] is False
    assert summary["native_failed_sessions"] == failed
    assert summary["request_difference"] == 1 and summary["output_difference"] == 10


def extractor():
    spec = importlib.util.spec_from_file_location(
        "glm_g4_extractor",
        Path(__file__).resolve().parents[1]
        / "evaluations/reports/2026-10-28-glm-g4-qualification/extract-sessions.py",
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_exporter_preserves_transient_attempt_identity():
    function = extractor().session_identity
    assert function(
        Path("security-auth-order/aggregate/1/sessions/senior-dev/attempt-0001/result.json")
    ) == (
        "security-auth-order/aggregate/1",
        1,
        "security-auth-order/aggregate/1",
        "senior-dev",
        "attempt-0001",
    )
    assert function(
        Path(
            "_retries/security-auth-order/aggregate/1/attempt-0002/"
            "sessions/senior-dev/attempt-0001/result.json"
        )
    ) == (
        "security-auth-order/aggregate/1",
        2,
        "_retries/security-auth-order/aggregate/1/attempt-0002",
        "senior-dev",
        "attempt-0001",
    )


def test_exporter_keeps_correction_request_costs(tmp_path):
    import json

    (tmp_path / "stdout.jsonl").write_text(
        json.dumps({"type": "cost", "detail": {"output": 100}}) + "\n"
    )
    (tmp_path / "correction.stdout.jsonl").write_text(
        json.dumps({"type": "cost", "detail": {"output": 12}}) + "\n"
    )
    assert extractor().request_outputs(tmp_path) == {
        "initial": [100],
        "correction": [12],
        "all": [100, 12],
    }


def test_exporter_redacts_both_provider_error_streams(tmp_path):
    import json

    directory = tmp_path / "experiment/bug/aggregate/1/sessions/senior-dev/attempt-0001"
    directory.mkdir(parents=True)
    data = {
        "status": "failed",
        "reason": "protocol",
        "usage": {"input": 0, "output": 0, "total": 0},
    }
    (directory / "result.json").write_text(json.dumps(data))
    for name, error in [
        ("stdout.jsonl", "private reviewer@example.invalid bearer secret-value"),
        ("correction.stdout.jsonl", "repeating the same chunk for reviewer@example.invalid"),
    ]:
        (directory / name).write_text(json.dumps({"type": "result", "error": error}) + "\n")
    result = extractor().extract(tmp_path)
    assert "reviewer@example.invalid" not in json.dumps(result)
    assert "secret-value" not in json.dumps(result)
    row = result["sessions"][0]
    assert row["nare_error"] == "provider_or_native_error"
    assert row["correction_error"] == "model_repetition"


def test_correction_repetition_is_failure_cause_not_original_anchor():
    from types import SimpleNamespace

    row = {
        "reason": "protocol",
        "persona": "junior-dev",
        "nare_error": None,
        "correction_error": "model_repetition",
        "protocol_correction": {"offered": True, "outcome": "no_answer"},
        "finding_answer_anchors": [{"line": 0}],
    }
    assert report.session_cause(row, {}, SimpleNamespace(cause=lambda row: "wrong")) == (
        "model_repetition"
    )


def test_zero_usage_assessor_failure_is_retained(tmp_path):
    import json
    from types import SimpleNamespace

    cell = "bug/assessed/1"
    row = {
        "cell": cell,
        "persona": "evidence-assessor",
        "attempt": "attempt-0001",
        "status": "failed",
        "reason": "provider",
        "tokens": 0,
        "output_tokens": 0,
        "max_output_tokens": {"limit": 20000, "reached": False},
        "structured_output_mode": "json_schema",
        "request_output_tokens": [],
        "constrained_turns": 0,
        "protocol_correction": None,
    }
    mechanical = {
        "cells": [
            {
                "run_id": cell,
                "attempts": [{"attempt": 1, "tokens": 0}],
                "session_statuses": [],
                "assessor_actual": False,
                "assessor_reason": "provider",
                "assessor_tokens": 0,
                "assessor_complete": False,
            }
        ]
    }
    for name, value in [
        ("mechanical-qualification.json", mechanical),
        ("session-diagnosis.json", {"sessions": [row]}),
        ("first-answers.json", {"sessions": []}),
    ]:
        (tmp_path / name).write_text(json.dumps(value))
    result = report.sessions_summary(tmp_path, SimpleNamespace(cause=lambda row: "provider"))
    assert result["sessions"] == 1 and result["complete"] == 0
    assert result["failed_sessions"][0]["cause"] == "provider"


def test_recovery_preserves_missing_matrix_cells_and_raw_artifact_hashes(tmp_path):
    import json

    spec = importlib.util.spec_from_file_location(
        "glm_g4_recovery",
        Path(__file__).resolve().parents[1]
        / "evaluations/reports/2026-10-28-glm-g4-qualification/recover-exit.py",
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    (tmp_path / "experiment").mkdir()
    (tmp_path / "gate.json").write_text(
        json.dumps({"cases": ["bug"], "arms": ["aggregate"], "repeats": 3})
    )
    (tmp_path / "experiment/runs.jsonl").write_text(
        json.dumps(
            {
                "run_id": "bug/aggregate/1",
                "status": "failed",
                "usage": {"input": 12, "output": 3, "total": 15},
            }
        )
        + "\n"
    )
    recovered = loaded.inventory(tmp_path)
    assert recovered["qualified"] is False
    assert recovered["expected_cells"] == 3
    assert recovered["missing_cells"] == ["bug/aggregate/2", "bug/aggregate/3"]
    assert recovered["retained_runs"][0]["status"] == "failed"
    assert recovered["retained_runs"][0]["tokens"] == 15
    assert "experiment/runs.jsonl" in recovered["artifact_sha256"]


def test_router_trailing_slash_uses_observed_fields_not_frozen_equality_flag():
    row = {
        "agent": "python-httpx2",
        "key": "proxy master key",
        "model_group": "spark/glm-5.3-flash",
        "model": "openai/glm-5.3-flash",
        "api_base": "http://192.168.3.40:8045/v1/",
        "attempted_fallbacks": 0,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "spend": 0,
        "status": "success",
    }
    window = {"window_utc": ["start", "end"], "rows": [row]}
    router = {
        "ok": False,
        "model_group_alias": None,
        "fallback_fields": {"fallbacks": None},
        "wildcard_groups": [],
        "glm_deployments": [{"model": row["model"], "api_base": row["api_base"]}],
    }
    doc = {
        "windows": {k: window for k in ("smoke", "g4", "prelaunch")},
        **{k: router for k in ("router_before_smoke", "router_prelaunch", "router_after")},
    }
    assert report.provider_summary(doc)["g4"]["evaluator_paid_provider_rows"] == 0
    router["fallback_fields"]["fallbacks"] = ["paid"]
    with pytest.raises(ValueError, match="fallback"):
        report.provider_summary(doc)
    router["fallback_fields"]["fallbacks"] = None
    router["ok"] = True
    router["model_group_alias"] = {"spark/glm-5.3-flash": "paid-model"}
    with pytest.raises(ValueError, match="fallback"):
        report.provider_summary(doc)


def test_smoke_waiver_preserves_failed_status_and_accounts_for_inflight_request():
    package = Path(report.__file__).parent
    provider = {
        "evaluator_rows": 4,
        "evaluator_prompt_tokens": 14545,
        "evaluator_completion_tokens": 13667,
    }
    result = report.smoke_summary(package, provider, "2026-10-28T15:45:39Z")
    assert result["session_status"] == "failed"
    assert result["completion_waived"] is True
    assert result["native_tokens"] == 12188
    assert result["proxy_tokens"] == 28212
    assert result["inflight_tokens"] == 16024


@pytest.mark.parametrize("mutation", ["unposted", "late", "tamper", "lost_inflight"])
def test_smoke_waiver_refuses_unposted_late_or_changed_evidence(tmp_path, mutation):
    import json
    import shutil

    source = Path(report.__file__).parent
    for name in (
        "g4-amendment.json",
        "amendment-post.json",
        "amendment-on-51.md",
        "launch-g4-amended.py.txt",
        "smoke-summary.json",
        "smoke-accounting-diagnosis.json",
        "smoke-activation-verification.json",
    ):
        shutil.copy(source / name, tmp_path / name)
    target = "amendment-post.json" if mutation in ("unposted", "late") else "g4-amendment.json"
    value = json.loads((tmp_path / target).read_text())
    if mutation == "unposted":
        value["url"] = "unposted"
    elif mutation == "late":
        value["posted_utc"] = "2026-10-28T16:00:00Z"
    elif mutation == "tamper":
        value["waived_check"] = "all failures"
    elif mutation == "lost_inflight":
        target = "smoke-accounting-diagnosis.json"
        value = json.loads((tmp_path / target).read_text())
        value["unreported_inflight_request"]["completion_tokens"] = 0
    (tmp_path / target).write_text(json.dumps(value))
    with pytest.raises(ValueError):
        report.smoke_summary(
            tmp_path,
            {
                "evaluator_rows": 4,
                "evaluator_prompt_tokens": 14545,
                "evaluator_completion_tokens": 13667,
            },
            "2026-10-28T15:45:39Z",
        )


def test_actual_package_reproduces_the_published_gate_and_all_losses():
    package = Path(report.__file__).parent
    report.load_c1(package.parent).verify_hashes(package)
    result = report.build(package, package.parent)
    assert result == report.read(package / "summary.json")
    assert result["qualified"] is False
    assert len(result["gate_losses"]) == 3
    assert result["spend_reconciliation"]["exact_match"] is True


@pytest.mark.parametrize("mutation", ["settings", "lost_failure", "cap", "correction", "paid"])
def test_actual_data_mutations_cannot_preserve_the_published_claim(tmp_path, mutation):
    import json
    import shutil

    source = Path(report.__file__).parent
    package = tmp_path / "package"
    shutil.copytree(source, package, ignore=shutil.ignore_patterns("__pycache__"))
    name = {
        "settings": "g4/evaluation.json",
        "lost_failure": "g4/session-diagnosis.json",
        "cap": "g4/session-diagnosis.json",
        "correction": "g4/session-diagnosis.json",
        "paid": "provider-evidence.json",
    }[mutation]
    value = report.read(package / name)
    if mutation == "settings":
        value["findings"]["protocol_correction"] = False
    elif mutation == "lost_failure":
        value["sessions"] = [r for r in value["sessions"] if r["status"] == "complete"]
    elif mutation == "cap":
        value["sessions"][0]["max_output_tokens"]["limit"] = 20001
    elif mutation == "correction":
        value["sessions"][0]["protocol_correction"]["offered"] = True
    else:
        row = next(r for r in value["windows"]["g4"]["rows"] if r["agent"] == "python-httpx2")
        row["api_base"] = "https://api.openai.com/v1"
    (package / name).write_text(json.dumps(value))
    with pytest.raises(ValueError):
        report.build(package, source.parent)


def test_changed_spend_cannot_reproduce_the_existing_summary_even_with_new_file_hash(tmp_path):
    import json
    import shutil
    import subprocess
    import sys

    source = Path(report.__file__).parent
    package = tmp_path / "package"
    shutil.copytree(source, package, ignore=shutil.ignore_patterns("__pycache__"))
    name = "provider-evidence.json"
    value = report.read(package / name)
    row = next(r for r in value["windows"]["g4"]["rows"] if r["agent"] == "python-httpx2")
    row["prompt_tokens"] += 1
    (package / name).write_text(json.dumps(value))
    manifest = report.read(package / "published-sha256.json")
    manifest[name] = report.sha(package / name)
    (package / "published-sha256.json").write_text(json.dumps(manifest))
    output = tmp_path / "summary.json"
    result = subprocess.run(
        [
            sys.executable,
            str(source / "reproduce.py"),
            "--package",
            str(package),
            "--reports",
            str(source.parent),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "recomputed summary differs" in result.stderr
    assert not output.exists()
