"""Score recorded model evidence and retained failures; no provider calls or promotion."""

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

from scrutare.evaluation.scoring import finding_id, score_experiment

parser = argparse.ArgumentParser()
parser.add_argument("--experiment", type=Path, default=Path(__file__).resolve().parent)
parser.add_argument("--corpus", type=Path, required=True)
parser.add_argument("--output", type=Path)
parser.add_argument("--diagnoses", type=Path)
args = parser.parse_args()
P = args.experiment.resolve()
OUTPUT = (args.output or P).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
CORPUS = args.corpus.resolve()
experiment = json.loads((P / "experiment.json").read_text())
snapshot = json.loads((P / "snapshot.json").read_text())
runs = experiment["runs"]
labels = {
    c["id"]: json.loads((CORPUS / "labels" / (c["id"] + ".json")).read_text())
    for c in snapshot["cases"]
}
decisions = json.loads((P / "automated-decisions.json").read_text())
pending = json.loads((P / "pending-human-adjudication.json").read_text())["items"]
matches = {d["finding_id"]: d["defect_id"] for d in decisions}
domains = {c["id"]: c["domain"] for c in snapshot["cases"]}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


for case in snapshot["cases"]:
    assert sha(CORPUS / "labels" / (case["id"] + ".json")) == case["labels_sha256"]
    for name, expected in case["capture_sha256"].items():
        assert sha(CORPUS / "captures" / case["id"] / name) == expected
assert len(runs) == len(snapshot["expected_runs"]) == 240
assert {r["run_id"] for r in runs} == {r["run_id"] for r in snapshot["expected_runs"]}
assert len(decisions) + len(pending) == sum(len(r["findings"]) for r in runs) == 0


def compact(metrics):
    result = {}
    for variant, original in metrics.items():
        value = dict(original)
        latency = sorted(value.pop("latency_seconds"))
        value["p95_latency_seconds_all_scheduled_runs"] = latency[
            math.ceil(0.95 * len(latency)) - 1
        ]
        value["scorer_quality_final"] = value["quality_final"]
        value["quality_final"] = False
        value["observed_tokens_are_lower_bound"] = bool(value["accounting_failures"])
        rate = value["clean_pr_false_positive_rate"]
        value["clean_false_positive_runs"] = (
            round(rate * value["eligible_clean_runs"]) if rate is not None else None
        )
        value["complete_run_fraction"] = value["complete_runs"] / len(latency)
        value["human_judgments"] = 0
        result[variant] = value
    return result


def score_subset(selected):
    identities = {finding_id(r, i) for r in selected for i in range(len(r["findings"]))}
    ds = [d for d in decisions if d["finding_id"] in identities]
    expected = [
        r for r in snapshot["expected_runs"] if r["run_id"] in {run["run_id"] for run in selected}
    ]
    return compact(score_experiment(selected, labels, ds, expected_runs=expected))


metrics = compact(
    score_experiment(runs, labels, decisions, expected_runs=snapshot["expected_runs"])
)
production = json.loads((P / "automated-metrics.json").read_text())["metrics"]
assert {v: production[v]["true_positive_defects"] for v in production} == {
    v: metrics[v]["true_positive_defects"] for v in metrics
}
repeat_metrics = {str(n): score_subset([r for r in runs if r["repeat"] == n]) for n in (1, 2, 3)}
domain_metrics = {
    domain: score_subset([r for r in runs if domains[r["case_id"]] == domain])
    for domain in sorted(set(domains.values()))
}
case_metrics = {
    case: score_subset([r for r in runs if r["case_id"] == case]) for case in sorted(domains)
}


def detected(run):
    if run["status"] not in ("complete", "partial") or not run["accounting_complete"]:
        return False
    return any(matches.get(finding_id(run, i)) is not None for i in range(len(run["findings"])))


