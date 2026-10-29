"""Reproduce g5 rule A and the unchanged first-attempt comparison, offline.

Public exports bind private native/provider captures by hashes. They cannot
independently authenticate the provider, replay omitted transcripts, or establish
semantic truth. No network, model, deployment or holdout activity is needed.
"""

import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True

G4 = "2026-10-28-glm-g4-qualification"
G3 = "2026-10-25-glm-g3-qualification"
C1 = "2026-10-22-glm-capped-c1-qualification"
S2 = "2026-10-21-glm-structured-s2-qualification"
KEYS = ("input", "output", "cache_read", "cache_write", "total")
ARMS = ("aggregate", "assessed", "focused1", "focused3")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def totals(rows):
    return {k: sum(r.get("usage", {}).get(k, 0) for r in rows) for k in KEYS}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def verify_manifest(package):
    manifest = read(package / "file-sha256.json")
    actual = {
        p.relative_to(package).as_posix()
        for p in package.rglob("*")
        if p.is_file() and p.name != "file-sha256.json" and "__pycache__" not in p.parts
    }
    require(set(manifest) == actual, "public manifest file set differs")
    for name, digest in manifest.items():
        require(sha(package / name) == digest, "public hash differs: " + name)


def helper(reports):
    spec = importlib.util.spec_from_file_location(
        "immutable_g4_reproducer", reports / G4 / "reproduce.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def derive_views(cells, clean):
    result = {}
    for view in ("rule_a", "first_attempt"):
        accepted = [
            c
            for c in cells
            if c.get("status") == "complete"
            and c.get("accounting_complete") is True
            and (
                view == "rule_a"
                or (
                    c.get("attempts")
                    and c["attempts"][0]["status"] == "complete"
                    and c["attempts"][0]["accounting_complete"] is True
                )
            )
        ]
        defects = [c for c in accepted if c["run_id"].split("/")[0] not in clean]

        def valid(c):
            return c.get("findings", 0) > 0 and c.get("evidence_valid") is True

        result[view] = {
            "complete": len(accepted),
            "clean_complete": sum(c["run_id"].split("/")[0] in clean for c in accepted),
            "defect_nonempty": sum(valid(c) for c in defects),
            "aggregate": sum(
                c["run_id"].split("/")[1] == "aggregate" and valid(c) for c in defects
            ),
            "assessed": sum(
                c["run_id"].split("/")[1] == "assessed" and c.get("assessor_complete") is True
                for c in defects
            ),
        }
    return result


def verify_ledger(ledger, sessions, strict=True):
    errors = []

    def check(ok, text):
        if not ok:
            errors.append(text)

    requests = ledger.get("requests", [])
    supplements = ledger.get("supplements", [])
    ids = [r.get("id") for r in requests]
    sources = [r.get("source_sha256") for r in requests]
    check(bool(requests), "no provider requests")
    check(len(set(ids)) == len(ids), "duplicate request identity")
    check(len(set(sources)) == len(sources), "duplicate provider source")
    check(
        all(isinstance(i, str) and len(i) == 64 for i in ids + sources),
        "invalid source/request hash",
    )
    by_session = {}
    native_events = {}
    native_usage = {k: 0 for k in KEYS}
    for row in sessions:
        if row["status"] == "not_started":
            continue
        key = (
            row["cell"],
            row.get("cell_attempt", 1),
            f"sessions/{row['persona']}/{row['attempt']}",
        )
        check(key not in by_session, "duplicate session")
        by_session[key] = row
        for stream, name in [
            ("stdout.jsonl", "initial_request_output_tokens"),
            ("correction.stdout.jsonl", "correction_request_output_tokens"),
        ]:
            for index, output in enumerate(row.get(name, [])):
                native_events[(*key, stream, index)] = output
        for k, name in [
            ("input", "input_tokens"),
            ("output", "output_tokens"),
            ("total", "tokens"),
        ]:
            native_usage[k] += row[name]
    attributed = collections.defaultdict(list)
    events = set()
    used_supplements = set()
    supp_usage = {k: 0 for k in KEYS}
    paid = fallbacks = 0
    for r in requests:
        u = r.get("usage", {})
        check(
            set(u) == set(KEYS) and all(type(u.get(k)) is int and u[k] >= 0 for k in KEYS),
            "invalid disjoint usage",
        )
        check(u.get("total") == sum(u.get(k, 0) for k in KEYS[:-1]), "usage total differs")
        route = r.get("model") == "openai/glm-5.3-flash" and r.get("api_base", "").rstrip("/") in (
            "http://192.0.2.40:8045",
            "http://192.0.2.40:8045/v1",
        )
        paid += int(bool(r.get("paid_calls")) or not route)
        fallbacks += r.get("fallbacks") or 0
        check(route and r.get("paid_calls") == 0, "paid or different provider")
        check(r.get("fallbacks") == 0, "fallback or missing fallback record")
        key = (r.get("run_id"), r.get("attempt"), r.get("session"))
        check(key in by_session, "unassigned provider request")
        event = (*key, r.get("stream"), r.get("event"))
        if r.get("stream") is not None:
            check(
                event in native_events and native_events.get(event) == u.get("output"),
                "request cost event differs",
            )
            check(event not in events, "duplicate native event")
            events.add(event)
            attributed[key].append(r)
        else:
            matches = [s for s in supplements if s.get("id") == r.get("id")]
            good = (
                len(matches) == 1
                and matches[0].get("reason") == "missing_failed_request"
                and all(matches[0].get(k) == r.get(k) for k in ("run_id", "attempt", "session"))
                and isinstance(matches[0].get("session_result_sha256"), str)
                and len(matches[0]["session_result_sha256"]) == 64
                and by_session.get(key, {}).get("status") in ("failed", "partial")
            )
            check(good, "invalid failed-request supplement")
            if good:
                check(r["id"] not in used_supplements, "duplicate supplement")
                used_supplements.add(r["id"])
                for k in KEYS:
                    supp_usage[k] += u[k]
    check(events == set(native_events), "missing native cost event")
    check(len(used_supplements) == len(supplements), "unused or duplicate supplement")
    for key, row in by_session.items():
        usage = totals(attributed[key])
        check(
            usage["input"] == row["input_tokens"]
            and usage["output"] == row["output_tokens"]
            and usage["total"] == row["tokens"],
            "native session usage differs",
        )
    provider = totals(requests)
    check(
        all(provider[k] == native_usage[k] + supp_usage[k] for k in KEYS),
        "provider/native/supplement sums differ",
    )
    check(not ledger.get("attribution_failed", False), "retained attribution failure")
    if errors and strict:
        raise ValueError("; ".join(sorted(set(errors))))
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "native_usage": native_usage,
        "provider_usage": provider,
        "supplement_usage": supp_usage,
        "supplements": len(supplements),
        "requests": len(requests),
        "paid_calls": paid,
        "fallbacks": fallbacks,
        "exact_match": provider == native_usage,
        "unknown_accounting": bool(errors),
    }


