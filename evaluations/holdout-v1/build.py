"""Rebuild original sealed data without executing fixtures or contacting providers."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from catalog import CASES


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_environment():
    return {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Scrutare fixture authors",
        "GIT_AUTHOR_EMAIL": "fixtures@example.invalid",
        "GIT_COMMITTER_NAME": "Scrutare fixture authors",
        "GIT_COMMITTER_EMAIL": "fixtures@example.invalid",
        "GIT_AUTHOR_DATE": "1600000000 +0000",
        "GIT_COMMITTER_DATE": "1600000000 +0000",
    }


def git(directory, *args, data=None):
    return subprocess.run(
        ["git", *args],
        cwd=directory,
        env=git_environment(),
        input=data,
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout


def commit_pair(identifier, base, head):
    """Use real Git objects with deterministic dates; identities are synthetic/local."""
    with tempfile.TemporaryDirectory(prefix="scrutare-holdout-git-") as temporary:
        directory = Path(temporary)
        git(directory, "-c", "init.templateDir=", "init", "--quiet")
        revisions = []
        for side, files in (("base", base), ("head", head)):
            for relative, contents in files.items():
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            git(directory, "add", "--all")
            tree = git(directory, "write-tree").decode().strip()
            parents = ("-p", revisions[0]) if revisions else ()
            revision = (
                git(
                    directory,
                    "commit-tree",
                    tree,
                    *parents,
                    data=f"original {identifier} {side}\n".encode(),
                )
                .decode()
                .strip()
            )
            revisions.append(revision)
        patch = git(
            directory,
            "diff",
            "--no-ext-diff",
            "--no-renames",
            "--no-color",
            revisions[0],
            revisions[1],
            "--",
        )
        return (*revisions, patch)


def build(root):
    entries = []
    for specification in CASES:
        identifier = specification["id"]
        files = dict(specification["dependencies"])
        base = {
            k: normalized(v)
            for k, v in {**files, specification["path"]: specification["base"]}.items()
        }
        head = {
            k: normalized(v)
            for k, v in {**files, specification["path"]: specification["head"]}.items()
        }
        before, after, patch = commit_pair(
            identifier,
            {k: v.encode() for k, v in base.items()},
            {k: v.encode() for k, v in head.items()},
        )
        for side, source in (("base", base), ("head", head)):
            for relative, contents in source.items():
                path = root / "sources" / identifier / side / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents, encoding="utf-8")
        capture = root / "captures" / identifier
        capture.mkdir(parents=True, exist_ok=True)
        (capture / "diff.patch").write_bytes(patch)
        dump(capture / "files.json", [{"filename": specification["path"], "status": "modified"}])
        repository = "sealed-fixtures/local"
        dump(
            capture / "metadata.json",
            {
                "schema_version": 1,
                "scrutare_version": "2026.10.2",
                "status": "ingested",
                "repository": repository,
                "pr_number": len(entries) + 1,
                "head_sha": after,
                "pull_request": {
                    "number": len(entries) + 1,
                    "state": "open",
                    "merged": False,
                    "title": "Update implementation",
                    "body": "",
                    "changed_files": 1,
                    "base": {"sha": before, "ref": "main", "repo": {"full_name": repository}},
                    "head": {"sha": after, "ref": "candidate", "repo": {"full_name": repository}},
                },
            },
        )
        dump(
            root / "labels" / f"{identifier}.json",
            {
                "case_id": identifier,
                "defects": []
                if specification["clean"]
                else [
                    {
                        "id": f"{identifier}-defect",
                        "trigger": specification["trigger"],
                        "impact": specification["impact"],
                        "evidence": f"{specification['path']}: {specification['impact']}",
                        "match_criteria": "Identify the changed behavior for "
                        f"{specification['trigger']} and its consequence: "
                        f"{specification['impact']}",
                        "fix_or_counterexample": specification["fix"],
                    }
                ],
                "counterexamples": [specification["fix"]],
                "unchanged_supporting_paths": sorted(files),
                "mechanical_control": f"controls/{identifier}.py",
                "expected_exit": {"base": 0, "head": 0 if specification["clean"] else 1},
                "expected_head_error": None
                if specification["clean"]
                else {
                    "sealed-operations-migration-drop-order": "OperationalError",
                    "sealed-consumer-optional-description": "AttributeError",
                    "sealed-consumer-unicode-preview": "UnicodeDecodeError",
                    "sealed-consumer-keyword-contract": "TypeError",
                }.get(specification["case_name"], "AssertionError"),
            },
        )
        control = root / "controls" / f"{identifier}.py"
        control.parent.mkdir(parents=True, exist_ok=True)
        control.write_text(normalized(specification["control"]), encoding="utf-8")
        entries.append(
            {
                "id": identifier,
                "split": "holdout",
                "domain": specification["domain"],
                "capture": f"captures/{identifier}",
                "labels": f"labels/{identifier}.json",
                "context_sources": {
                    side: {
                        "directory": f"sources/{identifier}/{side}",
                        "repository": repository,
                        "revision": revision,
                    }
                    for side, revision in (("base", before), ("head", after))
                },
                "provenance": {
                    "origin": "original",
                    "license": "Elastic-2.0",
                    "author": "ViviDynamics Scrutare contributors",
                    "external_material": False,
                    "source": "Purpose-written local synthetic Git fixtures; no hosted PR claim.",
                },
            }
        )
    dump(root / "manifest.json", {"schema_version": 1, "cases": entries})


def normalized(source):
    """Apply locked Ruff import layout before generating source Git identities."""
    return subprocess.run(
        ["ruff", "check", "--fix", "--stdin-filename", "fixture.py", "-"],
        input=source.encode(),
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout.decode()


if __name__ == "__main__":
    build(Path(__file__).parent)