pairings = [("aggregate", "assessed"), ("assessed", "focused1"), ("focused1", "focused3")]
comparisons = {}
for left, right in pairings:
    a = {(r["case_id"], r["repeat"]): r for r in runs if r["variant"] == left}
    b = {(r["case_id"], r["repeat"]): r for r in runs if r["variant"] == right}
    assert set(a) == set(b) and len(a) == 60
    changes = collections.Counter()
    cells = []
    for key in sorted(a):
        (first, second) = (a[key], b[key])
        bug = bool(labels[key[0]]["defects"])
        (x, y) = (detected(first), detected(second))
        if bug:
            changes[
                "both_detected"
                if x and y
                else "only_baseline_detected"
                if x
                else "only_candidate_detected"
                if y
                else "neither_detected"
            ] += 1
        cells.append(
            {
                "case_id": key[0],
                "domain": domains[key[0]],
                "repeat": key[1],
                "has_gold_defect": bug,
                "baseline_detected": x,
                "candidate_detected": y,
                "baseline_status": first["status"],
                "candidate_status": second["status"],
            }
        )

    def delta(field):
        (x, y) = (metrics[left][field], metrics[right][field])
        return None if x is None or y is None else y - x

    comparisons[left + "_to_" + right] = {
        "baseline": left,
        "candidate": right,
        "paired_case_repeat_cells": 60,
        "defect_opportunities": 39,
        "paired_detection_counts": dict(changes),
        "precision_delta": delta("finding_precision"),
        "recall_delta": delta("defect_recall"),
        "complete_fraction_delta": delta("complete_run_fraction"),
        "observed_token_ratio": metrics[right]["tokens"] / metrics[left]["tokens"],
        "p95_latency_ratio": metrics[right]["p95_latency_seconds_all_scheduled_runs"]
        / metrics[left]["p95_latency_seconds_all_scheduled_runs"],
        "cells": cells,
        "inference": "provisional_lower_bound_descriptive_paired_counts",
    }
