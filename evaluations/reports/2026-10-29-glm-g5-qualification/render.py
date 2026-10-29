"""Render the new g5 report from independently rebuilt retained-data facts."""

import json
import sys
from pathlib import Path
from string import Template

p = Path(sys.argv[1])


def r(n):
    return json.loads((p / n).read_text())


s = r("summary.json")
prep = r("g5/preparation.json")
freeze = r("freeze-record.json")
post = r("freeze-post.json")
e = r("provider-evidence.json")
smoke = r("smoke-summary.json") if (p / "smoke-summary.json").exists() else {}
views = s["views"]
v = views["rule_a"]
first = views["first_attempt"]


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        + ["| " + " | ".join(str(x).replace("|", "\\|") for x in row) + " |" for row in rows]
    )


verdict = "passed" if s["qualified"] else "did not pass"
rows = []
for title, key, den in [
    ("Complete/accounted accepted cells", "complete", 36),
    ("Clean cells complete", "clean_complete", 12),
    ("Defect cells with nonempty valid evidence", "defect_nonempty", 24),
    ("Aggregate defect evidence", "aggregate", 6),
    ("Actual complete assessed defect assessors", "assessed", 6),
]:
    rows.append(
        [title, f"{v[key]}/{den}", f"{first[key]}/{den}"]
        + [
            (
                f"{s['references'][x][key]}/{den}"
                if key in s["references"][x]
                else (
                    "12/12"
                    if key == "clean_complete"
                    else str(s["references"][x]["complete"] - 12) + "/24"
                )
            )
            for x in ("g4", "g3", "c1", "s2")
        ]
    )
