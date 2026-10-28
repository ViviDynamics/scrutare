"""Reproduce g4's frozen gate, corrections, causes, spend and load offline.

Uses only the standard library and the immutable c1/s2 sibling report packages.
Private capture and spend-read hashes bind the exported claims; public artifacts
cannot authenticate the original provider responses or proxy observations.
"""

import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

C1 = "2026-10-22-glm-capped-c1-qualification"
S2 = "2026-10-21-glm-structured-s2-qualification"
POSTED = {
    "preparation.json": "9b0188593205552e5c9a589512480269f889da8096eb18bac34c246339b8945e",
    "gate.json": "302812fb4c70d60376c2bee97c168a387ea64061cc6803de537af807590b23b0",
    "evaluation.json": "06e54db3b393b55722b5b6d577d18514fd8c709ff9b5ba4c688e479086dd1edf",
    "scrutare-2026.10.38-py3-none-any.whl": (
        "43634957026d0e3d1ec049f7a8f6cabebf986a7191b60e793408769b890a2e6b"
    ),
}
POSTED_FREEZE = "c8832d2834bcb09f1e16c31cf5a1bee52ac0a94ab4b3791d1e0770ec5f7c0374"
G3 = "2026-10-25-glm-g3-qualification"
ROSTER = {"senior-dev", "junior-dev", "security", "devops"}
POSTED_AMENDMENT = "2b573b08565c607a227df0adf65cdc60a7d7256b3c83174f57b3fe2cf60f068b"
AMENDMENT_URL = "https://github.com/ViviDynamics/scrutare/issues/51#issuecomment-6473582253"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_c1(reports):
    spec = importlib.util.spec_from_file_location(
        "immutable_c1_report", reports / C1 / "reproduce.py"
    )
    if not spec or not spec.loader:
        raise ValueError("cannot load the c1 report verifier")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.verify_hashes(reports / C1)
    helper.verify_hashes(reports / S2)
    return helper


def correction_summary(rows, answers):
    """Count full-answer corrections across all phases; type counts are session incidences."""
    by_answer = {a["session"]: a for a in answers}
    if len(by_answer) != len(answers):
        raise ValueError("duplicate first-answer identity")
    outcomes, phases, types, occurrences = (collections.Counter() for _ in range(4))
    by_type = collections.defaultdict(collections.Counter)
    tokens = answered = refused = corrected_complete = 0
    offered_rows = []
    eligible = [r for r in rows if r.get("protocol_correction") is not None]
    for row in eligible:
        prefix = row.get("cell_attempt_prefix", row["cell"])
        key = f"{prefix}/sessions/{row['persona']}/{row['attempt']}"
        first = by_answer.get(key)
        record = row["protocol_correction"]
        if first is None or (first["status"], first["reason"]) != (row["status"], row["reason"]):
            raise ValueError("first-answer classification missing or disagrees")
        phases[first["kind"]] += 1
        answer, defects = first.get("first_answer"), first.get("correctable")
        is_refused = answer not in (None, "valid")
        answered += answer is not None
        refused += is_refused
        if record["offered"] != (is_refused and defects is not None):
            raise ValueError("correction offer disagrees with full-answer eligibility")
        if record["offered"]:
            request = row.get("protocol_correction_request")
            outcome = record["outcome"]
            if (
                outcome not in ("corrected", "refused", "no_answer", "failed")
                or not request
                or request["defects"] != defects
                or len(defects) != record["defects"]
                or len(row["correction_request_output_tokens"]) > 1
                or row["status"] == "complete"
                and outcome != "corrected"
            ):
                raise ValueError("correction outcome/defects/turn bound disagrees")
            before = row["first_run_tokens"]
            extra = row["tokens"] - before if before is not None else -1
            if extra < 0 or outcome == "corrected" and extra == 0:
                raise ValueError("correction accounting does not include its turn")
            outcomes[outcome] += 1
            tokens += extra
            corrected_complete += outcome == "corrected" and row["status"] == "complete"
            unique_types = {d["type"] for d in defects}
            for defect in defects:
                occurrences[defect["type"]] += 1
            for kind in unique_types:
                types[kind] += 1
                by_type[kind][outcome] += 1
            offered_rows.append(
                {
                    "session": key,
                    "phase": first["kind"],
                    "outcome": outcome,
                    "status": row["status"],
                    "tokens": extra,
                    "defects": defects,
                }
            )
        elif (
            record != {"offered": False, "defects": 0, "outcome": None, "exit_code": None}
            or row["status"] == "complete"
            and is_refused
        ):
            raise ValueError("correction record missing for refused complete session")
    if len(answers) != len(eligible):
        raise ValueError("first-answer entries outside protocol sessions")
    return {
        "sessions": len(eligible),
        "by_phase": dict(phases),
        "answered_sessions": answered,
        "first_answers_refused": refused,
        "offered": sum(outcomes.values()),
        "by_outcome": dict(outcomes),
        "sessions_by_type": dict(types),
        "sessions_by_type_and_outcome": {k: dict(v) for k, v in by_type.items()},
        "defect_occurrences_by_type": dict(occurrences),
        "corrected_sessions_complete": corrected_complete,
        "tokens": tokens,
        "offered_sessions": offered_rows,
    }


