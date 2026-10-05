#!/usr/bin/env python3
"""Require one canonical release identity across tag, source and wheel."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from email.parser import BytesParser
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def check_release(tag: str, wheel: Path) -> None:
    if re.fullmatch(r"[0-9]{4}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*)", tag) is None:
        raise ValueError("tag must be unprefixed YYYY.M.PATCH without zero padding")
    source = Path(__file__).resolve().parents[1] / "src/scrutare/__init__.py"
    versions = [
        ast.literal_eval(node.value)
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets)
    ]
    if versions != [tag]:
        raise ValueError("authored runtime version does not match tag")
    if wheel.name != f"scrutare-{tag}-py3-none-any.whl":
        raise ValueError("wheel filename does not match tag")
    with ZipFile(wheel) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        expected = f"scrutare-{tag}.dist-info/METADATA"
        if metadata_paths != [expected]:
            raise ValueError("wheel must contain exactly one matching distribution METADATA")
        metadata = BytesParser().parsebytes(archive.read(expected))
    if metadata.get_all("Name") != ["scrutare"] or metadata.get_all("Version") != [tag]:
        raise ValueError("wheel METADATA name or version does not match release")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    try:
        check_release(args.tag, args.wheel)
    except (OSError, ValueError, SyntaxError, BadZipFile) as error:
        print(f"release check failed: {error}", file=sys.stderr)
        return 1
    print(f"Release verified: {args.tag} {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