rows.append(
    [
        "Published operational verdict",
        "pass" if s["qualified"] else "not qualified",
        "pass" if s["first_attempt_perfection_pass"] else "not qualified",
    ]
    + ["not qualified"] * 4
)
launch = (e.get("launch") or {}).get("data") or {}
fields = {
    "field_001": verdict,
    "field_002": v["complete"],
    "field_003": first["complete"],
    "field_004": post["url"],
    "field_005": prep["release_tag"],
    "field_006": prep["source_commit"],
    "field_007": next(k for k in freeze["files"] if k.endswith(".whl")),
    "field_008": next(v for (k, v) in freeze["files"].items() if k.endswith(".whl")),
    "field_009": launch.get("started_utc", "unknown"),
    "field_010": table(
        ["Criterion", "g5 rule A", "g5 first attempt", "g4", "g3", "c1", "s2"], rows
    ),
    "field_011": s["sessions_started"],
    "field_012": s["sessions_complete"],
    "field_013": s["sessions_unstarted"],
    "field_014": s["original_failed_sessions"],
    "field_015": s["rerun_failed_sessions"],
    "field_016": json.dumps(s["first_attempt_status_counts"], sort_keys=True),
    "field_017": json.dumps(s["status_counts"], sort_keys=True),
    "field_018": table(
        ["Common predicate / retained execution check", "Result"],
        [[k, "pass" if value else "FAIL"] for (k, value) in s["tests"].items()],
    ),
    "field_019": table(
        ["Cell", "Cell attempt", "Persona", "Terminal status/reason", "Cause", "Native tokens"],
        [
            [
                f"`{x['cell']}`",
                x["cell_attempt"],
                x["persona"],
                x["status"] + "/" + x["reason"],
                x["cause"],
                x["tokens"],
            ]
            for x in s["failed_sessions"]
        ],
    )
    if s["failed_sessions"]
    else "None.",
    "field_020": len(s["reruns"]),
    "field_021": s["attempts"],
    "field_022": table(
        ["Cell", "Original", "Rerun", "Selected attempt", "Original/rerun reconciled tokens"],
        [
            [
                f"`{x['cell']}`",
                x["original_status"],
                x["rerun_status"],
                x["selected_attempt"],
                f"{x['original_tokens']} / {x['rerun_tokens']}",
            ]
            for x in s["reruns"]
        ],
    )
    if s["reruns"]
    else "None. This run did not exercise live whole-cell recovery.",
    "field_023": table(
        ["Cell", "Final status", "Causes"],
        [[f"`{x['cell']}`", x["status"], ", ".join(x["causes"])] for x in s["failed_cells"]],
    )
    if s["failed_cells"]
    else (
        "None recorded. The complete gate table above remains authoritative, including "
        "any schedule-level accounting failure."
    ),
    "field_024": s["corrections"].get("offered"),
    "field_025": json.dumps(s["corrections"].get("sessions_by_type", {}), sort_keys=True),
    "field_026": json.dumps(s["corrections"].get("by_outcome", {}), sort_keys=True),
    "field_027": json.dumps(
        s["corrections"].get("sessions_by_type_and_outcome", {}), sort_keys=True
    ),
    "field_028": s["corrections"].get("tokens", "unknown"),
    "field_029": s["corrections"].get("corrected_sessions_complete", "unknown"),
    "field_030": s["corrections"].get("answered_sessions", "unknown"),
    "field_031": s["corrections"].get("first_answers_refused", "unknown"),
    "field_032": " No correction fired, so its live effect remains unmeasured."
    if s["corrections"].get("offered") == 0
    else "",
    "field_033": json.dumps(s["native_cumulative_usage"], sort_keys=True),
    "field_034": json.dumps(s["ledger"]["provider_usage"], sort_keys=True),
    "field_035": json.dumps(s["ledger"]["supplement_usage"], sort_keys=True),
    "field_036": s["ledger"]["supplements"],
    "field_037": s["ledger"]["valid"],
    "field_038": json.dumps(s["ledger"]["errors"]),
    "field_039": table(
        ["Run", "Native tokens, all attempts", "Reconciled tokens, all attempts"],
        [
            [
                "g5",
                s["native_cumulative_usage"]["total"]
                if s["native_cumulative_usage"]
                else "unknown",
                s["ledger"]["provider_usage"]["total"]
                if s["operation_cost"]["g5_complete"]
                else "unknown (known provider subtotal "
                + str(s["ledger"]["provider_usage"]["total"])
                + ")",
            ]
        ]
        + [
            [x, s["references"][x]["native_tokens"], s["references"][x]["reconciled_tokens"]]
            for x in ("g4", "g3", "c1", "s2")
        ],
    ),
    "field_040": s["operation_cost"]["g5_requests"],
    "field_041": s["operation_cost"]["g5_paid_calls"],
    "field_042": s["operation_cost"]["g5_fallbacks"],
    "field_043": "unknown"
    if s["nominal_attribution_including_smoke_usd"] is None
    else format(s["nominal_attribution_including_smoke_usd"], ".6f"),
    "field_044": "unknown"
    if s["cumulative_tokens_including_smoke"] is None
    else format(s["cumulative_tokens_including_smoke"], ","),
    "field_045": smoke.get("started_utc", "unknown"),
    "field_046": smoke.get("ended_utc", "unknown"),
    "field_047": s["smoke"].get("completed_cells"),
    "field_048": s["smoke"].get("smoke_attempts"),
    "field_049": json.dumps(s["smoke"].get("reconciled_cumulative_usage"), sort_keys=True),
    "field_050": table(
        [
            "Window",
            "All logged rows",
            "Evaluator requests",
            "Other Spark requests",
            "Other non-Spark requests",
            "Nominal evaluator attribution",
        ],
        [
            [
                name,
                d.get("known_rows"),
                d.get("evaluator_requests"),
                d.get("other_spark_requests"),
                d.get("other_non_spark_requests"),
                d.get("nominal_attribution_usd"),
            ]
            for (name, d) in s["provider"].items()
        ],
    ),
    "field_051": table(
        ["Qualification client / key / base", "Requests", "Prompt tokens", "Completion tokens"],
        [
            [key, x["rows"], x["prompt_tokens"], x["completion_tokens"]]
            for (key, x) in sorted(s["provider"]["g5"].get("by_agent_key_base", {}).items())
        ],
    ),
    "field_052": s["load_samples"],
    "field_053": s["load_read_errors"],
}
o = s["observations"]
latency_text = json.dumps(o["pipeline_latency_seconds_including_failed_attempts"], sort_keys=True)
discussion_text = json.dumps(s["discussion_all_attempts"], sort_keys=True)
fields["observations"] = (
    f"Accepted source observations: {o['accepted_findings']}; automated matches under the "
    f"unchanged prospective c1/Sol keyword rules: {o['automated_matches']}; pending for a "
    f"person: {o['pending_for_a_person']}. Clean cells with findings: "
    f"{o['clean_cells_with_findings']}. These are observations across repeats, not unique "
    "or human-confirmed defects; unmatched findings are pending, not scored false. "
    f"Zero human judgments. Pipeline latency per logical cell, including failed attempt "
    f"time but excluding queue wait: `{latency_text}`. "
    f"Discussion across retained attempts: `{discussion_text}`. "
    "A missing trace is unknown and is never successful zero-round execution."
)
fields["cost_by_phase"] = json.dumps(s["reconciled_usage_by_phase"], sort_keys=True)
cost = s["operation_cost"]
covered = (
    cost["complete"]
    and s["tests"].get("zero_paid_calls_and_fallbacks") is True
    and all(s["tests"].get("router_" + label) is True for label in ("smoke", "prelaunch", "after"))
)
fields["spend_statement"] = (
    "Only spark/glm-5.3-flash through LAN LiteLLM to192.168.3.40:8045 was authorized. "
    "Trailing-slash Spark URLs, including python-httpx, are normalized as self-hosted. "
    + (
        f"Router and spend records cover smoke and every qualification attempt. "
        f"Evaluator requests: {cost['g5_requests']}; paid calls: {cost['g5_paid_calls']}; "
        f"attempted fallbacks: {cost['g5_fallbacks']}. External spend: US$0. "
        if covered
        else "Complete routing/spend coverage is unverified; total paid calls, "
        "fallbacks and external spend are unknown. No zero-spend claim is made. "
    )
    + f"Known retained g5 provider rows: {s['ledger']['requests']}; known paid rows: "
    f"{s['ledger']['paid_calls']}; known fallback count: {s['ledger']['fallbacks']}. "
    f"Nominal operation attribution: {fields['field_043']}; known nominal subtotal: "
    f"US${cost['known_nominal_attribution_usd']:.6f} (not billing). Complete reconciled "
    f"operation tokens including smoke: {fields['field_044']}; conservative known token "
    f"lower bound: {cost['known_tokens_lower_bound']:,}. Native/provider counters overlap "
    "and are not added together. No failed spend is erased by selection or token reset."
)
fields["smoke_statement"] = (
    f"One released-wheel smoke job: {fields['field_045']}–{fields['field_046']}, with "
    "1800-second sessions matching g5. The released evaluator fixes three repeats, so "
    "the one job uses three senior-only security cells,50000 grants, and actual bounded "
    "A mode. It remains outside the36qualification cells. "
    + (
        "Completion, active prepared line counts/corrections, line-bound prompt, rule A "
        "snapshot/policy, provenance and accepted replay verified. No waiver/second smoke. "
        if s["tests"].get("smoke_complete_verified") and cost["smoke_complete"]
        else "Smoke completion/provenance/accounting verification is incomplete or unavailable. "
    )
    + f"Recorded completed cells: {fields['field_047']}; attempts: {fields['field_048']}; "
    f"known native usage: {json.dumps(s['smoke'].get('cumulative_usage'))}; "
    f"known reconciled usage: {fields['field_049']}."
)
fields["load_receipt"] = json.dumps(s["load_export_receipt"], sort_keys=True)
fields["observations"] += (
    " All observations lack human adjudication; the unmatched count is only the subset "
    "not matched automatically."
)
text = Template(Path(__file__).with_name("README.template.md").read_text()).substitute(fields)
(p / "README.md").write_text(text)
print(json.dumps({"rendered": str(p / "README.md"), "qualified": s["qualified"]}))
