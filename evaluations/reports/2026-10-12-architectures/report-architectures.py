"Score recorded model evidence and retained failures; no provider calls or promotion."

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

from scrutare.evaluation.scoring import finding_id, score_experiment

P = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--corpus", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
CORPUS = args.corpus.resolve()
OUTPUT = args.output.resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)
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
assert len(runs) == len(snapshot["expected_runs"]) == 420
assert {r["run_id"] for r in runs} == {r["run_id"] for r in snapshot["expected_runs"]}
assert len(decisions) + len(pending) == sum(len(r["findings"]) for r in runs) == 706


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


pairings = [
    ("baseline", "context"),
    ("context", "procedures"),
    ("procedures", "evidence"),
    ("evidence", "assessment"),
    ("assessment", "tools"),
    ("evidence", "routing"),
]
comparisons = {}
for left, right in pairings:
    a = {(r["case_id"], r["repeat"]): r for r in runs if r["variant"] == left}
    b = {(r["case_id"], r["repeat"]): r for r in runs if r["variant"] == right}
    assert set(a) == set(b) and len(a) == 60
    changes = collections.Counter()
    cells = []
    for key in sorted(a):
        first, second = a[key], b[key]
        bug = bool(labels[key[0]]["defects"])
        x, y = detected(first), detected(second)
        if bug:
            changes[
                (
                    "both_detected"
                    if x and y
                    else "only_baseline_detected"
                    if x
                    else "only_candidate_detected"
                    if y
                    else "neither_detected"
                )
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
        x, y = metrics[left][field], metrics[right][field]
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

# Retained normalized diagnostic counts do not authenticate private native sessions.
retained = json.loads((P / "automated-architecture-report.json").read_text())
failure_details = retained["noncomplete_runs"]
per_variant_failure = retained["failure_analysis"]
actual_rosters = retained["actual_activated_rosters"]
allocations = retained["actual_allocations"]
prompt_hashes = retained["actual_prompt_hashes"]
tool_records = retained["tool_capture_summaries"]

limits = [
    (
        "All judgments are automated;54 genuine ambiguities remain actual-"
        "human pending. Zero human judgments."
    ),
    (
        "One gpt-4.1-nano alias through LiteLLM; the dated backend revisio"
        "n is not verified. Results do not generalize across model rails."
    ),
    (
        "Twenty original development cases have13 distinct defects and7 cl"
        "ean controls. Three repeats give39 opportunities per arm, not39 i"
        "ndependent defects; repeat ranges are descriptive, not confidence"
        " intervals."
    ),
    (
        "The original pilot holdout was already exposed during baseline wo"
        "rk. The new40-case holdout was not used here and remains locally "
        "model-unseen. This development experiment cannot justify promotio"
        "n."
    ),
    (
        "All failures and invalid verdicts remain in recall denominators. "
        "Zero observations from an invalid/no-verdict run do not mean clea"
        "n approval; token totals with incomplete accounting are lower bou"
        "nds."
    ),
    (
        "Native error/schema_violation is recorded under Scrutare provider"
        " refusal; it does not demonstrate provider unavailability. Malfor"
        "med hashes and checked citation mismatches are protocol failures,"
        " not evidence that allegations are semantically false."
    ),
    (
        "No v2 source findings survive into valid verdicts. Evidence compl"
        "eted3/60,assessment1/60,tools0/60,routing1/60; quality comparison"
        "s of those arms are dominated by execution reliability, not prove"
        "n semantic gains."
    ),
    (
        "Every routing run used the same original four personas; generic o"
        "riginal pilot paths never activated a specialist. The routing arm"
        " measures routing artifacts/focus under unchanged roster, not spe"
        "cialist benefit."
    ),
    (
        "All tools-arm capture statuses/diagnostics are reported; caller-s"
        "upplied unsigned Ruff results do not prove absence of other defec"
        "ts."
    ),
    (
        "Praise, safe refactor descriptions and unsupported style/security"
        " allegations are retained as false source observations rather tha"
        "n discarded. Precision includes corroborating source observations"
        "; recall deduplicates gold defects per run."
    ),
    (
        "Only complete accounted fully judged clean runs enter clean-FP de"
        "nominators. Near-zero v2 clean-FP rates based on1–2 eligible runs"
        " are not a quality win."
    ),
    (
        "Concurrent schedules and uncontrolled proxy load confound latency"
        ". Native max-turns6 was not reached by the recorded v2 sessions; "
        "most stop after2–3 turns at schema, protocol, citation or token l"
        "imits."
    ),
    (
        "Independent executable controls validate concrete gold triggers a"
        "nd tested clean invariants, not universal absence of additional d"
        "efects. Potential unlabeled scenarios remain human-pending."
    ),
    "No defaults changed; no architecture was promoted.",
]
report = {
    "schema_version": 1,
    "status": "provisional_automated_human_pending",
    "human_judgments": 0,
    "quality_final": False,
    "promotion": "not_evaluated_no_unseen_evidence",
    "model_evidence": True,
    "scheduled_runs": 420,
    "missing_runs": 0,
    "development_cases": 20,
    "distinct_gold_defects": 13,
    "clean_controls": 7,
    "evaluation_status_counts": dict(collections.Counter(r["status"] for r in runs)),
    "evaluation_status_by_variant": {
        v: dict(collections.Counter(r["status"] for r in runs if r["variant"] == v))
        for v in metrics
    },
    "status_interpretation": "The experiment runner records native engine/panel status, "
    "then tags any nonfailed engine result with no valid verdict as invalid. "
    "score_experiment counts these exact recorded evaluation statuses; it does not "
    "reclassify them. Engine partial:discovery_incomplete and native session statuses "
    "are separately retained in failure_analysis/noncomplete_runs. Invalid is not "
    "clean, complete, or successful semantic adjudication.",
    "source_observations": 706,
    "automated_judgments": len(decisions),
    "pending_human_judgments": len(pending),
    "metrics": metrics,
    "repeat_metrics": repeat_metrics,
    "domain_metrics": domain_metrics,
    "case_metrics": case_metrics,
    "paired_components": comparisons,
    "failure_analysis": per_variant_failure,
    "noncomplete_runs": failure_details,
    "actual_activated_rosters": actual_rosters,
    "actual_allocations": allocations,
    "actual_prompt_hashes": prompt_hashes,
    "tool_capture_summaries": tool_records,
    "source_provenance": json.loads((P / "run-provenance.json").read_text()),
    "limitations": limits,
}
names = [
    "experiment.json",
    "snapshot.json",
    "blinded-packet.json",
    "automated-decisions.json",
    "pending-human-adjudication.json",
    "automated-metrics.json",
    "reviewed-case-dispositions.json",
    "adjudication-provenance.json",
    "adjudicate-architectures.py",
    "report-architectures.py",
]
report["artifacts_sha256"] = {name: sha(P / name) for name in names}
dump("automated-architecture-report.json", report)


def percent(value):
    return "n/a" if value is None else f"{value:.1%}"


lines = [
    "# Provisional automated architecture comparison",
    "",
    "420 actual runs,20 development cases,13 defects,7 guarded clean controls. "
    + ("652 explicit automated judgments:294 matched observations and358 false observations; ")
    + "54 await actual human judgment. **Zero human judgments; defaults unchanged.**",
    "",
    (
        "| Arm | Complete / failed / invalid | TP / FP | Precision | Detec"
        "ted /39 | Recall | Pending | Observed tokens | p95 seconds | Clea"
        "n FP / eligible |"
    ),
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for arm, m in metrics.items():
    lines.append(
        f"| {arm} | {m['complete_runs']} / {m['failed_runs']} / {m['invalid_runs']} | "
        f"{m['true_positive_findings']} / {m['false_positive_findings']} | "
        f"{percent(m['finding_precision'])} | {m['true_positive_defects']} /39 | "
        f"{percent(m['defect_recall'])} | {m['pending_findings']} | {m['tokens']:,}"
        f"{' ≥' if m['observed_tokens_are_lower_bound'] else ''} | "
        f"{m['p95_latency_seconds_all_scheduled_runs']:.2f} | "
        f"{m['clean_false_positive_runs']} / {m['eligible_clean_runs']} |"
    )
lines += [
    "",
    "Context and revised procedures completed reliably here, but do not show an "
    "accuracy gain over the paired baseline. Context changes precision46.9%→45.0% and "
    "recall84.6%→76.9%; adding v1 procedures changes these to43.1% and79.5%. "
    + ("Precision is provisional and may move in either direction after pending judgments; ")
    + "recall is currently a lower bound where judgments remain pending. These are "
    "descriptive observations, not significance claims.",
    "",
    ("The structured arms produce no valid surviving source findings. Their measured0/39 ")
    + ("recall reflects refused/incomplete runs, not clean approvals or proof that context, ")
    + "assessment or tools reduce code-review quality under other rails.",
    "",
    "Recorded evaluation statuses are185 complete,116 failed,119 invalid. The "
    "runner tags a nonfailed engine outcome with no valid verdict as invalid; "
    "for example, assessment has59 engine partial/discovery_incomplete results "
    "recorded as59 invalid evaluation runs. Production scoring counts these exact "
    "recorded statuses. JSON separately preserves engine and native-session statuses, "
    "so invalid runs are never represented as clean approvals.",
    "",
    "## Concrete reliability diagnosis",
    "",
    "- 402 v2 sessions stop at native schema_violation. These are classified under "
    "Scrutare provider refusal, but do not demonstrate unavailable service.",
    ("- 187 native-complete v2 sessions fail protocol.189 individual claims fail the fixed ")
    + "complete revision/hash check;186 inspected citations have65-character hashes "
    + ("where64 are required. Additional hashes are truncated, nonhex or other wrong lengths."),
    ("- 52 checked-citation refusals:45 content_hash_mismatch,4 line_range_outside_capture, ")
    + ("2 revision_mismatch and1 path_not_captured. Mechanical validity is distinct from truth."),
    ("- 40 sessions stop under token budgets. No v2 session reaches the six-turn ceiling; ")
    + "most complete or fail after2–3 turns. No timeout evidence was recorded.",
    "- Assessment/tools discovery is incomplete in59/60 and60/60 runs respectively. "
    "The independent assessor cannot repair candidates that failed discovery admission.",
    "- All60 routed rosters remain senior-dev,junior-dev,security,devops; this corpus "
    "does not measure newly activated specialists.",
    "",
    "## Paired component comparisons",
    "",
    "All comparisons pair the exact same case/repeat cells. Detailed JSON contains "
    "each paired detection cell, domain/repeat metrics, source TP/FP, duplicate rates, "
    "unique detections, actual quotas, prompt hashes and tool statuses.",
    "",
    "| Transition | Precision delta | Recall delta | Observed token ratio | p95 ratio |",
    "|---|---:|---:|---:|---:|",
]
for name, c in comparisons.items():
    lines.append(
        f"| {name} | {percent(c['precision_delta'])} | {percent(c['recall_delta'])} | "
        f"{c['observed_token_ratio']:.2f} | {c['p95_latency_ratio']:.2f} |"
    )
lines += [
    "",
    "## Repeat variation",
    "",
    ("Each repeat has13 defect opportunities per arm. These repeats share the same cases ")
    + "and are not independent datasets or statistical confidence intervals.",
]
for arm in ("baseline", "context", "procedures"):
    values = [repeat_metrics[str(n)][arm] for n in (1, 2, 3)]
    lines.append(
        "- "
        + arm
        + ": recall "
        + ", ".join(percent(v["defect_recall"]) for v in values)
        + "; precision "
        + ", ".join(percent(v["finding_precision"]) for v in values)
        + "."
    )
lines += ["", "## Limits", ""] + ["- " + value for value in limits]
lines += [
    "",
    "## Reproduction",
    "",
    (
        "Use the reconstructed recorded source or current compatible evalu"
        "ator. Production `scrutare.evaluation adjudicate` "
    )
    + "exports the exact blinded packet. `adjudicate-architectures.py` checks packet, "
    "disposition, source and executable-control hashes before reproducing recorded "
    "automated decisions. Production `scrutare.evaluation score` checks frozen labels "
    "and schedule. Quality tables reproduce from the published experiment/snapshot, "
    "decisions and original pilot labels without private native sessions. The public "
    "`report-architectures.py` reconstructs paired/domain/repeat quality summaries "
    "and imports retained normalized diagnoses; published normalized diagnosis JSON "
    + ("is sufficient to inspect counts but cannot authenticate underlying native captures. ")
    + "Neither operation calls providers or reruns jobs.",
    "",
    "Raw native sessions remain private. The pinned wheel, source patch/archive, "
    "experiment/snapshot and public report artifact hashes retain provenance. "
    "These files contain no credential values.",
    "",
]
(OUTPUT / "automated-architecture-report.md").write_text("\n".join(lines))
print(
    json.dumps(
        {"metrics": metrics, "pending_human": len(pending), "failure_runs": len(failure_details)},
        indent=2,
    )
)
