"""New publication path: atomic package export, with explicit missing/read failures.

Never changes any frozen or measured input. Not part of the execution freeze;
its source and tests are included in the public publication manifest.
"""

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

H = Path(__file__).resolve().parent.parent
NARE = "/home/user/Workspace/ViviDynamics/scrutare/.agents/state/nare-2026.10.12/.venv/bin/nare"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def scrub(value, job):
    if isinstance(value, dict):
        return {k: scrub(v, job) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, job) for v in value]
    if isinstance(value, str):
        value = value.replace(NARE, "nare@2026.10.12")
        root = str(job / "experiment")
        if value == root:
            return "."
        value = value.replace(root + "/", "").replace(str(job.parent), "<private state>")
        return re.sub(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", "<redacted-email>", value)
    return value


def dump(p, value, secret=None):
    text = json.dumps(value, sort_keys=True, indent=1) + "\n"
    if secret:
        assert secret not in text and hashlib.sha256(secret.encode()).hexdigest() not in text
    p.write_text(text)


def optional(p, kind=dict):
    if p.is_symlink():
        return {"state": "unsafe_link"}
    try:
        value = read(p)
        if not isinstance(value, kind):
            return {"state": "wrong_shape", "sha256_private": sha(p)}
        return {"state": "available", "sha256_private": sha(p), "data": value}
    except FileNotFoundError:
        return {"state": "missing"}
    except (OSError, ValueError) as e:
        return {"state": "unreadable", "error": type(e).__name__}


def export_job(job, out, secret):
    out.mkdir(parents=True)
    receipts = {}
    names = {
        "preparation.json": "preparation.json",
        "gate.json": "gate.json",
        "evaluation.json": "evaluation.json",
        "experiment/experiment.json": "experiment.json",
        "experiment/snapshot.json": "snapshot.json",
        "corpus/manifest.json": "corpus-manifest.json",
        "provider-ledger.json": "provider-ledger.json",
        "mechanical-qualification.json": "mechanical-qualification.json",
    }
    for source, name in names.items():
        receipt = optional(job / source)
        receipts[source] = {k: v for k, v in receipt.items() if k != "data"}
        if receipt["state"] != "available":
            continue
        if source in ("preparation.json", "gate.json", "evaluation.json"):
            # Frozen bytes match their prospective posted hashes; preparation contains
            # its disclosed pinned nare console path. This is an immutable exception.
            assert secret not in (job / source).read_text()
            shutil.copy2(job / source, out / name)
        else:
            dump(out / name, scrub(receipt["data"], job), secret)
    dump(
        out / "export-receipt.json",
        {"artifacts": receipts, "missing_artifact_is_not_success": True},
        secret,
    )
    attempts = []
    experiment = optional(job / "experiment/experiment.json")
    if experiment["state"] == "available":
        for row in experiment["data"].get("runs", []):
            for saved in row.get("attempts", []):
                directory = Path(saved["run_dir"])
                if not directory.resolve().is_relative_to((job / "experiment").resolve()):
                    attempts.append(
                        {
                            "run_id": row["run_id"],
                            "attempt": saved["attempt"],
                            "state": "unsafe_path",
                        }
                    )
                    continue
                value = optional(directory / "evaluation.json")
                data = value.get("data", {})
                attempts.append(
                    {
                        "run_id": row["run_id"],
                        "attempt": saved["attempt"],
                        **{k: v for k, v in value.items() if k != "data"},
                        "execution": {
                            k: data[k]
                            for k in (
                                "status",
                                "error",
                                "accounting_complete",
                                "usage",
                                "latency_seconds",
                                "discussion_trace",
                            )
                            if k in data
                        },
                    }
                )
    dump(out / "attempt-execution.json", scrub({"attempts": attempts}, job), secret)
    dump(
        out / "original-artifact-sha256.json",
        {k: v["sha256_private"] for k, v in receipts.items() if v["state"] == "available"},
        secret,
    )


def provider_evidence(h, secret):
    e = h / "evidence"
    aliases = {}
    agents = {}

    def key(alias):
        return (
            alias
            if alias in ("proxy master key", "client-a", "client-b")
            else aliases.setdefault(alias, f"other key {len(aliases) + 1}")
        )

    def agent(a):
        return (
            a
            if a in ("python-httpx2", "python-httpx", "opencode", "claude-code", "none")
            else agents.setdefault(a, f"other client {len(agents) + 1}")
        )

    windows = {}
    for name in ("smoke", "prelaunch", "g5"):
        raw = optional(e / f"{name}-rows.json", list)
        summary = optional(e / f"{name}-spend-summary.json")
        rows = []
        if raw["state"] == "available" and any(not isinstance(r, dict) for r in raw["data"]):
            raw = {"state": "wrong_shape", "sha256_private": raw.get("sha256_private")}
        if raw["state"] == "available":
            for r in raw["data"]:
                md = r.get("metadata") or {}
                alias = (
                    "proxy master key"
                    if md.get("user_api_key") == "litellm_proxy_master_key"
                    or r.get("api_key") == "litellm_proxy_master_key"
                    else md.get("user_api_key_alias") or "unnamed other key"
                )
                rows.append(
                    {
                        "start_utc": r.get("startTime"),
                        "end_utc": r.get("endTime"),
                        "agent": agent((md.get("user_agent") or "none").split("/")[0]),
                        "key": key(alias),
                        "model_group": r.get("model_group"),
                        "model": r.get("model"),
                        "api_base": r.get("api_base") or "",
                        "status": r.get("status"),
                        "prompt_tokens": r.get("prompt_tokens") or 0,
                        "completion_tokens": r.get("completion_tokens") or 0,
                        "spend": r.get("spend") or 0,
                        "attempted_fallbacks": md.get("attempted_fallbacks"),
                        "attempted_retries": md.get("attempted_retries"),
                    }
                )
        windows[name] = {
            "state": raw["state"],
            "raw_rows_sha256_private": raw.get("sha256_private"),
            "summary_state": summary["state"],
            "summary": summary.get("data"),
            "rows": rows,
        }
    samples = []
    sample_errors = []
    p = e / "load-g5-samples.jsonl"
    sample_receipt = {"state": "missing", "errors": sample_errors}
    if p.exists():
        sample_receipt.update(state="available", sha256_private=sha(p))
        try:
            lines = p.read_bytes().splitlines()
        except OSError as error:
            lines = []
            sample_receipt.update(state="unreadable", error=type(error).__name__)
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if not isinstance(r, dict):
                    raise ValueError("sample is not a mapping")
                groups = r.get("by_agent_key_group_base") or {}
                if not isinstance(groups, dict):
                    raise ValueError("groups are not a mapping")
                by = {}
                for index, (group_key, v) in enumerate(groups.items()):
                    parts = group_key.split(" | ")
                    if len(parts) != 4 or not isinstance(v, dict):
                        sample_errors.append(
                            {"line": number, "group": index, "error": "malformed grouping"}
                        )
                        continue
                    a, alias, group, base = parts
                    by[f"{agent(a)} | {key(alias)} | {group} | {base}"] = v
                samples.append(
                    {
                        **{k: v for k, v in r.items() if k != "by_agent_key_group_base"},
                        "by_agent_key_group_base": by,
                    }
                )
            except (ValueError, TypeError, AttributeError) as error:
                sample_errors.append({"line": number, "error": type(error).__name__})
        if sample_errors:
            sample_receipt["state"] = "partial"
    routers = {
        label: optional(e / f"fallback-check-{label}.json")
        for label in ("smoke", "prelaunch", "after")
    }
    # Router deployment IDs are deployment fingerprints, not API key hashes.
    return {
        "windows": windows,
        "samples_15min": samples,
        "sample_receipt": sample_receipt,
        "routers": routers,
        "launch": optional(h / "g5/process.json"),
        "completion": optional(e / "run-completion.json"),
        "collection_steps": optional(e / "finish-step-status.json"),
        "alias_redaction": (
            "Only proxy master key, client-a and client-b aliases and known "
            "agent families are retained; others use stable numbered labels. No API key/hash, "
            "client IP, email, request body or transcript is exported."
        ),
        "load_limits": (
            "Samples overlap and are lower bounds until in-flight requests are persisted. "
            "Full-window reads count rows logged by their recorded read time; "
            "bypass traffic is invisible."
        ),
    }


def export_package(h, destination, secret):
    job = h / "g5"
    assert not destination.exists(), "never overwrite an exported or published package"
    freeze = read(h / "freeze-g5.json")
    for n, d in freeze["files"].items():
        assert sha(job / n) == d, n
    for n, d in freeze["script_sha256"].items():
        assert sha(h / n) == d, n
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".g5-export-", dir=destination.parent) as temp:
        out = Path(temp) / "package"
        out.mkdir()
        export_job(job, out / "g5", secret)
        dump(out / "provider-evidence.json", scrub(provider_evidence(h, secret), job), secret)
        for source, name in [
            ("freeze-g5.json", "freeze-record.json"),
            ("freeze-post.json", "freeze-post.json"),
            ("freeze-on-51.md", "freeze-on-51.md"),
            ("rule-a-common-predicates.md", "rule-a-common-predicates.md"),
            ("freeze-comment-readback.json", "freeze-comment-readback.json"),
        ]:
            shutil.copy2(h / source, out / name)
        for name in (
            "smoke-summary.json",
            "smoke-verification.json",
            "smoke-request-ledger.json",
            "smoke-job-status.json",
            "run-completion.json",
            "finish-step-status.json",
            "FINAL-EVIDENCE-READY.json",
            "ledger-build-status.json",
        ):
            receipt = optional(
                h / "evidence" / name, list if name == "finish-step-status.json" else dict
            )
            if receipt["state"] == "available":
                dump(out / name, scrub(receipt["data"], job), secret)
            else:
                dump(out / (name + ".unavailable.json"), receipt, secret)
        scripts = out / "execution-scripts"
        scripts.mkdir()
        for name in freeze["script_sha256"]:
            shutil.copy2(h / name, scripts / (name + ".txt"))
        operations = []
        py = str(job / "venv/bin/python")
        argv = [
            py,
            "-I",
            str(h / "extract-sessions.py"),
            str(job),
            "--output",
            str(out / "g5/session-diagnosis.json"),
        ]
        p = subprocess.run(argv, capture_output=True, text=True)
        operations.append(
            {
                "operation": "extract_sessions",
                "returncode": p.returncode,
                "stdout": p.stdout,
                "stderr": p.stderr,
            }
        )
        argv = [py, "-I", str(h / "classify-first-answers.py"), str(job / "experiment")]
        p = subprocess.run(argv, capture_output=True, text=True)
        operations.append(
            {"operation": "classify_first_answers", "returncode": p.returncode, "stderr": p.stderr}
        )
        dump(
            out / "g5/first-answers.json",
            {
                "sessions": [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
                if p.returncode == 0
                else [],
                "extraction_succeeded": p.returncode == 0,
            },
            secret,
        )
        dump(out / "publication-extraction.json", scrub(operations, job), secret)
        for name in (
            "reproduce.py",
            "test_report_g5.py",
            "export.py",
            "render.py",
            "README.template.md",
        ):
            shutil.copy2(Path(__file__).parent / name, out / name)
        for p in out.rglob("*"):
            if p.is_file():
                t = p.read_text()
                assert secret not in t and hashlib.sha256(secret.encode()).hexdigest() not in t, (
                    p.name
                )
                if p.suffix == ".json" and p.name != "preparation.json":
                    assert "/home/" not in t and "/tmp/" not in t, p.name
        out.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=H)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    secret = next(
        shlex.split(line.partition("=")[2].strip())[0]
        for line in Path("/home/user/Workspace/example/infrastructure/.env")
        .read_text()
        .splitlines()
        if line.startswith("LITELLM_MASTER_KEY=")
    )
    export_package(args.state, args.output.resolve(), secret)
    print(json.dumps({"exported_package": str(args.output), "atomic_export": True}))