def session_cause(row):
    error = row.get("nare_error")
    if error in ("model_repetition", "schema_failure", "reasoning_without_answer"):
        return error
    if row["reason"] == "budget":
        return (
            "budget_overrun_assessor"
            if row["persona"].startswith("evidence-assessor")
            else "budget_overrun_discovery"
        )
    if row["reason"] == "protocol":
        anchors = (
            row.get("correction_answer_anchors")
            if (row.get("protocol_correction") or {}).get("outcome") == "refused"
            else row.get("finding_answer_anchors")
        )
        if any(type(a.get("line")) is int and a["line"] <= 0 for a in anchors or []):
            return "invalid_finding_anchor"
        return (
            "assessment_protocol" if row["persona"].startswith("evidence-assessor") else "protocol"
        )
    return row["reason"]


def verify_pipeline_sessions(cells, sessions):
    roster = {"senior-dev", "junior-dev", "security", "devops"}
    for cell in cells:
        for attempt in cell["attempts"]:
            members = [
                r
                for r in sessions
                if r["cell"] == cell["run_id"] and r["cell_attempt"] == attempt["attempt"]
            ]
            initial = [
                r for r in members if r["persona"] in roster and r["attempt"] == "attempt-0001"
            ]
            require(
                len(initial) == 4 and {r["persona"] for r in initial} == roster,
                "required discovery participant missing",
            )
            if cell.get("selected_attempt") != attempt["attempt"]:
                continue
            require(
                all(r["status"] == "complete" for r in initial),
                "accepted attempt has incomplete discovery",
            )
            started = [r for r in members if r["status"] != "not_started"]
            require(
                all(r["status"] == "complete" for r in started), "accepted attempt has failed phase"
            )
            assessors = [r for r in members if r["persona"] == "evidence-assessor"]
            actual = any(r["status"] == "complete" and r["tokens"] > 0 for r in assessors)
            require(
                actual == cell.get("assessor_complete"),
                "accepted actual assessor differs from native records",
            )
    return True


