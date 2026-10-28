"""Read-only recovery after an abnormal evaluator exit; never restart any model work.

Use --state PRIVATE_STATE --output REPORT/recovered-exit.json --collect after the
evaluator exits. This exports a failed/missing-cell inventory and collects the final
spend/router observations even when no complete experiment.json exists. All writes
go to new evidence/report files; frozen inputs and scripts remain untouched.
"""

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def inventory(job):
    gate = read(job / "gate.json")
    expected = {
        f"{case}/{arm}/{repeat}"
        for case in gate["cases"]
        for arm in gate["arms"]
        for repeat in range(1, gate["repeats"] + 1)
    }
    runs = []
    path = job / "experiment/runs.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue  # An interrupted final line is covered by the private file hash.
            if row["run_id"] not in expected:
                raise ValueError("unexpected retained run identity")
            runs.append(
                {
                    "run_id": row["run_id"],
                    "status": row["status"]
                    if row["status"] in ("complete", "failed", "partial", "missing", "error")
                    else "error",
                    "tokens": row["usage"]["total"],
                }
            )
    paths = [p for p in (job / "experiment").rglob("*") if p.is_file()]
    return {
        "qualified": False,
        "recovery_only": True,
        "expected_cells": len(expected),
        "missing_cells": sorted(expected - {r["run_id"] for r in runs}),
        "retained_runs": runs,
        "artifact_sha256": {
            p.relative_to(job).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    state = args.state.resolve()
    job, evidence = state / "g4", state / "evidence"
    status = subprocess.run(
        ["systemctl", "--user", "is-active", "-q", "scrutare-glm-g4.service"], check=False
    )
    if status.returncode == 0:
        raise ValueError("refuse recovery while evaluator is active")
    freeze = read(state / "freeze-g4.json")
    for name, digest in freeze["files"].items():
        if hashlib.sha256((job / name).read_bytes()).hexdigest() != digest:
            raise ValueError("frozen file changed")
    result = inventory(job)
    result["observed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if args.collect:
        start = read(job / "process.json")["started_utc"].replace("T", " ").removesuffix("Z")
        end = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        commands = [
            ["collect-window.py", "g4", start, end],
            [
                "private-call.py",
                "spend-verify.py",
                start,
                end,
                str(evidence / "g4-spend-check.json"),
            ],
            ["private-call.py", "fallback-check.py", str(evidence / "fallback-check-after.json")],
        ]
        for name, *arguments in commands:
            subprocess.run([sys.executable, str(state / name), *arguments], check=True)
        result["spend_summary"] = read(evidence / "g4-spend-summary.json")
        result["router_ok"] = read(evidence / "fallback-check-after.json")["ok"]
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "qualified": False,
                "missing_cells": len(result["missing_cells"]),
                "retained_runs": len(result["retained_runs"]),
            }
        )
    )


if __name__ == "__main__":
    main()