def verify_serving_rows(rows):
    for row in rows:
        if (
            row["model_group"] != "spark/glm-5.3-flash"
            or row["model"] != "openai/glm-5.3-flash"
            or row["api_base"].rstrip("/") != "http://192.168.3.40:8045/v1"
            or row["attempted_fallbacks"] not in (None, 0)
        ):
            raise ValueError("serving evidence includes a different deployment or fallback")


def provider_summary(evidence):
    result = {}
    for label in ("smoke", "g4", "prelaunch"):
        window = evidence["windows"][label]
        rows = window["rows"]
        ours = [r for r in rows if r["agent"] == "python-httpx2"]
        if not ours:
            raise ValueError("serving evidence has no evaluator rows")
        verify_serving_rows(ours)
        by = {}
        for row in rows:
            group = f"{row['agent']} | {row['key']} | {row['api_base']}"
            part = by.setdefault(group, {"rows": 0, "prompt_tokens": 0, "completion_tokens": 0})
            part["rows"] += 1
            part["prompt_tokens"] += row["prompt_tokens"]
            part["completion_tokens"] += row["completion_tokens"]
        spark = [r for r in rows if r["api_base"].rstrip("/") == "http://192.168.3.40:8045/v1"]
        result[label] = {
            "window_utc": window["window_utc"],
            "rows_all_keys": len(rows),
            "evaluator_rows": len(ours),
            "evaluator_prompt_tokens": sum(r["prompt_tokens"] for r in ours),
            "evaluator_completion_tokens": sum(r["completion_tokens"] for r in ours),
            "evaluator_nominal_attribution_usd": round(sum(r["spend"] for r in ours), 6),
            "external_spend_usd": 0,
            "evaluator_paid_provider_rows": 0,
            "evaluator_attempted_fallbacks_nonzero": 0,
            "all_keys_attempted_fallbacks_nonzero": sum(
                bool(r["attempted_fallbacks"]) for r in rows
            ),
            "other_spark_rows": sum(r["agent"] != "python-httpx2" for r in spark),
            "base_url_with_trailing_slash_rows": sum(r["api_base"].endswith("/") for r in spark),
            "by_agent_key_base": by,
            "evaluator_failed_rows": [r for r in ours if r["status"] != "success"],
        }
    for label in ("router_before_smoke", "router_prelaunch", "router_after"):
        router = evidence[label]
        if (
            "model_group_alias" not in router
            or "glm" in json.dumps(router["model_group_alias"] or {}).lower()
            or any(router["fallback_fields"].values())
            or router["wildcard_groups"]
            or len(router["glm_deployments"]) != 1
        ):
            raise ValueError("serving router evidence does not exclude fallbacks")
        (deployment,) = router["glm_deployments"]
        verify_serving_rows(
            [{"model_group": "spark/glm-5.3-flash", **deployment, "attempted_fallbacks": 0}]
        )
    return result