def verify_attempt_execution(cells, execution):
    expected = {(c["run_id"], a["attempt"]): a for c in cells for a in c["attempts"]}
    rows = execution.get("attempts", [])
    keys = [(r.get("run_id"), r.get("attempt")) for r in rows]
    require(bool(expected) and len(keys) == len(set(keys)), "duplicate native attempt execution")
    require(set(keys) == set(expected), "native attempt execution set differs")
    for row, key in zip(rows, keys):
        require(row.get("state") == "available", "native attempt execution unavailable")
        actual = row.get("execution", {})
        require(
            all(
                actual.get(k) == expected[key].get(k)
                for k in ("status", "accounting_complete", "usage", "error")
            ),
            "native attempt execution differs from inspection",
        )
    return True


def policy_tests(cells, gate, experiment, ledger):
    expected = {f"{c}/{a}/{r}" for c in gate["cases"] for a in ARMS for r in range(1, 4)}
    matrix = len(cells) == 36 and len(expected) == 36 and {c["run_id"] for c in cells} == expected
    views = derive_views(cells, gate["clean_cases"])
    v = views["rule_a"]
    attempts = [a for c in cells for a in c.get("attempts", [])]

    def bounded(c):
        a = c.get("attempts", [])
        return bool(
            1 <= len(a) <= 2
            and [x.get("attempt") for x in a] == list(range(1, len(a) + 1))
            and (
                len(a) == 1
                or (
                    a[0]["status"] != "complete"
                    and a[0].get("failed_sessions", 0) > 0
                    and a[0].get("retryable") is True
                )
            )
            and (
                c.get("selected_attempt") == len(a) and a[-1]["status"] == "complete"
                if c["status"] == "complete"
                else c.get("selected_attempt") is None
            )
        )

    tests = {
        "matrix_exact": matrix,
        "experiment_terminal": experiment.get("status") == "complete",
        "all_36_complete_accounted": matrix and v["complete"] == 36,
        "all_12_clean_complete": matrix and v["clean_complete"] == 12,
        "all_24_defect_cells_nonempty_valid": v["defect_nonempty"] == 24,
        "six_aggregate_nonempty_valid": v["aggregate"] == 6,
        "six_assessed_actual_complete": v["assessed"] == 6,
        "accepted_replay_valid": matrix and all(c.get("replay_valid") is True for c in cells),
        "bounded_rule_a_selection": matrix and all(bounded(c) for c in cells),
        "every_attempt_terminal": bool(attempts)
        and all(a.get("terminal") is True for a in attempts),
        "all_attempts_reconciled": bool(attempts)
        and ledger["valid"]
        and all(a.get("ledger_reconciled") is True for a in attempts),
        "all_attempts_provenance_routing": bool(attempts)
        and all(
            a.get("provenance_valid") is True and a.get("routing_valid") is True for a in attempts
        ),
        "all_attempts_within_ceiling": bool(attempts)
        and all(
            type(a.get("tokens")) is int and 0 <= a["tokens"] <= gate["max_tokens_per_run"]
            for a in attempts
        ),
        "zero_paid_calls_and_fallbacks": bool(attempts)
        and ledger["valid"]
        and ledger["paid_calls"] == ledger["fallbacks"] == 0
        and all(a.get("paid_calls") == a.get("fallbacks") == 0 for a in attempts),
        "holdout_untouched": gate.get("holdout_used") is False,
    }
    return tests


def verify_router(receipt):
    if receipt.get("state") != "available":
        return False
    data = receipt.get("data") or {}
    fields = data.get("fallback_fields") or {}
    deployments = data.get("glm_deployments") or []
    aliases = data.get("model_group_alias") or {}
    return (
        data.get("ok") is True
        and data.get("lan_path_private") is True
        and {"fallbacks", "context_window_fallbacks", "content_policy_fallbacks"} <= set(fields)
        and all(not v for v in fields.values())
        and data.get("wildcard_groups") == []
        and len(deployments) == 1
        and deployments[0].get("model") == "openai/glm-5.3-flash"
        and deployments[0].get("api_base") == "http://192.0.2.40:8045/v1"
        and "glm" not in json.dumps(aliases).lower()
    )