diagnoses = json.loads((args.diagnoses or P / "normalized-diagnostics.json").read_text())
assert diagnoses["schema_version"] == 1
assert len(diagnoses["noncomplete_runs"]) == 237
assert sum(len(r["findings"]) for r in runs) == 0
rounds = diagnoses["round_trace_availability"]
assert rounds["focused1"]["traces_available"] == rounds["focused3"]["traces_available"] == 60
assert rounds["focused1"]["round_count_distribution"] == {"0": 60}
assert rounds["focused3"]["round_count_distribution"] == {"0": 60}
assert all(value["native_assessor_invocations"] == 0 for value in rounds.values())
limits = [
    "The fixed gpt-4.1-nano alias, shared40000-token ceiling,max6turns"
    + ",120s timeout and concurrency3 match all four arms. Backend dated"
    + " revision remains unverified.",
    "Twenty original development cases contain13 documented defects an"
    + "d7 guarded clean controls; repeats give39 opportunities per arm, "
    + "not39 independent defects.",
    "All240 records and failed/invalid outcomes remain. No valid sourc"
    + "e observations survive, so automated judgments and pending human "
    + "judgments are both0. This is not evidence that reviewed changes w"
    + "ere clean.",
    "All120 focused traces exist but contain0 actual discussion rounds"
    + ".119 stop at discovery_incomplete;1 is undisputed with no finding"
    + "s. The configured1-vs3 round bound was never exercised.",
    "No native independent-assessor invocation occurred in any arm. Re"
    + "served quotas and empty no-candidate assessment artifacts do not "
    + "demonstrate a model assessment.",
    "Failure mechanisms are normalized from retained private sessions:"
    + " schema violations, malformed revision/hash protocol, checked-cit"
    + "ation mismatch and token budgets. Native error/schema_violation d"
    + "oes not imply service unavailable.",
    "Every v2 session remains below the max6turns bound; most stop aft"
    + "er2–3 turns. No timeout or turn-limit mechanism is asserted witho"
    + "ut a recorded stop.",
    "Token totals with incomplete accounting are lower bounds. Missing"
    + " valid verdicts count against recall and never count as clean app"
    + "roval.",
    "No semantic winner, benefit from more discussion, or supported-mi"
    + "nority retention quality can be measured when neither assessment "
    + "nor discussion executed.",
    "Quality metrics reproduce from public experiment/snapshot/decisio"
    + "ns/labels; normalized diagnostics can be inspected without native"
    + " sessions but are unsigned retained summaries, not authenticated "
    + "proofs of model behavior.",
    "Original executable controls validate source triggers and specifi"
    + "c clean invariants, not universal absence of other defects. No hu"
    + "man judgments are invented.",
    "This used only the original development corpus. The fresh40-case "
    + "holdout remains locally model-unseen; no holdout quality evidence"
    + " or default promotion is claimed.",
    "Concurrent proxy load confounds latency. These data characterize "
    + "reliability of this candidate/rail/schedule, not all multi-agent "
    + "review architectures.",
]
report = {
    "schema_version": 1,
    "status": "provisional_execution_inconclusive",
    "quality_final": False,
    "human_judgments": 0,
    "source_observations": 0,
    "automated_judgments": 0,
    "pending_human_judgments": 0,
    "scheduled_runs": 240,
    "missing_runs": 0,
    "development_cases": 20,
    "distinct_gold_defects": 13,
    "clean_controls": 7,
    "evaluation_status_counts": dict(collections.Counter(r["status"] for r in runs)),
    "evaluation_status_by_variant": {
        v: dict(collections.Counter(r["status"] for r in runs if r["variant"] == v))
        for v in metrics
    },
    "status_interpretation": "The runner marks nonfailed engine outcomes without a valid verdic"
    + "t invalid; the production scorer counts those recorded evaluation"
    + " statuses. Native session status, engine status and evaluation el"
    + "igibility remain separate.",
    "metrics": metrics,
    "repeat_metrics": repeat_metrics,
    "domain_metrics": domain_metrics,
    "case_metrics": case_metrics,
    "paired_components": comparisons,
    "diagnoses": diagnoses,
    "limitations": limits,
    "promotion": "not_evaluated_no_unseen_quality_evidence",
    "source_provenance": json.loads((P / "source-provenance.json").read_text()),
    "artifacts_sha256": {
        name: sha(P / name)
        for name in (
            "experiment.json",
            "snapshot.json",
            "blinded-packet.json",
            "automated-decisions.json",
            "pending-human-adjudication.json",
            "automated-metrics.json",
            "normalized-diagnostics.json",
            "report-focused.py",
            "adjudicate-focused.py",
            "adjudication-provenance.json",
            "source-provenance.json",
        )
    },
}
dump("automated-focused-report.json", report)


def pct(value):
    return "n/a" if value is None else f"{value:.1%}"


lines = [
    "# Provisional focused-protocol execution comparison",
    "",
    "240 actual scheduled runs over20 development cases,13 documented "
    + "defects and7 guarded clean controls. **No valid source findings, "
    + "no independent-assessor invocation and no discussion round execut"
    + "ed. Zero automated judgments and zero human judgments. Defaults u"
    + "nchanged.**",
    "",
    "| Arm | Complete / failed / invalid | Detected /39 | Precision | "
    + "Observed tokens | p95 seconds | Clean FP / eligible |",
    "|---|---:|---:|---:|---:|---:|---:|",
]
for arm, m in metrics.items():
    lines.append(
        f"| {arm} | {m['complete_runs']} / {m['failed_runs']} / {m['invalid_runs']} | "
        f"{m['true_positive_defects']} /39 | {pct(m['finding_precision'])} | "
        f"{m['tokens']:,}{(' ≥' if m['observed_tokens_are_lower_bound'] else '')} | "
        f"{m['p95_latency_seconds_all_scheduled_runs']:.2f} | "
        f"{m['clean_false_positive_runs']} / {m['eligible_clean_runs']} |"
    )