def frozen_checks(package, reports):
    directory = package / "g4"
    if sha(package / "freeze-record.json") != POSTED_FREEZE:
        raise ValueError("freeze record differs from the hash posted on #51")
    freeze = read(package / "freeze-record.json")
    prep = read(directory / "preparation.json")
    for name, digest in POSTED.items():
        actual = prep["input_sha256"][name] if name.endswith(".whl") else sha(directory / name)
        if actual != digest or freeze["files"][name] != digest:
            raise ValueError("frozen artifact differs from the hash posted on #51")
    for name in ("gate.json", "evaluation.json"):
        if prep["input_sha256"][name] != sha(directory / name):
            raise ValueError("frozen preparation disagrees with its inputs")
    c1 = reports / C1 / "c1"
    gate, old = read(directory / "gate.json"), read(c1 / "gate.json")
    if gate["c1_gate_sha256"] != sha(c1 / "gate.json"):
        raise ValueError("frozen c1 predecessor hash disagrees")
    for key in gate["unchanged_criteria_keys"]:
        if json.dumps(gate[key]).encode() != json.dumps(old[key]).encode():
            raise ValueError("gate criteria/pass rule differ from c1")
    expected = read(c1 / "evaluation.json")
    expected["context"]["line_counts"] = True
    expected["findings"] = {
        "evidence": "v2",
        "citation_correction": True,
        "protocol_correction": True,
    }
    evaluation = read(directory / "evaluation.json")
    if evaluation != expected:
        raise ValueError("configuration differs from c1 beyond the three frozen opt-ins")
    prior = reports / G3 / "g3"
    if gate["g3_gate_sha256"] != sha(prior / "gate.json"):
        raise ValueError("g3 predecessor hash disagrees")
    previous_gate = read(prior / "gate.json")
    for key in gate["unchanged_criteria_keys"]:
        if json.dumps(gate[key]).encode() != json.dumps(previous_gate[key]).encode():
            raise ValueError("g4 criteria/pass rule differ from g3")
    old_prep = read(c1 / "preparation.json")
    for key, value in old_prep["input_sha256"].items():
        if key.startswith("corpus/") and prep["input_sha256"].get(key) != value:
            raise ValueError("frozen corpus input differs from c1")
    if (
        prep["release_tag"] != gate["release_tag"]
        or prep["source_commit"] != gate["source_commit"]
        or prep["release_tag"] != freeze["release_tag"]
        or not prep["context_line_counts"]
        or not prep["citation_correction"]
        or not prep["protocol_correction"]
        or prep["holdout_used"]
    ):
        raise ValueError("preparation differs from the frozen release and settings")
    snap = read(directory / "snapshot.json")
    smoke_rail = read(package / "smoke-summary.json")["config"]["models"]["default"]
    for arm in gate["arms"]:
        config = snap["configs"][arm]
        if (
            not config["context"]["line_counts"]
            or not config["findings"]["citation_correction"]
            or not config["findings"]["protocol_correction"]
            or config["models"]["default"] != smoke_rail
        ):
            raise ValueError("runtime snapshot differs from frozen opt-ins/rail")
    original = read(directory / "original-artifact-sha256.json")
    mechanical = read(directory / "mechanical-qualification.json")
    if (
        mechanical["gate_sha256"] != sha(directory / "gate.json")
        or mechanical["experiment_sha256"] != original["experiment/experiment.json"]
    ):
        raise ValueError("mechanical summary is not bound to the frozen gate/experiment")
    return gate