def provider_summary(evidence):
    out = {}
    for name, window in evidence["windows"].items():
        if window["state"] != "available":
            out[name] = {"available": False, "known_rows": None, "unknown_usage": True}
            continue
        rows = window["rows"]
        ours = [r for r in rows if r["agent"] == "python-httpx2"]
        by = {}
        for row in rows:
            key = f"{row['agent']} | {row['key']} | {row['api_base']}"
            d = by.setdefault(key, {"rows": 0, "prompt_tokens": 0, "completion_tokens": 0})
            d["rows"] += 1
            d["prompt_tokens"] += row["prompt_tokens"]
            d["completion_tokens"] += row["completion_tokens"]
        out[name] = {
            "available": True,
            "known_rows": len(rows),
            "evaluator_requests": len(ours),
            "evaluator_usage": {
                "input": sum(r["prompt_tokens"] for r in ours),
                "output": sum(r["completion_tokens"] for r in ours),
                "total": sum(r["prompt_tokens"] + r["completion_tokens"] for r in ours),
            },
            "nominal_attribution_usd": round(sum(r["spend"] for r in ours), 6),
            "paid_calls": sum(
                r["model_group"] != "onprem/glm-5.3-flash"
                or r["model"] != "openai/glm-5.3-flash"
                or r["api_base"].rstrip("/")
                not in ("http://192.0.2.40:8045", "http://192.0.2.40:8045/v1")
                for r in ours
            ),
            "fallbacks": sum(r.get("attempted_fallbacks") or 0 for r in ours),
            "other_onprem_requests": sum(
                r["agent"] != "python-httpx2" and r["model_group"] == "onprem/glm-5.3-flash"
                for r in rows
            ),
            "other_non_onprem_requests": sum(
                r["agent"] != "python-httpx2" and r["model_group"] != "onprem/glm-5.3-flash"
                for r in rows
            ),
            "trailing_slash_onprem_rows": sum(
                r["model_group"] == "onprem/glm-5.3-flash" and r["api_base"].endswith("/")
                for r in rows
            ),
            "by_agent_key_base": by,
        }
    return out


def frozen_checks(package, reports):
    freeze = read(package / "freeze-record.json")
    post = read(package / "freeze-post.json")
    prep = read(package / "g5/preparation.json")
    gate = read(package / "g5/gate.json")
    old = read(reports / G4 / "g4/gate.json")
    prior = read(reports / G4 / "g4/preparation.json")
    require(
        sha(package / "freeze-record.json") == post["freeze_sha256"],
        "freeze record differs from post",
    )
    require(sha(package / "freeze-on-51.md") == post["body_sha256"], "posted freeze prose differs")
    for name, digest in freeze["files"].items():
        if not name.endswith(".whl"):
            require(sha(package / "g5" / name) == digest, "frozen input differs: " + name)
        else:
            require(prep["input_sha256"][name] == digest, "wheel identity differs")
        require(digest in (package / "freeze-on-51.md").read_text(), "posted hash missing")
    for name, digest in freeze["script_sha256"].items():
        require(
            sha(package / "execution-scripts" / (name + ".txt")) == digest,
            "execution script differs",
        )
    for name in gate["g4_unchanged_profile_keys"]:
        require(gate[name] == old[name], "g4 profile drift: " + name)
    require(
        (package / "g5/evaluation.json").read_bytes()
        == (reports / G4 / "g4/evaluation.json").read_bytes(),
        "g4 config bytes differ",
    )
    require(
        all(
            prep["input_sha256"][n] == d
            for n, d in prior["input_sha256"].items()
            if n.startswith("corpus/")
        ),
        "g4 corpus differs",
    )
    require(
        gate["common_predicates"] == (package / "rule-a-common-predicates.md").read_text(),
        "common predicates differ",
    )
    require(
        sha(package / "rule-a-common-predicates.md") == gate["common_predicates_sha256"],
        "common predicate hash differs",
    )
    require(
        prep["qualification_rule_a"] is True
        and prep["transient_retries_per_cell"] == prep["transient_retry_budget_job"] == 0,
        "retry policy differs",
    )
    require(
        prep["release_tag"] == "2026.10.40"
        and prep["source_commit"] == "f6162931052b4f96e24cc82f29820aaa5fce6c4f",
        "released identity differs",
    )
    require(
        gate["smoke_timeout_seconds"] == prep["timeout_seconds"] == 1800
        and gate["smoke_completion_required"] is True,
        "smoke condition differs",
    )
    require(
        gate["whole_cell_rerun"]["max_reruns_per_cell"] == 1
        and gate["schedules_allowed"].startswith("g5 only."),
        "extra schedule or recovery",
    )
    return prep, gate


def available(package, name, default):
    try:
        value = read(package / name)
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def observations(runs, clean, matcher, accepted_ids=None):
    accepted = [
        r
        for r in runs
        if r["status"] == "complete"
        and r["accounting_complete"]
        and (accepted_ids is None or r["run_id"] in accepted_ids)
    ]
    decisions = []
    detected = set()
    for run in accepted:
        for index in range(len(run["findings"])):
            defect = matcher.decide(run, index)
            if defect is not None:
                decisions.append(
                    {
                        "run_id": run["run_id"],
                        "finding_id": matcher.finding_id(run, index),
                        "defect_id": defect,
                        "method": "automated",
                    }
                )
                detected.add(run["run_id"])
    findings = sum(len(r["findings"]) for r in accepted)
    latency = [r["latency_seconds"] for r in runs if r.get("attempts")]
    return {
        "accepted_findings": findings,
        "automated_matches": len(decisions),
        "pending_for_a_person": findings - len(decisions),
        "defect_cells_automatically_matched": len(detected),
        "clean_cells_with_findings": sum(
            r["case_id"] in clean and bool(r["findings"]) for r in accepted
        ),
        "actual_human_judgments": 0,
        "decisions": decisions,
        "pipeline_latency_seconds_including_failed_attempts": (
            {
                "cells": len(latency),
                "mean": round(statistics.mean(latency), 2),
                "median": round(statistics.median(latency), 2),
                "p95_nearest_rank": round(sorted(latency)[math.ceil(0.95 * len(latency)) - 1], 2),
                "max": round(max(latency), 2),
            }
            if latency
            else None
        ),
    }


