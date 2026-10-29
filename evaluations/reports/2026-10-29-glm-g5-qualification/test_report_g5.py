"""Offline report invariants: attempts, unknown cost, unique requests and selection."""

import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("report_g5", Path(__file__).with_name("reproduce.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def session():
    return {
        "cell": "defect/assessed/1",
        "cell_attempt": 1,
        "cell_attempt_prefix": "defect/assessed/1",
        "persona": "senior-dev",
        "attempt": "attempt-0001",
        "status": "complete",
        "reason": "end_turn",
        "input_tokens": 2,
        "output_tokens": 3,
        "tokens": 5,
        "initial_request_output_tokens": [3],
        "correction_request_output_tokens": [],
        "request_output_tokens": [3],
    }


def request():
    return {
        "run_id": "defect/assessed/1",
        "attempt": 1,
        "session": "sessions/senior-dev/attempt-0001",
        "stream": "stdout.jsonl",
        "event": 0,
        "id": "a" * 64,
        "source_sha256": "b" * 64,
        "usage": {"input": 2, "output": 3, "cache_read": 0, "cache_write": 0, "total": 5},
        "paid_calls": 0,
        "fallbacks": 0,
        "model": "openai/glm-5.3-flash",
        "api_base": "http://192.168.3.40:8045/v1/",
    }


def test_ledger_reproduces_all_costs_with_trailing_slash_spark():
    r = m.verify_ledger({"requests": [request()], "supplements": []}, [session()])
    assert (
        r["exact_match"]
        and r["provider_usage"]["total"] == 5
        and r["paid_calls"] == r["fallbacks"] == 0
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_id",
        "duplicate_source",
        "wrong_cost",
        "paid",
        "fallback",
        "unassigned",
        "missing_cost",
    ],
)
def test_ledger_refuses_changed_or_unattributed_requests(mutation):
    a = request()
    rows = [a]
    s = [session()]
    if mutation == "duplicate_id":
        rows.append(copy.deepcopy(a))
    if mutation == "duplicate_source":
        b = copy.deepcopy(a)
        b["id"] = "c" * 64
        rows.append(b)
    if mutation == "wrong_cost":
        a["usage"]["input"] += 1
        a["usage"]["total"] += 1
    if mutation == "paid":
        a["paid_calls"] = 1
    if mutation == "fallback":
        a["fallbacks"] = 1
    if mutation == "unassigned":
        a["session"] = None
    if mutation == "missing_cost":
        rows = []
    with pytest.raises(ValueError):
        m.verify_ledger({"requests": rows, "supplements": []}, s)


def test_failed_request_supplement_is_charged_once_without_native_flag_rewrite():
    a = request()
    a.update(
        stream=None,
        event=None,
        usage={"input": 7, "output": 11, "cache_read": 0, "cache_write": 0, "total": 18},
    )
    s = session()
    s.update(
        status="failed",
        reason="timeout",
        input_tokens=0,
        output_tokens=0,
        tokens=0,
        request_output_tokens=[],
        initial_request_output_tokens=[],
    )
    supplement = {k: a[k] for k in ("id", "run_id", "attempt", "session")}
    supplement.update(reason="missing_failed_request", session_result_sha256="c" * 64)
    r = m.verify_ledger({"requests": [a], "supplements": [supplement]}, [s])
    assert (
        r["provider_usage"]["total"] == 18
        and r["native_usage"]["total"] == 0
        and r["supplement_usage"]["total"] == 18
    )
    assert not r["exact_match"] and s["status"] == "failed"


def test_first_attempt_view_never_borrows_rerun_findings_or_assessor():
    cells = [
        {
            "run_id": "defect/assessed/1",
            "status": "complete",
            "accounting_complete": True,
            "findings": 1,
            "evidence_valid": True,
            "assessor_complete": True,
            "attempts": [
                {"status": "failed", "accounting_complete": True},
                {"status": "complete", "accounting_complete": True},
            ],
        },
        {
            "run_id": "clean/aggregate/1",
            "status": "complete",
            "accounting_complete": True,
            "findings": 0,
            "evidence_valid": True,
            "assessor_complete": False,
            "attempts": [{"status": "complete", "accounting_complete": True}],
        },
    ]
    views = m.derive_views(cells, ["clean"])
    assert views["rule_a"]["complete"] == 2 and views["first_attempt"]["complete"] == 1
    assert views["rule_a"]["assessed"] == 1 and views["first_attempt"]["assessed"] == 0
    assert (
        views["rule_a"]["defect_nonempty"] == 1 and views["first_attempt"]["defect_nonempty"] == 0
    )


def test_export_keeps_failed_verdict_and_spend_when_metadata_is_missing(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "export_g5", Path(__file__).with_name("export.py")
    )
    e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e)
    import json

    job = tmp_path / "g5"
    job.mkdir()
    out = tmp_path / "public"
    for n in ("preparation.json", "gate.json", "evaluation.json"):
        (job / n).write_text("{}")
    (job / "provider-ledger.json").write_text('{"attribution_failed":true,"requests":[]}')
    (job / "mechanical-qualification.json").write_text(
        '{"qualification":"not_qualified","tests":{"missing":false}}'
    )
    e.export_job(job, out, "offline-secret")
    assert (
        json.loads((out / "mechanical-qualification.json").read_text())["qualification"]
        == "not_qualified"
    )
    assert json.loads((out / "provider-ledger.json").read_text())["attribution_failed"]
    receipt = json.loads((out / "export-receipt.json").read_text())
    assert receipt["artifacts"]["experiment/experiment.json"]["state"] == "missing"
    assert receipt["artifacts"]["experiment/snapshot.json"]["state"] == "missing"