def session_cause(row, cells, helper):
    errors = (row.get("nare_error"), row.get("correction_error"))
    if "model_repetition" in errors:
        return "model_repetition"
    if "schema_failure" in errors:
        return "schema_failure"
    if "reasoning_without_answer" in errors:
        return "nare_error_reasoning_without_answer"
    if row["reason"] == "protocol":
        correction_refused = (row.get("protocol_correction") or {}).get("outcome") == "refused"
        anchors = (
            row.get("correction_answer_anchors")
            if correction_refused
            else row.get("finding_answer_anchors")
        ) or []
        if any(type(a.get("line")) is int and a["line"] <= 0 for a in anchors):
            return "invalid_finding_anchor"
        identifiers = (
            row.get("correction_assessment_candidate_ids")
            if correction_refused
            else row.get("assessment_answer_candidate_ids")
        )
        if row["persona"] == "evidence-assessor" and identifiers is not None:
            expected = {c["candidate_id"] for c in cells[row["cell"]]["candidates"]}
            if set(identifiers) != expected or len(identifiers) != len(set(identifiers)):
                return "assessment_candidate_identity"
    return helper.cause(row)


def sessions_summary(directory, helper):
    cells = read(directory / "mechanical-qualification.json")["cells"]
    rows = read(directory / "session-diagnosis.json")["sessions"]
    unique = {(r["cell"], r.get("cell_attempt", 1), r["persona"], r["attempt"]) for r in rows}
    if len(unique) != len(rows):
        raise ValueError("duplicated session diagnosis identity")
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[row["cell"], row.get("cell_attempt", 1)].append(row)
        cap = row["max_output_tokens"]
        if (
            cap["limit"] != 20000
            or cap["reached"] != (row["reason"] == "output_cap")
            or row["structured_output_mode"] != "json_schema"
            or sum(row["request_output_tokens"]) != row["output_tokens"]
            or max(row["request_output_tokens"], default=0) > 20000
        ):
            raise ValueError("session diagnosis disagrees with the frozen cap or request usage")
    expected_groups = {(c["run_id"], a["attempt"]) for c in cells for a in c["attempts"]}
    if set(grouped) != expected_groups:
        raise ValueError("session diagnosis differs from retained cell attempts")
    for cell in cells:
        for attempt in cell["attempts"]:
            members = grouped[cell["run_id"], attempt["attempt"]]
            if sum(r["tokens"] for r in members) != attempt["tokens"]:
                raise ValueError("session diagnosis usage differs from the cell attempt")
        final = grouped[cell["run_id"], len(cell["attempts"])]
        discovery = sorted(
            (r["persona"], r["status"], r["reason"], r["tokens"])
            for r in final
            if r["persona"] in ROSTER and r["attempt"] == "attempt-0001"
        )
        retained = sorted(
            (r["persona"], r["status"], r["reason"], r["tokens"]) for r in cell["session_statuses"]
        )
        assessors = [
            r
            for r in final
            if r["persona"] == "evidence-assessor" and r["attempt"] == "attempt-0001"
        ]
        if (
            discovery != retained
            or len(assessors) != int(cell["assessor_tokens"] is not None)
            or cell["assessor_actual"] != any(r["tokens"] > 0 for r in assessors)
            or any(
                (r["reason"], r["tokens"], r["status"] == "complete")
                != (cell["assessor_reason"], cell["assessor_tokens"], cell["assessor_complete"])
                for r in assessors
            )
        ):
            raise ValueError("session diagnosis disagrees with the mechanical cell")
    by_cell = {c["run_id"]: c for c in cells}

    def cause(row):
        return session_cause(row, by_cell, helper)

    failed = [r for r in rows if r["status"] != "complete"]
    corrections = correction_summary(rows, read(directory / "first-answers.json")["sessions"])
    return {
        "sessions": len(rows),
        "complete": len(rows) - len(failed),
        "not_complete_by_cause": dict(collections.Counter(cause(r) for r in failed)),
        "failed_sessions": [
            {
                "cell": r["cell"],
                "cell_attempt": r.get("cell_attempt", 1),
                "persona": r["persona"],
                "cause": cause(r),
                "reason": r["reason"],
            }
            for r in failed
        ],
        "requests": sum(len(r["request_output_tokens"]) for r in rows),
        "constrained_turns_initial": sum(r["constrained_turns"] or 0 for r in rows),
        "requests_at_cap": sum(t == 20000 for r in rows for t in r["request_output_tokens"]),
        "corrections": corrections,
    }