def first_attempt_tests(tests, views):
    first = views["first_attempt"]
    return {
        **tests,
        "all_originals_complete_on_first_attempt": first["complete"] == 36,
        "all_original_clean_complete": first["clean_complete"] == 12,
        "all_original_defects_nonempty_valid": first["defect_nonempty"] == 24,
        "original_aggregate_evidence": first["aggregate"] == 6,
        "original_actual_assessors": first["assessed"] == 6,
    }


def discussion_summary(cells, execution):
    expected = {
        (c["run_id"], a["attempt"])
        for c in cells
        if c["run_id"].split("/")[1] in ("focused1", "focused3")
        for a in c.get("attempts", [])
    }
    records = {(r["run_id"], r["attempt"]): r for r in execution.get("attempts", [])}
    valid = []
    for key in expected:
        row = records.get(key, {})
        trace = (row.get("execution") or {}).get("discussion_trace")
        if isinstance(trace, dict) and trace.get("availability") == "recorded":
            valid.append(trace)
    unknown = len(expected) - len(valid)
    known_rounds = sum(len(t.get("rounds") or []) for t in valid)
    return {
        "expected_focused_attempts": len(expected),
        "attempts_with_trace": len(valid),
        "unavailable_traces": unknown,
        "rounds_reported": None if unknown else known_rounds,
        "known_rounds_subtotal": known_rounds,
        "complete": unknown == 0,
        "trace_statuses": dict(collections.Counter(t.get("status", "unknown") for t in valid)),
    }


def operation_cost(native, ledger, provider, smoke, tests):
    g5 = provider.get("g5", {})
    sw = provider.get("smoke", {})
    g5_complete = bool(
        g5.get("available")
        and ledger["valid"]
        and tests.get("native_attempts_consistent", True)
        and tests.get("provider_window_ledger_consistent", True)
    )
    smoke_complete = bool(
        sw.get("available")
        and smoke.get("verified") is True
        and smoke.get("reconciled_cumulative_usage") is not None
    )
    complete = g5_complete and smoke_complete
    native_g5 = (native or {}).get("total")
    provider_g5 = ledger["provider_usage"]["total"]
    native_smoke = (smoke.get("cumulative_usage") or {}).get("total")
    provider_smoke = (smoke.get("reconciled_cumulative_usage") or {}).get("total")
    if provider_smoke is None and sw.get("available"):
        provider_smoke = sw.get("evaluator_usage", {}).get("total")
    lower = max(native_g5 or 0, provider_g5 or 0) + max(native_smoke or 0, provider_smoke or 0)
    known_nominal = round(
        sum(provider.get(n, {}).get("nominal_attribution_usd") or 0 for n in ("smoke", "g5")), 6
    )
    return {
        "complete": complete,
        "g5_complete": g5_complete,
        "smoke_complete": smoke_complete,
        "total_tokens": lower if complete else None,
        "known_tokens_lower_bound": lower,
        "native_g5_tokens": native_g5,
        "known_provider_g5_tokens": provider_g5,
        "native_smoke_tokens": native_smoke,
        "known_provider_smoke_tokens": provider_smoke,
        "nominal_attribution_usd": known_nominal if complete else None,
        "known_nominal_attribution_usd": known_nominal,
        "g5_requests": ledger["requests"] if g5_complete else None,
        "g5_paid_calls": ledger["paid_calls"] if g5_complete else None,
        "g5_fallbacks": ledger["fallbacks"] if g5_complete else None,
        "method": "Separate phase known subtotals; maximum of native/provider tokens per "
        "phase is a conservative lower bound, never their sum. Missing total or "
        "attribution remains unknown. Nominal known spend is a subtotal, not billing.",
    }