def export_module():
    spec = importlib.util.spec_from_file_location(
        "export_g5_paths", Path(__file__).with_name("export.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corrupt_metadata_remains_explicit_while_failed_spend_is_exported(tmp_path):
    import json

    module = export_module()
    job = tmp_path / "g5"
    (job / "experiment").mkdir(parents=True)
    (job / "experiment/snapshot.json").write_text("{broken")
    (job / "provider-ledger.json").write_text('{"attribution_failed":true,"requests":[]}')
    out = tmp_path / "export"
    module.export_job(job, out, "offline-secret")
    receipt = json.loads((out / "export-receipt.json").read_text())
    assert receipt["artifacts"]["experiment/snapshot.json"]["state"] == "unreadable"
    assert (out / "provider-ledger.json").exists()


def test_missing_router_and_spend_reads_are_unknown_not_empty_success(tmp_path):
    module = export_module()
    result = module.provider_evidence(tmp_path, "offline-secret")
    assert all(v["state"] == "missing" for v in result["routers"].values())
    assert all(v["state"] == "missing" for v in result["windows"].values())
    summary = m.provider_summary(result)
    assert all(v["unknown_usage"] and v["known_rows"] is None for v in summary.values())


def test_interrupted_export_is_atomic_and_can_be_resumed(tmp_path, monkeypatch):

    module = export_module()
    h = tmp_path / "private"
    (h / "g5").mkdir(parents=True)
    (h / "freeze-g5.json").write_text('{"files":{},"script_sha256":{}}')
    for name in ("freeze-post.json", "freeze-comment-readback.json"):
        (h / name).write_text("{}")
    for name in ("freeze-on-51.md", "rule-a-common-predicates.md"):
        (h / name).write_text("offline dummy")
    destination = tmp_path / "reports/package"

    def interrupted(*args, **kwargs):
        raise OSError("simulated publication interruption")

    monkeypatch.setattr(module.subprocess, "run", interrupted)
    with pytest.raises(OSError, match="interruption"):
        module.export_package(h, destination, "offline-secret")
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def matrix():
    cells = []
    for case in ("clean", "security", "contract"):
        for arm in m.ARMS:
            for repeat in range(1, 4):
                cells.append(
                    {
                        "run_id": f"{case}/{arm}/{repeat}",
                        "status": "complete",
                        "accounting_complete": True,
                        "findings": 0 if case == "clean" else 1,
                        "evidence_valid": True,
                        "assessor_complete": arm != "aggregate",
                        "replay_valid": True,
                        "selected_attempt": 1,
                        "attempts": [
                            {
                                "attempt": 1,
                                "status": "complete",
                                "accounting_complete": True,
                                "terminal": True,
                                "failed_sessions": 0,
                                "retryable": False,
                                "ledger_reconciled": True,
                                "provenance_valid": True,
                                "routing_valid": True,
                                "tokens": 10,
                                "paid_calls": 0,
                                "fallbacks": 0,
                            }
                        ],
                    }
                )
    return cells


@pytest.mark.parametrize("mutation", ["none", "empty", "bad_selection", "invalid_reroll"])
def test_rule_a_matrix_needs_all_24_evidence_and_bounded_selection(mutation):
    import copy

    cells = matrix()
    cell = next(c for c in cells if c["run_id"] == "security/assessed/1")
    if mutation == "empty":
        cell["findings"] = 0
    if mutation in ("bad_selection", "invalid_reroll"):
        first = cell["attempts"][0]
        second = copy.deepcopy(first)
        second["attempt"] = 2
        first.update(status="failed", failed_sessions=1, retryable=True)
        cell["attempts"].append(second)
        cell["selected_attempt"] = 2 if mutation == "invalid_reroll" else 1
        if mutation == "invalid_reroll":
            first.update(failed_sessions=0, retryable=False)
    tests = m.policy_tests(
        cells,
        {
            "cases": ["clean", "security", "contract"],
            "clean_cases": ["clean"],
            "max_tokens_per_run": 200000,
            "holdout_used": False,
        },
        {"status": "complete"},
        {"valid": True, "paid_calls": 0, "fallbacks": 0},
    )
    assert all(tests.values()) == (mutation == "none")


@pytest.mark.parametrize("mutation", ["none", "missing_reviewer", "missing_assessor"])
def test_native_attempt_proves_reviewers_and_actual_selected_assessor(mutation):
    rows = []
    for persona in ("senior-dev", "junior-dev", "security", "devops", "evidence-assessor"):
        r = session()
        r["persona"] = persona
        rows.append(r)
    c = {
        "run_id": "defect/assessed/1",
        "status": "complete",
        "selected_attempt": 1,
        "assessor_complete": True,
        "attempts": [{"attempt": 1}],
    }
    if mutation == "missing_reviewer":
        rows = [r for r in rows if r["persona"] != "junior-dev"]
    if mutation == "missing_assessor":
        rows = [r for r in rows if r["persona"] != "evidence-assessor"]
    if mutation == "none":
        assert m.verify_pipeline_sessions([c], rows) is True
    else:
        with pytest.raises(ValueError):
            m.verify_pipeline_sessions([c], rows)


def test_failed_attempt_findings_cannot_enter_automated_observations():
    from types import SimpleNamespace

    matcher = SimpleNamespace(decide=lambda r, i: "label", finding_id=lambda r, i: "hash")
    runs = [
        {
            "run_id": "defect/assessed/1",
            "case_id": "defect",
            "status": "failed",
            "accounting_complete": True,
            "findings": [{}],
            "attempts": [{}],
            "latency_seconds": 5,
        },
        {
            "run_id": "defect/assessed/2",
            "case_id": "defect",
            "status": "complete",
            "accounting_complete": True,
            "findings": [{}],
            "attempts": [{}],
            "latency_seconds": 7,
        },
    ]
    result = m.observations(runs, ["clean"], matcher)
    assert result["accepted_findings"] == result["automated_matches"] == 1
    assert result["decisions"][0]["run_id"] == "defect/assessed/2"
    assert result["pipeline_latency_seconds_including_failed_attempts"]["mean"] == 6


def test_unknown_spend_retains_nonzero_native_lower_bound():
    provider = {
        "g5": {"available": False},
        "smoke": {"available": True, "nominal_attribution_usd": 0.1},
    }
    ledger = {
        "valid": False,
        "provider_usage": {"total": 0},
        "paid_calls": 0,
        "fallbacks": 0,
        "requests": 0,
    }
    smoke = {
        "verified": True,
        "cumulative_usage": {"total": 5},
        "reconciled_cumulative_usage": {"total": 5},
    }
    cost = m.operation_cost({"total": 855}, ledger, provider, smoke, {})
    assert cost["total_tokens"] is None and cost["nominal_attribution_usd"] is None
    assert cost["known_tokens_lower_bound"] == 860
    assert cost["g5_paid_calls"] is None and cost["g5_fallbacks"] is None
    assert cost["known_nominal_attribution_usd"] == 0.1


@pytest.mark.parametrize(
    "failure", ["attribution", "spend", "routing", "replay", "focused_evidence"]
)
def test_first_attempt_verdict_keeps_all_common_predicates(failure):
    tests = {
        "all_attempts_reconciled": True,
        "zero_paid_calls_and_fallbacks": True,
        "all_attempts_provenance_routing": True,
        "accepted_replay_valid": True,
        "all_24_defect_cells_nonempty_valid": True,
    }
    key = {
        "attribution": "all_attempts_reconciled",
        "spend": "zero_paid_calls_and_fallbacks",
        "routing": "all_attempts_provenance_routing",
        "replay": "accepted_replay_valid",
        "focused_evidence": "all_24_defect_cells_nonempty_valid",
    }[failure]
    tests[key] = False
    views = {
        "first_attempt": {
            "complete": 36,
            "clean_complete": 12,
            "defect_nonempty": 24,
            "aggregate": 6,
            "assessed": 6,
        }
    }
    assert not all(m.first_attempt_tests(tests, views).values())


def test_missing_focused_trace_is_unknown_even_when_record_is_omitted():
    cells = [{"run_id": "defect/focused3/1", "attempts": [{"attempt": 1}, {"attempt": 2}]}]
    d = m.discussion_summary(cells, {"attempts": []})
    assert d["expected_focused_attempts"] == d["unavailable_traces"] == 2
    assert d["rounds_reported"] is None and d["known_rounds_subtotal"] == 0


def test_truncated_load_and_wrong_shaped_spend_are_retained_as_errors(tmp_path):
    module = export_module()
    e = tmp_path / "evidence"
    e.mkdir()
    (e / "load-g5-samples.jsonl").write_text(
        '{"window_utc":[],"by_agent_key_group_base":{}}\n{"window_utc":'
    )
    (e / "g5-rows.json").write_text("null")
    result = module.provider_evidence(tmp_path, "offline-secret")
    assert len(result["samples_15min"]) == 1 and result["sample_receipt"]["errors"]
    assert result["windows"]["g5"]["state"] == "wrong_shape"


def failed_package(tmp_path, monkeypatch, scenario):
    """Synthetic export/build fixture; predecessor/freeze checks are isolated here.

    No fixture is a measurement or a qualification. Immutable checks and actual
    predecessor reproduction are verified separately on the real retained data.
    """
    import json
    from types import SimpleNamespace

    export = export_module()
    job = tmp_path / "private/g5"
    (job / "experiment").mkdir(parents=True)
    out = tmp_path / "package"
    out.mkdir()
    gate = {
        "cases": ["clean", "defect", "other"],
        "clean_cases": ["clean"],
        "max_tokens_per_run": 200000,
        "holdout_used": False,
    }
    prep = {"release_tag": "synthetic offline fixture", "source_commit": "0" * 40}
    native = {"input": 500, "output": 355, "cache_read": 0, "cache_write": 0, "total": 855}
    attempt = copy.deepcopy(matrix()[0]["attempts"][0])
    attempt.update(
        status="failed",
        usage=native,
        tokens=855,
        provenance_valid=False,
        failed_sessions=1,
        retryable=False,
    )
    cell = {
        "run_id": "defect/assessed/1",
        "run_dir": str(job / "experiment/defect/assessed/1"),
        "status": "failed",
        "accounting_complete": False,
        "findings": 0,
        "evidence_valid": False,
        "assessor_complete": False,
        "replay_valid": False,
        "selected_attempt": None,
        "attempts": [attempt],
        "usage": native,
    }
    attempt["run_dir"] = cell["run_dir"]
    raw = {**cell, "findings": [], "case_id": "defect", "latency_seconds": 3}
    mechanical = {
        "cells": [cell],
        "tests": {},
        "cumulative_usage": native,
        "qualification": "not_qualified",
    }
    row = request()
    row.update(run_id=None, attempt=None, session=None, stream=None, event=None)
    requests = [] if scenario == "unavailable_g5_spend" else [row]
    values = {
        "preparation.json": prep,
        "gate.json": gate,
        "evaluation.json": {},
        "mechanical-qualification.json": mechanical,
        "provider-ledger.json": {
            "requests": requests,
            "supplements": [],
            "attribution_failed": True,
        },
        "experiment/experiment.json": {
            "status": "incomplete",
            "runs": [raw],
            "cumulative_usage": native,
        },
    }
    for name, data in values.items():
        (job / name).write_text(json.dumps(data))
    export.export_job(job, out / "g5", "offline-secret")
    if scenario == "missing_inspection":
        (out / "g5/mechanical-qualification.json").unlink()
    if scenario != "extraction_failure":
        sr = session()
        sr.update(
            status="failed",
            reason="timeout",
            input_tokens=500,
            output_tokens=355,
            tokens=855,
            initial_request_output_tokens=[355],
            request_output_tokens=[355],
        )
        (out / "g5/session-diagnosis.json").write_text(json.dumps({"sessions": [sr]}))
    (out / "g5/first-answers.json").write_text('{"sessions":[],"extraction_succeeded":false}')
    served = {
        "agent": "python-httpx2",
        "key": "proxy master key",
        "model_group": "spark/glm-5.3-flash",
        "model": "openai/glm-5.3-flash",
        "api_base": "http://192.168.3.40:8045/v1",
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "spend": 0.1,
        "attempted_fallbacks": 0,
    }
    g5_row = copy.deepcopy(served)
    if scenario == "window_ledger_mismatch":
        g5_row["completion_tokens"] = 4
    evidence = {
        "windows": {
            "smoke": {"state": "available", "rows": [served]},
            "prelaunch": {"state": "available", "rows": []},
            "g5": {"state": "missing", "rows": []}
            if scenario == "unavailable_g5_spend"
            else {"state": "available", "rows": [g5_row]},
        },
        "samples_15min": [],
        "launch": {"state": "missing"},
        "sample_receipt": {"state": "missing"},
    }
    (out / "provider-evidence.json").write_text(json.dumps(evidence))
    usage = {"input": 2, "output": 3, "cache_read": 0, "cache_write": 0, "total": 5}
    smoke = {
        "verified": True,
        "session_complete": True,
        "completion_waived": False,
        "cumulative_usage": usage,
        "reconciled_cumulative_usage": usage,
        "completed_cells": 3,
        "smoke_attempts": 3,
    }
    if scenario != "unavailable_smoke_accounting":
        (out / "smoke-verification.json").write_text(json.dumps(smoke))
    (out / "smoke-summary.json").write_text(
        json.dumps(
            {
                "started_utc": "2026-10-28T00:00:00Z",
                "ended_utc": "2026-10-28T00:01:00Z",
                "cumulative_usage": usage,
            }
        )
    )
    (out / "freeze-record.json").write_text(json.dumps({"files": {"fixture.whl": "0" * 64}}))
    (out / "freeze-post.json").write_text('{"url":"https://example.invalid/offline-fixture"}')
    reports = tmp_path / "reports"
    baseline = {"jobs": {}}
    for label, directory in (("g4", m.G4), ("g3", m.G3), ("c1", m.C1), ("s2", m.S2)):
        baseline["jobs"][label] = {
            "gate_items": {
                "cells_complete_and_accounted": 0,
                "cells_complete_and_accounted_first_attempt": 0,
                "aggregate_defect_cells_nonempty_valid_v2": 0,
                "assessed_defect_cells_actual_complete_assessor": 0,
            },
            "usage_all_attempts": {"total": 0},
            "cell_latency_seconds": {"mean": 0},
        }
        d = reports / directory / label
        d.mkdir(parents=True)
        (d / "mechanical-qualification.json").write_text('{"cells":[]}')
        (d / "gate.json").write_text('{"clean_cases":["clean"]}')
    (reports / m.G4 / "summary.json").write_text(json.dumps(baseline))
    for directory, field in (
        (m.C1, "spend_rows_c1_window_reread"),
        (m.S2, "spend_rows_s2_window_reread"),
    ):
        (reports / directory / "provider-evidence.json").write_text(
            json.dumps(
                {
                    field: {
                        "by_client_and_key": {
                            "python-httpx2 | proxy master key | fixture": {
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                            }
                        }
                    }
                }
            )
        )
    matcher = SimpleNamespace(
        verify_hashes=lambda p: None, decide=lambda r, i: None, finding_id=lambda r, i: "unused"
    )
    prior = SimpleNamespace(
        load_c1=lambda r: matcher,
        build=lambda p, r: baseline,
        correction_summary=lambda *args: {"offered": 0},
    )
    monkeypatch.setattr(m, "helper", lambda reports: prior)
    monkeypatch.setattr(m, "frozen_checks", lambda p, r: (prep, gate))
    return out, reports


@pytest.mark.parametrize(
    "scenario",
    [
        "unavailable_g5_spend",
        "extraction_failure",
        "corrupt_provenance",
        "window_ledger_mismatch",
        "unavailable_smoke_accounting",
        "missing_inspection",
    ],
)
def test_failed_export_build_and_render_keep_known_costs_and_unknown_totals(
    tmp_path, monkeypatch, scenario
):
    import json
    import subprocess
    import sys

    package, reports = failed_package(tmp_path, monkeypatch, scenario)
    summary = m.build(package, reports)
    assert not summary["qualified"] and not summary["first_attempt_perfection_pass"]
    assert summary["native_cumulative_usage"]["total"] == 855
    assert summary["operation_cost"]["total_tokens"] is None
    assert summary["operation_cost"]["known_tokens_lower_bound"] == 860
    if scenario == "window_ledger_mismatch":
        assert not summary["tests"]["provider_window_ledger_consistent"]
    if scenario == "extraction_failure":
        assert "native attempt tokens differ" in summary["evidence_audit_errors"]
    (package / "summary.json").write_text(json.dumps(summary))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("render.py")), str(package)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    text = (package / "README.md").read_text()
    assert "did not pass" in text and "known token lower bound: 860" in text
    assert "total paid calls, fallbacks and external spend are unknown" in text
    assert "operation tokens including smoke: unknown" in text
    assert "Router and spend records cover smoke and every qualification attempt" not in text


def test_full_atomic_export_build_render_survives_corrupt_optional_evidence(tmp_path, monkeypatch):
    import hashlib
    import json
    import subprocess
    import sys
    from types import SimpleNamespace

    fixture, reports = failed_package(tmp_path, monkeypatch, "extraction_failure")
    e = export_module()
    h = tmp_path / "private"
    evidence = h / "evidence"
    evidence.mkdir()
    wheel = h / "g5/fixture.whl"
    wheel.write_bytes(b"offline fixture; never executed")
    (h / "freeze-g5.json").write_text(
        json.dumps(
            {
                "files": {"fixture.whl": hashlib.sha256(wheel.read_bytes()).hexdigest()},
                "script_sha256": {},
            }
        )
    )
    (h / "freeze-post.json").write_text('{"url":"https://example.invalid/offline-fixture"}')
    (h / "freeze-comment-readback.json").write_text("{}")
    for name in ("freeze-on-51.md", "rule-a-common-predicates.md"):
        (h / name).write_text("synthetic offline fixture")
    for name in ("smoke-summary.json", "smoke-verification.json"):
        (evidence / name).write_text((fixture / name).read_text())
    bad = '{"window_utc":[],"by_agent_key_group_base":{"malformed":{}}}\n{"window_utc":'
    (evidence / "load-g5-samples.jsonl").write_text(bad)
    (evidence / "g5-rows.json").write_text("null")
    target = tmp_path / "atomic-report"
    with monkeypatch.context() as isolated:
        isolated.setattr(
            e.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=1, stdout="", stderr="offline extraction unavailable"
            ),
        )
        e.export_package(h, target, hashlib.sha256(str(tmp_path).encode()).hexdigest())
    assert (evidence / "load-g5-samples.jsonl").read_text() == bad
    assert (evidence / "g5-rows.json").read_text() == "null"
    data = json.loads((target / "provider-evidence.json").read_text())
    assert data["windows"]["g5"]["state"] == "wrong_shape"
    assert len(data["sample_receipt"]["errors"]) == 2
    summary = m.build(target, reports)
    assert not summary["qualified"]
    assert summary["operation_cost"]["total_tokens"] is None
    assert summary["operation_cost"]["known_tokens_lower_bound"] == 860
    (target / "summary.json").write_text(json.dumps(summary))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("render.py")), str(target)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "known token lower bound: 860" in (target / "README.md").read_text()