def gate_losses(cells, gate, failed_sessions):
    bugs = set(gate["cases"]) - set(gate["clean_cases"])
    result = []
    for cell in cells:
        case, arm, _ = cell["run_id"].split("/")
        reasons = sorted({r["cause"] for r in failed_sessions if r["cell"] == cell["run_id"]})
        if cell["status"] != "complete" and not reasons:
            reasons.append("cell_" + cell["status"])
        if not cell["accounting_complete"]:
            reasons.append("accounting_incomplete")
        if len(cell["attempts"]) > 1:
            reasons.append("needed_transient_rerun")
        if any(a["tokens"] > gate["max_tokens_per_run"] for a in cell["attempts"]):
            reasons.append("attempt_over_ceiling")
        if (
            case in bugs
            and arm == "aggregate"
            and (cell["findings"] == 0 or cell["finding_validation_errors"])
        ):
            reasons.append("aggregate_empty_or_invalid_evidence")
        if (
            case in bugs
            and arm == "assessed"
            and (cell["assessment_status"] != "complete" or not cell["assessor_complete"])
        ):
            reasons.append("assessor_not_run_or_incomplete")
        if reasons:
            result.append({"cell": cell["run_id"], "causes": reasons})
    return result


def reconcile_usage(usage, sessions, provider):
    differences = {
        "request_difference": provider["evaluator_rows"] - sessions["requests"],
        "input_difference": provider["evaluator_prompt_tokens"] - usage["input"],
        "output_difference": provider["evaluator_completion_tokens"] - usage["output"],
    }
    exact = all(value == 0 for value in differences.values())
    if not provider["evaluator_failed_rows"] and not sessions["failed_sessions"] and not exact:
        raise ValueError(
            "serving usage differs from native requests/tokens "
            "without provider or native session failures"
        )
    return {
        **differences,
        "exact_match": exact,
        "native_failed_sessions": sessions["failed_sessions"],
        "cause_review_required": not exact,
        "provider_failed_rows": len(provider["evaluator_failed_rows"]),
    }


def smoke_summary(package, provider, launch_utc):
    """Verify the completion waiver without reclassifying the failed smoke as complete."""
    amendment = read(package / "g4-amendment.json")
    post = read(package / "amendment-post.json")
    smoke = read(package / "smoke-summary.json")
    diagnosis = read(package / "smoke-accounting-diagnosis.json")
    activation = read(package / "smoke-activation-verification.json")
    def utc(value):
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if (
        sha(package / "g4-amendment.json") != POSTED_AMENDMENT
        or post["amendment_sha256"] != POSTED_AMENDMENT
        or post["url"] != AMENDMENT_URL
        or utc(post["posted_utc"]) >= utc(launch_utc)
        or sha(package / "amendment-on-51.md") != post["body_sha256"]
        or POSTED_AMENDMENT not in (package / "amendment-on-51.md").read_text()
        or sha(package / "launch-g4-amended.py.txt") != amendment["launcher_sha256"]
        or amendment["freeze_sha256"] != POSTED_FREEZE
        or amendment["waived_check"] != "smoke session completion only"
        or amendment["frozen_files_changed"] is not False
        or diagnosis != amendment["smoke_evidence"]
    ):
        raise ValueError("smoke waiver differs from posted pre-launch amendment")
    if (
        (smoke["status"], smoke["session_status"], smoke["reason"])
        != ("failed", "failed", "timeout")
        or smoke["usage"] != diagnosis["native_usage"]
        or not all(
            activation.get(k) is True
            for k in (
                "activation_verified",
                "completion_waived",
                "prepared_line_counts",
                "prepared_citation_correction",
                "prepared_protocol_correction",
                "line_bound_prompt_in_invocation",
                "runtime_correction_policy_recorded",
                "provenance_verified",
            )
        )
        or activation["session_complete"] is not False
        or activation["accounting_complete"] is not False
        or not diagnosis["provenance_hashes_verified"]
    ):
        raise ValueError("smoke activation or timeout evidence disagrees")
    inflight = diagnosis["unreported_inflight_request"]
    if (
        provider["evaluator_rows"] != diagnosis["proxy_requests"]
        or provider["evaluator_prompt_tokens"] != diagnosis["proxy_prompt_tokens"]
        or provider["evaluator_completion_tokens"] != diagnosis["proxy_completion_tokens"]
        or provider["evaluator_prompt_tokens"] - smoke["usage"]["input"]
        != inflight["prompt_tokens"]
        or provider["evaluator_completion_tokens"] - smoke["usage"]["output"]
        != inflight["completion_tokens"]
        or not 0 < inflight["completion_tokens"] < smoke["max_output_tokens"]["limit"]
    ):
        raise ValueError("smoke in-flight request does not explain usage difference")
    return {
        "session_status": "failed",
        "reason": "timeout",
        "completion_waived": True,
        "activation_verified": True,
        "amendment_url": post["url"],
        "native_tokens": smoke["usage"]["total"],
        "proxy_tokens": provider["evaluator_prompt_tokens"]
        + provider["evaluator_completion_tokens"],
        "inflight_tokens": inflight["prompt_tokens"] + inflight["completion_tokens"],
    }