def build(package, reports):
    prior_helper = helper(reports)
    # Verify all published predecessor packages and their unchanged summaries.
    c1_helper = prior_helper.load_c1(reports)
    c1_helper.verify_hashes(reports / G4)
    baseline = prior_helper.build(reports / G4, reports)
    require(baseline == read(reports / G4 / "summary.json"), "historical g4 reproduction differs")
    prep, gate = frozen_checks(package, reports)
    experiment = available(package, "g5/experiment.json", {"status": "missing", "runs": []})
    mechanical = available(package, "g5/mechanical-qualification.json", {})
    if not isinstance(mechanical.get("cells"), list) or not isinstance(
        mechanical.get("tests"), dict
    ):
        mechanical = {
            "cells": [],
            "tests": {"retained_inspection_available": False},
            "cumulative_usage": experiment.get("cumulative_usage"),
            "qualification": "not_qualified",
        }
    cells = mechanical["cells"]
    snapshot = available(package, "g5/snapshot.json", {})
    sessions = available(package, "g5/session-diagnosis.json", {"sessions": []})["sessions"]
    started = [r for r in sessions if r["status"] != "not_started"]
    provider_ledger = available(
        package,
        "g5/provider-ledger.json",
        {"requests": [], "supplements": [], "attribution_failed": True},
    )
    ledger = verify_ledger(provider_ledger, sessions, strict=False)
    derived = policy_tests(cells, gate, experiment, ledger)
    disagreements = [
        key
        for key, value in derived.items()
        if key in mechanical["tests"] and value != mechanical["tests"][key]
    ]
    audit_errors = []

    def audit(ok, message):
        if not ok:
            audit_errors.append(message)

    try:
        audit(
            bool(snapshot) and snapshot.get("qualification", {}).get("rule") == "A",
            "snapshot lacks rule A",
        )
        old = read(reports / G4 / "g4/snapshot.json")
        audit(snapshot["configs"] == old["configs"], "prepared arm configs differ from g4")
        for case, arms in snapshot["case_variants"].items():
            for arm, facts in arms.items():
                audit(
                    facts["allocations"] == old["case_variants"][case][arm]["allocations"]
                    and facts["review_ceiling"] == 200000,
                    "prepared grants differ",
                )
    except (OSError, ValueError, KeyError, TypeError):
        audit_errors.append("prepared snapshot unavailable or inconsistent")
    # Immutable frozen-file hashes hard-refuse; damaged output evidence instead
    # remains a failed audit with known costs and unavailable facts retained.
    for cell in cells:
        for attempt in cell["attempts"]:
            members = [
                r
                for r in started
                if r["cell"] == cell["run_id"] and r["cell_attempt"] == attempt["attempt"]
            ]
            audit(
                sum(r["tokens"] for r in members) == attempt["usage"]["total"],
                "native attempt tokens differ",
            )
            audit(
                sum(r["status"] != "complete" for r in members) == attempt["failed_sessions"],
                "failed-session count differs",
            )
            bound = [
                r
                for r in provider_ledger["requests"]
                if r.get("run_id") == cell["run_id"] and r.get("attempt") == attempt["attempt"]
            ]
            audit(
                sum(r["usage"]["total"] for r in bound) == attempt["tokens"],
                "reconciled attempt tokens differ",
            )
    native = totals([{"usage": a["usage"]} for c in cells for a in c["attempts"]])
    if cells:
        audit(
            native == mechanical["cumulative_usage"] == experiment["cumulative_usage"],
            "cumulative native usage differs",
        )
    answers = available(package, "g5/first-answers.json", {"sessions": []})
    try:
        corrections = (
            prior_helper.correction_summary(sessions, answers["sessions"])
            if started and answers.get("extraction_succeeded", True)
            else {"offered": None, "unknown": True}
        )
    except (ValueError, KeyError, TypeError):
        corrections = {
            "offered": None,
            "unknown": True,
            "error": "correction evidence incomplete or inconsistent",
        }
        audit_errors.append("correction evidence incomplete or inconsistent")
    evidence = read(package / "provider-evidence.json")
    provider = provider_summary(evidence)
    try:
        pipeline_valid = verify_pipeline_sessions(cells, sessions)
        pipeline_error = None
    except ValueError as error:
        pipeline_valid = False
        pipeline_error = str(error)
    tests = {
        **mechanical["tests"],
        **derived,
        "independent_request_attribution": ledger["valid"],
        "independent_pipeline_sessions": pipeline_valid,
        "released_predicates_consistent": not disagreements,
        "native_attempts_consistent": not audit_errors,
    }
    for name in ("smoke", "g5"):
        v = provider[name]
        tests[name + "_spend_verified"] = (
            v["available"]
            and v["paid_calls"] == v["fallbacks"] == 0
            and v["evaluator_requests"] > 0
        )
    for label in ("smoke", "prelaunch", "after"):
        tests["router_" + label] = verify_router(evidence.get("routers", {}).get(label, {}))
    smoke = available(package, "smoke-verification.json", {})
    smoke_saved = available(package, "smoke-summary.json", {})
    if "cumulative_usage" not in smoke and smoke_saved.get("cumulative_usage") is not None:
        smoke["cumulative_usage"] = smoke_saved["cumulative_usage"]
        smoke["native_usage_source"] = "retained smoke-summary.json; verification unavailable"
    tests["smoke_complete_verified"] = (
        smoke.get("verified") is True
        and smoke.get("session_complete") is True
        and smoke.get("completion_waived") is False
    )
    tests["provider_window_ledger_consistent"] = bool(
        provider["g5"]["available"]
        and provider["g5"]["evaluator_usage"]["total"] == ledger["provider_usage"]["total"]
    )
    if provider["g5"]["available"] and not tests["provider_window_ledger_consistent"]:
        audit_errors.append("provider window/ledger totals differ")
    launch = (evidence.get("launch") or {}).get("data") or {}
    if launch and smoke:

        def utc(t):
            return datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))

        summary = read(package / "smoke-summary.json")
        posted = read(package / "freeze-comment-readback.json")
        require(
            posted["url"] == read(package / "freeze-post.json")["url"]
            and posted["body"].rstrip() == (package / "freeze-on-51.md").read_text().rstrip(),
            "freeze server readback differs",
        )
        require(
            utc(posted["created_utc"]) < utc(summary["started_utc"]),
            "freeze was not posted before smoke",
        )
        require(
            utc(launch["started_utc"]) > utc(summary["ended_utc"]) > utc(freeze_time(package)),
            "freeze/smoke/launch order differs",
        )
    views = derive_views(cells, gate["clean_cases"])
    failed = [r for r in started if r["status"] != "complete"]
    failure_rows = [
        {k: r[k] for k in ("cell", "cell_attempt", "persona", "status", "reason", "tokens")}
        | {"cause": session_cause(r)}
        for r in failed
    ]
    reruns = [
        {
            "cell": c["run_id"],
            "original_status": c["attempts"][0]["status"],
            "rerun_status": c["attempts"][1]["status"],
            "selected_attempt": c.get("selected_attempt"),
            "original_tokens": c["attempts"][0]["tokens"],
            "rerun_tokens": c["attempts"][1]["tokens"],
        }
        for c in cells
        if len(c["attempts"]) == 2
    ]
    loss = []
    for c in cells:
        if (
            c["status"] == "complete"
            and c["accounting_complete"]
            and c["replay_valid"]
            and (
                c["run_id"].split("/")[0] in gate["clean_cases"]
                or (c["findings"] > 0 and c["evidence_valid"])
            )
        ):
            continue
        causes = sorted({r["cause"] for r in failure_rows if r["cell"] == c["run_id"]}) or [
            "cell_" + c["status"]
        ]
        loss.append({"cell": c["run_id"], "status": c["status"], "causes": causes})
    reference = {}
    for label in ("g4", "g3", "c1", "s2"):
        j = baseline["jobs"][label]
        g = j["gate_items"]
        reference[label] = {
            "complete": g["cells_complete_and_accounted"],
            "first_attempt": g["cells_complete_and_accounted_first_attempt"],
            "aggregate": g["aggregate_defect_cells_nonempty_valid_v2"],
            "assessed": g["assessed_defect_cells_actual_complete_assessor"],
            "native_tokens": j["usage_all_attempts"]["total"],
            "historical_qualified": False,
            "rule_a_recovery": "unobserved; published verdict unchanged",
        }
    for label, directory in (("g4", G4), ("g3", G3), ("c1", C1), ("s2", S2)):
        historical = read(reports / directory / label / "mechanical-qualification.json")["cells"]
        clean = read(reports / directory / label / "gate.json")["clean_cases"]
        reference[label]["clean_complete"] = sum(
            c["run_id"].split("/")[0] in clean
            and c["status"] == "complete"
            and c["accounting_complete"]
            for c in historical
        )
        reference[label]["defect_nonempty"] = sum(
            c["run_id"].split("/")[0] not in clean
            and c["status"] == "complete"
            and c["accounting_complete"]
            and c["findings"] > 0
            and not c["finding_validation_errors"]
            for c in historical
        )
        reference[label]["mean_cell_latency_seconds"] = baseline["jobs"][label][
            "cell_latency_seconds"
        ]["mean"]
    # Retained-data common-predicate proposal reconciles the two historical missing
    # requests once; original engine accounting flags/verdicts remain unchanged.
    for label, directory, field in (
        ("s2", S2, "spend_rows_s2_window_reread"),
        ("c1", C1, "spend_rows_c1_window_reread"),
    ):
        groups = read(reports / directory / "provider-evidence.json")[field]["by_client_and_key"]
        ours = [
            value
            for key, value in groups.items()
            if key.startswith("python-httpx2 | proxy master key")
        ]
        require(len(ours) == 1, "historical evaluator attribution differs")
        reference[label]["reconciled_tokens"] = (
            ours[0]["prompt_tokens"] + ours[0]["completion_tokens"]
        )
    for label in ("g3", "g4"):
        reference[label]["reconciled_tokens"] = reference[label]["native_tokens"]
    validated = {
        c["run_id"]
        for c in cells
        if c.get("status") == "complete"
        and c.get("accounting_complete")
        and c.get("replay_valid")
        and c.get("evidence_valid")
    }
    observed = observations(
        experiment.get("runs", []), gate["clean_cases"], c1_helper, accepted_ids=validated
    )
    observed["returned_findings_not_validated_for_acceptance"] = sum(
        len(r.get("findings", []))
        for r in experiment.get("runs", [])
        if r["run_id"] not in validated
    )

    execution = available(package, "g5/attempt-execution.json", {"attempts": []})
    try:
        execution_valid = verify_attempt_execution(cells, execution)
    except ValueError as error:
        execution_valid = False
        audit_errors.append(str(error))
    tests["independent_attempt_execution"] = execution_valid
    discussion = discussion_summary(cells, execution)
    cost_by_phase = {
        name: totals([r for r in provider_ledger["requests"] if r.get("attempt") == number])
        for name, number in (("originals", 1), ("reruns", 2))
    }
    cost_by_phase["unassigned"] = totals(
        [r for r in provider_ledger["requests"] if r.get("attempt") not in (1, 2)]
    )
    first_tests = first_attempt_tests(tests, views)
    costs = operation_cost(mechanical.get("cumulative_usage"), ledger, provider, smoke, tests)
    qualified = all(tests.values())
    return {
        "schema_version": 1,
        "qualified": qualified,
        "rule": "A",
        "tests": tests,
        "views": views,
        "first_attempt_perfection_pass": all(first_tests.values()),
        "first_attempt_predicates": first_tests,
        "references": reference,
        "status_counts": dict(collections.Counter(c["status"] for c in cells)),
        "first_attempt_status_counts": dict(
            collections.Counter(c["attempts"][0]["status"] for c in cells if c["attempts"])
        ),
        "attempts": sum(len(c["attempts"]) for c in cells),
        "reruns": reruns,
        "failed_cells": loss,
        "failed_sessions": failure_rows,
        "sessions_started": len(started),
        "sessions_unstarted": len(sessions) - len(started),
        "sessions_complete": sum(r["status"] == "complete" for r in started),
        "original_failed_sessions": sum(r["cell_attempt"] == 1 for r in failed),
        "rerun_failed_sessions": sum(r["cell_attempt"] == 2 for r in failed),
        "corrections": corrections,
        "observations": observed,
        "discussion_all_attempts": discussion,
        "reconciled_usage_by_phase": cost_by_phase,
        "native_cumulative_usage": mechanical["cumulative_usage"],
        "ledger": ledger,
        "provider": provider,
        "smoke": smoke,
        "operation_cost": costs,
        "cumulative_tokens_including_smoke": costs["total_tokens"],
        "nominal_attribution_including_smoke_usd": costs["nominal_attribution_usd"],
        "evidence_audit_errors": audit_errors,
        "load_samples": len(evidence["samples_15min"]),
        "load_read_errors": sum("error" in r for r in evidence["samples_15min"]),
        "load_export_receipt": evidence.get("sample_receipt", {"state": "unknown"}),
        "holdout_used": False,
        "actual_human_judgments": 0,
        "defaults_promoted": False,
        "semantic_qualification": False,
        "released_predicate_disagreements": disagreements,
        "pipeline_session_error": pipeline_error,
        "comparison_limit": (
            "Different days/releases/concurrent load; no causal effect or semantic "
            "quality conclusion. Historical rule A recovery was not observed."
        ),
    }


def freeze_time(package):
    return read(package / "freeze-record.json")["frozen_utc"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--reports", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    package = args.package.resolve()
    reports = args.reports.resolve() if args.reports else package.parent
    if not args.write:
        verify_manifest(package)
    result = build(package, reports)
    if args.write:
        (package / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    else:
        require(result == read(package / "summary.json"), "recomputed summary differs")
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "qualified": result["qualified"],
                "first_attempt_complete": result["views"]["first_attempt"]["complete"],
                "rule_a_complete": result["views"]["rule_a"]["complete"],
                "reruns": len(result["reruns"]),
            }
        )
    )


if __name__ == "__main__":
    main()
