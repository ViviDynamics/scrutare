#!/usr/bin/env python3
"""Select a release tag from validated main history, or stamp a build checkout."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CALVER = re.compile(r"[0-9]{4}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*)")
SOURCE = Path("src/scrutare/__init__.py")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def ancestor(commit: str, descendant: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", commit, descendant])
    if result.returncode not in (0, 1):
        raise ValueError("could not check release ancestry")
    return result.returncode == 0


def version_assignment() -> ast.Assign:
    nodes = [
        node for node in ast.parse(SOURCE.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets)
    ]
    if len(nodes) != 1:
        raise ValueError("source must contain one version assignment")
    return nodes[0]


def validate_version(value: str) -> None:
    if CALVER.fullmatch(value) is None:
        raise ValueError("release version must be canonical YYYY.M.PATCH")


def plan(month: str) -> tuple[str, bool]:
    if re.fullmatch(r"[0-9]{4}\.(?:[1-9]|1[0-2])", month) is None:
        raise ValueError("release month must be canonical YYYY.M")
    head = git("rev-parse", "HEAD")
    if not ancestor(head, "origin/main"):
        raise ValueError("only commits in main history can be released")
    authored = ast.literal_eval(version_assignment().value)
    if not isinstance(authored, str):
        raise ValueError("authored version must be a string")
    validate_version(authored)
    tags = [name for name in git("tag", "--list").splitlines() if CALVER.fullmatch(name)]
    versions = sorted(tags, key=lambda name: tuple(int(part) for part in name.split(".")))
    tagged_at = ""
    latest = ""
    for name in versions:
        # Peel annotated tags; tags of trees/blobs are not release commits.
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{name}^{{commit}}"],
            text=True, capture_output=True,
        )
        if result.returncode:
            continue
        commit = result.stdout.strip()
        if commit == head:
            tagged_at = name
        elif ancestor(commit, head):
            latest = name
    if tagged_at:
        return tagged_at, True
    if authored not in tags:
        return authored, False
    patch = int(latest.rsplit(".", 1)[1]) + 1 if latest.startswith(month + ".") else 0
    while f"{month}.{patch}" in tags:
        patch += 1
    return f"{month}.{patch}", False


def stamp(version: str) -> None:
    validate_version(version)
    node = version_assignment()
    lines = SOURCE.read_text().splitlines(keepends=True)
    # Restrict replacement to the sole literal assignment; preserve the rest of the file.
    if node.lineno != node.end_lineno or not isinstance(node.value, ast.Constant):
        raise ValueError("source version must be a single-line literal assignment")
    lock = Path("uv.lock")
    locked = None
    if lock.exists():
        original = lock.read_text()
        locked, count = re.subn(
            r'(\[\[package\]\]\nname = "scrutare"\nversion = ")[^"\n]+(")',
            lambda match: match[1] + version + match[2], original,
        )
        # uv currently omits the editable project's dynamic version entirely.
        dynamic = '[[package]]\nname = "scrutare"\nsource = { editable = "." }\n'
        if original.count('[[package]]\nname = "scrutare"\n') != 1 or (
            count != 1 and not (count == 0 and dynamic in original)
        ):
            raise ValueError("lockfile must contain one scrutare package entry")
    lines[node.lineno - 1] = f'__version__ = "{version}"\n'
    SOURCE.write_text("".join(lines))
    if locked is not None:
        lock.write_text(locked)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("plan")
    now = datetime.now(timezone.utc)
    selection.add_argument("--month", default=f"{now.year}.{now.month}")
    stamping = commands.add_parser("stamp")
    stamping.add_argument("version")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            version, exists = plan(args.month)
            print(f"version={version}\ntag_exists={str(exists).lower()}")
        else:
            stamp(args.version)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        print(f"auto-release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