@pytest.mark.parametrize("mutation", ["status", "accounting", "usage", "missing", "duplicate"])
def test_measured_attempt_execution_must_match_inspected_attempts(mutation):
    attempt = {
        "attempt": 1,
        "status": "complete",
        "accounting_complete": True,
        "usage": {"input": 2, "output": 3, "total": 5},
        "error": None,
    }
    cells = [{"run_id": "defect/assessed/1", "attempts": [attempt]}]
    execution = {
        "attempts": [
            {
                "run_id": "defect/assessed/1",
                "attempt": 1,
                "state": "available",
                "execution": copy.deepcopy(attempt),
            }
        ]
    }
    assert m.verify_attempt_execution(cells, execution)
    row = execution["attempts"][0]
    if mutation == "status":
        row["execution"]["status"] = "failed"
    elif mutation == "accounting":
        row["execution"]["accounting_complete"] = False
    elif mutation == "usage":
        row["execution"]["usage"]["total"] += 1
    elif mutation == "missing":
        execution["attempts"].pop()
    elif mutation == "duplicate":
        execution["attempts"].append(copy.deepcopy(row))
    with pytest.raises(ValueError):
        m.verify_attempt_execution(cells, execution)


@pytest.mark.parametrize(
    "mutation", ["ok", "fallback", "wildcard", "model", "base", "alias", "lan"]
)
def test_router_claim_requires_retained_routing_evidence(mutation):
    router = {
        "state": "available",
        "data": {
            "ok": True,
            "lan_path_private": True,
            "fallback_fields": {
                "fallbacks": None,
                "context_window_fallbacks": None,
                "content_policy_fallbacks": None,
            },
            "wildcard_groups": [],
            "model_group_alias": {},
            "glm_deployments": [
                {"model": "openai/glm-5.3-flash", "api_base": "http://192.168.3.40:8045/v1"}
            ],
        },
    }
    assert m.verify_router(router)
    d = router["data"]
    if mutation == "ok":
        d["ok"] = False
    elif mutation == "fallback":
        d["fallback_fields"]["fallbacks"] = [{"spark/glm-5.3-flash": ["paid"]}]
    elif mutation == "wildcard":
        d["wildcard_groups"] = ["*"]
    elif mutation == "model":
        d["glm_deployments"][0]["model"] = "paid/model"
    elif mutation == "base":
        d["glm_deployments"][0]["api_base"] = "https://paid.example/v1"
    elif mutation == "alias":
        d["model_group_alias"] = {"spark/glm-5.3-flash": "paid"}
    elif mutation == "lan":
        d["lan_path_private"] = False
    assert not m.verify_router(router)
