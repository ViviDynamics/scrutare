"""Execute only this checked-in original corpus's trusted evaluator controls.

Not a runner for arbitrary PRs or arbitrary corpus arguments. Model jobs never call
this verifier. Fixture execution uses isolated temporary copies, no network calls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def inventory(root):
    names = ["manifest.json", "catalog.py", "build.py", "verify.py", "README.md"]
    for folder in ("captures", "labels", "controls", "sources"):
        names.extend(
            path.relative_to(root).as_posix()
            for path in (root / folder).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def verify(root):
    root = root.resolve()
    if root != Path(__file__).resolve().parent:
        raise ValueError("Only checked-in original sealed fixtures may execute")
    frozen = json.loads((root / "freeze-sha256.json").read_text())
    current = inventory(root)
    if frozen["files"] != current:
        raise ValueError("Sealed fixture inventory changed; refuse execution")
    canonical = json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if frozen["inventory_sha256"] != digest:
        raise ValueError("Inventory digest disagrees")
    spec = importlib.util.spec_from_file_location("holdout_build", root / "build.py")
    module = importlib.util.module_from_spec(spec)
    # Import the trusted catalog via an explicit local module; no fixture source is imported here.
    catalog_spec = importlib.util.spec_from_file_location("catalog", root / "catalog.py")
    catalog = importlib.util.module_from_spec(catalog_spec)
    catalog_spec.loader.exec_module(catalog)
    previous = sys.modules.get("catalog")
    sys.modules["catalog"] = catalog
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            del sys.modules["catalog"]
        else:
            sys.modules["catalog"] = previous
    entries = json.loads((root / "manifest.json").read_text())["cases"]
    rows, failures = [], []
    patches_verified = 0
    for entry in entries:
        identifier = entry["id"]
        gold = json.loads((root / entry["labels"]).read_text())
        sources = {}
        for side in ("base", "head"):
            directory = root / entry["context_sources"][side]["directory"]
            sources[side] = {
                p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*.py")
            }
            for name, content in sources[side].items():
                compile(content, name, "exec")
        before, after, patch = module.commit_pair(identifier, sources["base"], sources["head"])
        capture = root / entry["capture"]
        metadata = json.loads((capture / "metadata.json").read_text())
        if (
            before != metadata["pull_request"]["base"]["sha"]
            or after != metadata["pull_request"]["head"]["sha"]
            or patch != (capture / "diff.patch").read_bytes()
        ):
            failures.append(identifier + ": git capture mismatch")
        with tempfile.TemporaryDirectory(prefix="scrutare-patch-") as temporary:
            directory = Path(temporary)
            shutil.copytree(
                root / entry["context_sources"]["base"]["directory"], directory, dirs_exist_ok=True
            )
            module.git(directory, "-c", "init.templateDir=", "init", "--quiet")
            module.git(directory, "apply", "--check", "-", data=patch)
            module.git(directory, "apply", "-", data=patch)
            applied = {
                p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob("*.py")
            }
            if applied != sources["head"]:
                failures.append(identifier + ": exact patch does not produce head")
            else:
                patches_verified += 1
        exits, errors = {}, {}
        for side in ("base", "head"):
            with tempfile.TemporaryDirectory(prefix="scrutare-gold-") as temporary:
                directory = Path(temporary)
                shutil.copytree(
                    root / entry["context_sources"][side]["directory"],
                    directory,
                    dirs_exist_ok=True,
                )
                control = (root / gold["mechanical_control"]).read_text()
                driver = "import sys; sys.path.insert(0, sys.argv[1]); exec(sys.argv[2])"
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", driver, str(directory), control],
                    cwd=directory,
                    env={"PATH": os.environ["PATH"]},
                    capture_output=True,
                    timeout=10,
                )
                exits[side] = result.returncode
                lines = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
                errors[side] = lines[-1].split(":", 1)[0].rsplit(".", 1)[-1] if lines else None
                expected_error = gold["expected_head_error"] if side == "head" else None
                if result.returncode != gold["expected_exit"][side]:
                    failures.append(identifier + ": unexpected " + side + " control exit")
                if errors[side] != expected_error:
                    failures.append(identifier + ": unexpected " + side + " failure type")
        rows.append(
            {
                "case_id": identifier,
                "base_sha": before,
                "head_sha": after,
                "control_exits": exits,
                "control_errors": errors,
                "expected_defect": bool(gold["defects"]),
            }
        )
    return {
        "schema_version": 1,
        "kind": "trusted_mechanical_controls_not_model_accuracy",
        "inventory_sha256": digest,
        "cases": len(rows),
        "patches_verified": patches_verified,
        "defects_verified": sum(r["expected_defect"] for r in rows),
        "clean_controls_verified": sum(not r["expected_defect"] for r in rows),
        "failures": failures,
        "results": rows,
    }


if __name__ == "__main__":
    report = verify(Path(__file__).parent)
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(bool(report["failures"]))