lines += [
    "",
    "Aggregate and assessed each have1 complete empty result; focused1"
    + " has1 and focused3 has0.178 runs are failed and59 invalid. The as"
    + "sessed arm’s59 engine partial/discovery_incomplete outcomes are r"
    + "ecorded as invalid because no valid verdict exists. Production sc"
    + "oring preserves these eligibility classifications.",
    "",
    "## Actual round availability",
    "",
    "| Arm | Configured bound | Applicable traces available | Actual c"
    + "ompleted rounds | Assessor invocations |",
    "|---|---:|---:|---:|---:|",
]
for arm, a in rounds.items():
    bound = snapshot["configs"][arm]["rounds"]["max"] if a["applicable"] else "disabled (default3)"
    lines.append(
        f"| {arm} | {bound} | "
        f"{(a['traces_available'] if a['applicable'] else 'not applicable')} | "
        f"{('0 in all60 runs' if a['applicable'] else 'not applicable')} | "
        f"{a['native_assessor_invocations']} |"
    )
lines += [
    "",
    "The one-round/multiple-round limits were never exercised. All120 "
    + "focused debate traces are available:119 stop at incomplete discov"
    + "ery and1 is undisputed with an empty candidate set. Round-level n"
    + "ew evidence, disposition changes, round tokens and round latency "
    + "are therefore unavailable—not zero-cost completed discussions. Ne"
    + "ither a quality winner nor a benefit from extra rounds can be inf"
    + "erred.",
    "",
    "## Concrete reliability evidence",
    "",
]
combined = collections.Counter()
citations = collections.Counter()
protocol = collections.Counter()
for value in diagnoses["failure_analysis"].values():
    combined.update(value["native_stop_reasons"])
    citations.update(value["citation_validation_codes"])
    protocol.update(value["fixed_protocol_claim_diagnostics"])
lines += [
    "- Native/session stop counts: " + json.dumps(dict(combined), sort_keys=True),
    "- Checked-citation refusal counts: " + json.dumps(dict(citations), sort_keys=True),
    "- Fixed protocol claim diagnostics: " + json.dumps(dict(protocol), sort_keys=True),
    "",
    "These are mechanical/protocol diagnostics under the fixed rail. T"
    + "hey do not establish that claims were semantically false, that th"
    + "e provider was unavailable, or that the implementation has an ide"
    + "ntified defect. Detailed JSON preserves actual quotas, rosters, p"
    + "rompt hashes, turn counts, malformed-hash shapes and every noncom"
    + "plete outcome.",
    "",
    "## Limits",
    "",
] + ["- " + value for value in limits]
lines += [
    "",
    "## Public reproduction",
    "",
    "Run the frozen candidate evaluator’s production score CLI against"
    + " experiment.json, the original pilot corpus and automated-decisio"
    + "ns.json. Then run `python report-focused.py --experiment REPORT_D"
    + "IRECTORY --corpus PILOT_DIRECTORY` using the pinned evaluator pac"
    + "kage. The script reads normalized-diagnostics.json and never open"
    + "s native run directories or .agents state. Quality/domain/repeat/"
    + "paired metrics reproduce independently from public records; diagn"
    + "osis summaries are imported as retained unsigned evidence. Origin"
    + "al private sessions remain retained for audit.",
    "",
]
(OUTPUT / "automated-focused-report.md").write_text("\n".join(lines))
print(
    json.dumps(
        {
            "metrics": metrics,
            "actual_round_trace_counts": {
                v: {
                    "available": a["traces_available"],
                    "rounds": a["round_count_distribution"],
                    "assessor_calls": a["native_assessor_invocations"],
                }
                for (v, a) in rounds.items()
            },
        },
        indent=2,
    )
)