def build(package, reports):
    helper = load_c1(reports)
    frozen_checks(package, reports)
    jobs, causes = {}, {}
    for label, directory in (
        ("g4", package / "g4"),
        ("c1", reports / C1 / "c1"),
        ("s2", reports / S2 / "s2"),
    ):
        _, jobs[label] = helper.summarize(directory, first_attempt_rule=True)
        if label != "g4":
            causes[label] = helper.diagnosis(
                directory / "session-diagnosis.json",
                read(directory / "mechanical-qualification.json")["cells"],
                read(directory / "evaluation.json"),
            )
    prior_spec = importlib.util.spec_from_file_location(
        "immutable_g3_report", reports / G3 / "reproduce.py"
    )
    if not prior_spec or not prior_spec.loader:
        raise ValueError("cannot load g3 report verifier")
    prior_helper = importlib.util.module_from_spec(prior_spec)
    prior_spec.loader.exec_module(prior_helper)
    helper.verify_hashes(reports / G3)
    prior_summary = prior_helper.build(reports / G3, reports)
    if prior_summary != read(reports / G3 / "summary.json"):
        raise ValueError("g3 predecessor reproduction differs")
    jobs["g3"] = prior_summary["jobs"]["g3"]
    causes["g3"] = prior_summary["session_causes"]["g3"]
    causes["g4"] = sessions_summary(package / "g4", helper)
    evidence = read(package / "provider-evidence.json")
    provider = provider_summary(evidence)
    smoke = smoke_summary(package, provider["smoke"], evidence["launch"]["started_utc"])
    return {
        "schema_version": 1,
        "qualified": jobs["g4"]["all_gate_tests_pass"],
        "defaults_promoted": False,
        "actual_human_judgments": 0,
        "holdout_used": False,
        "jobs": jobs,
        "session_causes": causes,
        "provider": provider,
        "smoke": smoke,
        "spend_reconciliation": reconcile_usage(
            jobs["g4"]["usage_all_attempts"], causes["g4"], provider["g4"]
        ),
        "gate_losses": gate_losses(
            read(package / "g4/mechanical-qualification.json")["cells"],
            read(package / "g4/gate.json"),
            causes["g4"]["failed_sessions"],
        ),
        "side_by_side": {k: jobs[k]["gate_items"] for k in ("g4", "g3", "c1", "s2")},
        "comparison_limit": "Different days/releases and concurrent load; "
        "not a paired effect estimate.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--reports", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    package = args.package.resolve()
    reports = args.reports.resolve() if args.reports else package.parent
    helper = load_c1(reports)
    if not args.write:
        helper.verify_hashes(package)
    summary = build(package, reports)
    if args.write:
        (package / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    elif summary != read(package / "summary.json"):
        raise ValueError("recomputed summary differs from the published one")
    args.output.write_text(json.dumps(summary, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "qualified": summary["qualified"],
                "corrections": summary["session_causes"]["g4"]["corrections"]["offered"],
            }
        )
    )


if __name__ == "__main__":
    main()
