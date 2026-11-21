#!/usr/bin/env python3
"""Move Scrutare's nare pin to a verified release and report what the range changes.

`status` compares `nare.pin` with nare's latest published release. `bump` verifies the
target tag's commit with `git ls-remote`, rewrites `nare.pin`, the literal versions in
the user documents and the previous pin in the pin test, and prints the release notes in
the range and, given both `nare contract` outputs, what the contract changes.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPOSITORY = "ViviDynamics/nare"
REMOTE = f"https://github.com/{REPOSITORY}.git"
PIN_FILE = Path("nare.pin")
PIN_TEST = Path("tests/test_nare_runtime_pin.py")
# User documents that name the pinned release in prose or commands. Each must mention
# the current pin, so a document that stops naming it is noticed here, not left stale.
DOCUMENTS = (
    Path("README.md"),
    Path(".agents/test-commands.md"),
    Path("docs/cli.md"),
    Path("docs/config.md"),
    Path("docs/releases.md"),
    Path("docs/session-fanout.md"),
)
CALVER = re.compile(r"[0-9]{4}\.(?:[1-9]|1[0-2])\.(?:0|[1-9][0-9]*)")
COMMIT = re.compile(r"[0-9a-f]{40}")
PREVIOUS = re.compile(r'^PREVIOUS = \("[^"\n]*", "[^"\n]*"\)$', re.MULTILINE)
# A GitHub pull request body holds at most 65536 characters.
BODY_LIMIT = 60000
CONTRACT_LABEL = "nare-contract-change"
ISSUE_REFERENCE = re.compile(r"(^|[^\w/#&])#([0-9]+)\b")
MENTION = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")
DESCRIPTION = "Move Scrutare's nare pin to a verified release and report the range."


class BumpError(ValueError):
    """A precondition of the bump failed; nothing was written."""


@dataclass(frozen=True)
class Pin:
    release: str
    commit: str


@dataclass(frozen=True)
class Release:
    tag: str
    body: str


@dataclass(frozen=True)
class ContractDiff:
    old_version: object
    new_version: object
    flags_added: list[str] = field(default_factory=list)
    flags_removed: list[str] = field(default_factory=list)
    flags_changed: list[tuple[str, object, object]] = field(default_factory=list)
    defaults: list[tuple[str, object, object]] = field(default_factory=list)
    entries_added: list[str] = field(default_factory=list)
    entries_removed: list[str] = field(default_factory=list)
    entries_changed: list[str] = field(default_factory=list)

    @property
    def version_changed(self) -> bool:
        return self.old_version != self.new_version

    @property
    def empty(self) -> bool:
        return not (self.version_changed or self.flags_added or self.flags_removed
                    or self.flags_changed or self.defaults or self.entries_added
                    or self.entries_removed or self.entries_changed)


@dataclass(frozen=True)
class Bump:
    old: Pin
    new: Pin
    changes: dict[Path, tuple[str, str]]
    releases: list[Release]
    contract: ContractDiff | None


def calver_key(tag: str) -> tuple[int, int, int]:
    if CALVER.fullmatch(tag) is None:
        raise BumpError(f"{tag!r} is not a canonical YYYY.M.PATCH release")
    year, month, patch = (int(part) for part in tag.split("."))
    return year, month, patch


def parse_pin(text: str) -> Pin:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            key, separator, value = line.partition("=")
            if not separator or key in values:
                raise BumpError(f"nare.pin line {line!r} is not a single KEY=value")
            values[key] = value
    if set(values) != {"NARE_RELEASE", "NARE_COMMIT"}:
        raise BumpError("nare.pin must set exactly NARE_RELEASE and NARE_COMMIT")
    pin = Pin(values["NARE_RELEASE"], values["NARE_COMMIT"])
    calver_key(pin.release)
    if COMMIT.fullmatch(pin.commit) is None:
        raise BumpError("NARE_COMMIT must be a full 40 character commit")
    return pin


def render_pin(text: str, pin: Pin) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        if line.startswith("NARE_RELEASE="):
            line = f"NARE_RELEASE={pin.release}\n"
        elif line.startswith("NARE_COMMIT="):
            line = f"NARE_COMMIT={pin.commit}\n"
        lines.append(line)
    return "".join(lines)


def releases_from(entries: Sequence[Mapping[str, Any]]) -> list[Release]:
    """Published releases with canonical tags, oldest first. Drafts and prereleases drop."""
    found = []
    for entry in entries:
        tag = entry.get("tag_name")
        if entry.get("draft") or entry.get("prerelease") or not isinstance(tag, str):
            continue
        if CALVER.fullmatch(tag) is None:
            continue
        body = entry.get("body")
        found.append(Release(tag, body if isinstance(body, str) else ""))
    return sorted(found, key=lambda release: calver_key(release.tag))


def fetch_releases(source: Path | None) -> list[Release]:
    if source is not None:
        entries = json.loads(source.read_text())
    else:
        output = subprocess.check_output(
            ["gh", "api", "--paginate", f"repos/{REPOSITORY}/releases?per_page=100",
             "--jq", ".[] | {tag_name, body, draft, prerelease}"],
            text=True,
        )
        entries = [json.loads(line) for line in output.splitlines() if line.strip()]
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise BumpError("release data must be a list of release objects")
    return releases_from(entries)


def latest(releases: Sequence[Release]) -> str:
    if not releases:
        raise BumpError(f"{REPOSITORY} has no published release")
    return releases[-1].tag


def resolve_commit(remote: str, tag: str) -> str:
    """The commit a remote tag names, peeling an annotated tag."""
    calver_key(tag)
    output = subprocess.check_output(
        ["git", "ls-remote", "--tags", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        text=True,
    )
    refs = {}
    for line in output.splitlines():
        sha, _, ref = line.partition("\t")
        refs[ref] = sha
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if commit is None:
        raise BumpError(f"tag {tag} does not exist on {remote}")
    if COMMIT.fullmatch(commit) is None:
        raise BumpError(f"tag {tag} resolved to {commit!r}, not a commit")
    return commit


def flatten(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict) and value:
        flat: dict[str, object] = {}
        for key, item in value.items():
            flat.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: value}


def is_default(path: str) -> bool:
    return any("default" in part for part in path.split("."))


def diff_contracts(old: Mapping[str, Any], new: Mapping[str, Any]) -> ContractDiff:
    old_flags = old["flags"] if isinstance(old.get("flags"), dict) else {}
    new_flags = new["flags"] if isinstance(new.get("flags"), dict) else {}
    old_flat, new_flat = flatten(dict(old)), flatten(dict(new))
    defaults = [
        (path, old_flat.get(path), new_flat.get(path))
        for path in sorted(set(old_flat) | set(new_flat))
        if is_default(path) and old_flat.get(path) != new_flat.get(path)
    ]
    # `contract`, `nare` and `flags` have their own sections; the release always differs.
    entries = set(old) | set(new)
    entries -= {"contract", "nare", "flags"}
    return ContractDiff(
        old_version=old.get("contract"),
        new_version=new.get("contract"),
        flags_added=sorted(set(new_flags) - set(old_flags)),
        flags_removed=sorted(set(old_flags) - set(new_flags)),
        flags_changed=[(name, old_flags[name], new_flags[name])
                       for name in sorted(set(old_flags) & set(new_flags))
                       if old_flags[name] != new_flags[name]],
        defaults=defaults,
        entries_added=sorted(name for name in entries if name not in old),
        entries_removed=sorted(name for name in entries if name not in new),
        entries_changed=sorted(name for name in entries if name in old and name in new
                               and old[name] != new[name]),
    )


def replace_literals(text: str, old: Pin, new: Pin) -> tuple[str, int]:
    release = re.compile(rf"(?<![0-9.]){re.escape(old.release)}(?![0-9])")
    text, releases = release.subn(new.release, text)
    commit = re.compile(rf"(?<![0-9a-f]){re.escape(old.commit)}(?![0-9a-f])")
    text, commits = commit.subn(new.commit, text)
    return text, releases + commits


def plan_bump(root: Path, target: str, releases: Sequence[Release], remote: str,
              contracts: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None,
              ) -> Bump | None:
    """The bump to `target`, or None when the pin is already there."""
    pin_text = (root / PIN_FILE).read_text()
    old = parse_pin(pin_text)
    if target == old.release:
        if resolve_commit(remote, target) != old.commit:
            raise BumpError(f"tag {target} no longer names the pinned commit {old.commit}")
        return None
    if calver_key(target) < calver_key(old.release):
        raise BumpError(f"{target} is older than the pinned {old.release}")
    if target not in {release.tag for release in releases}:
        raise BumpError(f"{target} is not a published {REPOSITORY} release")
    new = Pin(target, resolve_commit(remote, target))
    changes: dict[Path, tuple[str, str]] = {PIN_FILE: (pin_text, render_pin(pin_text, new))}
    for path in DOCUMENTS:
        original = (root / path).read_text()
        rewritten, count = replace_literals(original, old, new)
        if count == 0:
            raise BumpError(f"{path} does not mention the pinned {old.release}")
        changes[path] = (original, rewritten)
    test = (root / PIN_TEST).read_text()
    previous = f'PREVIOUS = ("{old.release}", "{old.commit}")'
    moved, count = PREVIOUS.subn(previous, test)
    if count != 1:
        raise BumpError(f"{PIN_TEST} must assign PREVIOUS exactly once")
    changes[PIN_TEST] = (test, moved)
    in_range = [release for release in releases
                if calver_key(old.release) < calver_key(release.tag) <= calver_key(target)]
    diff = diff_contracts(*contracts) if contracts is not None else None
    return Bump(old, new, changes, in_range, diff)


def release_notes(release: Release) -> str:
    # nare appends artifact checksums to every release; the PR needs the change, not those.
    body = re.split(r"^SHA-256:\s*$", release.body, maxsplit=1, flags=re.MULTILINE)[0]
    # In a Scrutare pull request `#12` would link Scrutare's issue 12 and `@name` would
    # notify that account, so qualify nare's references and break the mentions.
    body = ISSUE_REFERENCE.sub(rf"\1{REPOSITORY}#\2", body)
    body = MENTION.sub("@\u200b", body)
    return body.strip() or "(no release notes)"


def code(value: object) -> str:
    return f"`{json.dumps(value)}`"


def contract_section(diff: ContractDiff | None) -> list[str]:
    lines = ["## `nare contract` changes", ""]
    if diff is None:
        return lines + ["Not collected: run the bump with both releases' `nare contract` "
                        "output to compare them.", ""]
    if diff.empty:
        return lines + ["None. Flags, defaults and every other entry are unchanged.", ""]
    if diff.version_changed:
        lines += [f"- **Contract version: {code(diff.old_version)} to "
                  f"{code(diff.new_version)}.**"]
    for title, names in (("Flags added", diff.flags_added),
                         ("Flags removed", diff.flags_removed),
                         ("Entries added", diff.entries_added),
                         ("Entries removed", diff.entries_removed),
                         ("Entries changed", diff.entries_changed)):
        if names:
            lines.append(f"- {title}: " + ", ".join(f"`{name}`" for name in names))
    if diff.flags_changed:
        lines.append("- Flags changed:")
        lines += [f"  - `{name}`: {code(before)} to {code(after)}"
                  for name, before, after in diff.flags_changed]
    if diff.defaults:
        lines.append("- Defaults changed:")
        lines += [f"  - `{path}`: {code(before)} to {code(after)}"
                  for path, before, after in diff.defaults]
    return lines + [""]


def report(bump: Bump) -> str:
    lines = [
        f"Moves the nare pin from {bump.old.release} (`{bump.old.commit}`) to "
        f"{bump.new.release} (`{bump.new.commit}`). The tag's commit was verified with "
        "`git ls-remote`.",
        "",
    ]
    if bump.contract is not None and bump.contract.version_changed:
        lines += [
            f"> **This release changes the nare machine contract version from "
            f"{code(bump.contract.old_version)} to {code(bump.contract.new_version)}.** "
            f"Scrutare must be adapted before this can merge. Labeled `{CONTRACT_LABEL}`.",
            "",
        ]
    lines += contract_section(bump.contract)
    lines += [f"## Release notes ({bump.old.release}..{bump.new.release})", ""]
    for release in reversed(bump.releases):
        lines += [f"### [{release.tag}](https://github.com/{REPOSITORY}/releases/tag/"
                  f"{release.tag})", "", release_notes(release), ""]
    lines += [
        "## Before merging",
        "",
        "- This bump is never merged automatically. A green, additive bump is merged after "
        "review.",
        "- The bump workflow dispatches CI on this branch itself, since a push made with "
        "its `GITHUB_TOKEN` starts no other workflow. A pull request a maintainer opens "
        "from the branch runs the usual CI and disclosure guard.",
        "- A nare defect this bump exposes is filed on ViviDynamics/nare, linked here and "
        "fixed in nare. Scrutare does not carry workarounds for nare defects.",
        "",
    ]
    text = "\n".join(lines)
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT].rsplit("\n", 1)[0] + "\n\n(Release notes truncated.)\n"
    return text


def unified(bump: Bump) -> str:
    return "".join(
        "".join(difflib.unified_diff(before.splitlines(keepends=True),
                                     after.splitlines(keepends=True),
                                     f"a/{path}", f"b/{path}"))
        for path, (before, after) in bump.changes.items()
    )


def load_contract(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise BumpError(f"{path} is not a `nare contract` object")
    return value


def write_outputs(path: Path | None, values: Mapping[str, str]) -> None:
    if path is not None:
        with path.open("a") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--releases", type=Path,
                        help="release JSON (GitHub API shape) instead of `gh api`")
    parser.add_argument("--outputs", type=Path, help="append KEY=value lines, as $GITHUB_OUTPUT")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="compare the pin with nare's latest release")
    bumping = commands.add_parser("bump", help="move the pin to a release")
    bumping.add_argument("release", help="a release tag, or `latest`")
    bumping.add_argument("--remote", default=REMOTE, help="git remote that verifies the tag")
    bumping.add_argument("--old-contract", type=Path, help="`nare contract` of the pinned release")
    bumping.add_argument("--new-contract", type=Path, help="`nare contract` of the target")
    bumping.add_argument("--report", type=Path, help="also write the report here")
    bumping.add_argument("--dry-run", action="store_true",
                         help="print the report and the diff; write no repository file")
    args = parser.parse_args(argv)
    try:
        releases = fetch_releases(args.releases)
        if args.command == "status":
            current = parse_pin((args.root / PIN_FILE).read_text()).release
            newest = latest(releases)
            values = {"current": current, "latest": newest,
                      "changed": str(newest != current).lower()}
            print("".join(f"{key}={value}\n" for key, value in values.items()), end="")
            write_outputs(args.outputs, values)
            return 0
        target = latest(releases) if args.release == "latest" else args.release
        if (args.old_contract is None) != (args.new_contract is None):
            raise BumpError("pass both --old-contract and --new-contract, or neither")
        contracts = None
        if args.old_contract is not None and args.new_contract is not None:
            contracts = (load_contract(args.old_contract), load_contract(args.new_contract))
        bump = plan_bump(args.root, target, releases, args.remote, contracts)
        if bump is None:
            print(f"nare pin is already {target}; nothing to do.")
            write_outputs(args.outputs, {"changed": "false", "release": target})
            return 0
        text = report(bump)
        print(text, end="")
        if args.report is not None:
            args.report.write_text(text)
        if args.dry_run:
            print("\n## Dry run: files that would change\n\n```diff\n" + unified(bump) + "```")
        else:
            for path, (_, after) in bump.changes.items():
                (args.root / path).write_text(after)
        contract_changed = bump.contract is not None and bump.contract.version_changed
        write_outputs(args.outputs, {
            "changed": "true", "previous": bump.old.release, "release": bump.new.release,
            "commit": bump.new.commit, "contract_changed": str(contract_changed).lower(),
        })
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"bump-nare: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
