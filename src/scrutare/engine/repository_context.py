"""Pinned Git object capture and deterministic bounded text evidence, without checkout access."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from hashlib import sha1, sha256
from pathlib import Path
from typing import Any, Protocol

from scrutare.config import ReviewConfig
from scrutare.engine.github import GitHubError, _repository
from scrutare.engine.paths import _matches, parse_changed_files, select_changed_files
from scrutare.findings.models import _path

MANIFEST = "repository-context.json"
_SHA = re.compile(r"[0-9a-f]{40}")
_SENSITIVE = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
              "id_ed25519*", "**/.git/**", "**/.ssh/**", "**/.aws/**", ".netrc", ".npmrc")
_STATUSES = {"captured", "excluded", "sensitive", "absent", "missing", "binary", "symlink",
             "submodule", "directory", "oversized", "file_limit", "total_limit", "tree_limit",
             "unavailable"}


class RepositoryObjectClient(Protocol):
    def get_commit(self, repository: str, sha: str) -> str: ...
    def get_tree(self, repository: str, sha: str) -> tuple[dict[str, Any], ...]: ...
    def get_blob(self, repository: str, sha: str, *, max_bytes: int) -> bytes: ...


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def regular(path: Path) -> bytes:
    if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_file():
        raise ValueError("Repository context expects unlinked regular artifacts.")
    return path.read_bytes()


def _json(path: Path) -> Any:
    # Reuse duplicate-key rejection, rather than accepting divergent manifest interpretations.
    from scrutare.engine.review_inputs import _read_json
    return _read_json(path)


def _revision(pr: dict[str, Any], side: str) -> tuple[str | None, str]:
    value = pr[side]
    sha = value.get("sha")
    if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
        raise ValueError("Repository context requires exact captured commit SHAs.")
    repo = value.get("repo")
    if side == "head" and repo is None:
        return None, sha
    if not isinstance(repo, dict) or not isinstance(repo.get("full_name"), str):
        raise ValueError("Repository context requires captured repository identities.")
    _repository(repo["full_name"])
    return repo["full_name"], sha


def _candidates(files_data: object, config: ReviewConfig) -> list[dict[str, Any]]:
    files = parse_changed_files(files_data)
    eligible = {file.filename for file in select_changed_files(files, config.github.paths)}
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for file in sorted(files, key=lambda file: file.filename):
        for side in ("base", "head"):
            path = file.previous_filename if side == "base" and file.previous_filename else (
                file.filename)
            seen.add((side, path))
            result.append({"side": side, "path": path, "changed_path": file.filename,
                           "reason": "changed_file", "eligible": file.filename in eligible,
                           "absent": (side == "base" and file.status == "added"
                                      or side == "head" and file.status == "removed")})
    for path in sorted(config.context.related_paths):
        for side in ("base", "head"):
            if (side, path) not in seen:
                result.append({"side": side, "path": path, "changed_path": None,
                               "reason": "explicit_related", "eligible": False, "absent": False})
                seen.add((side, path))
    for candidate in result:
        _path(candidate["path"])
        if "\\" in candidate["path"]:
            raise ValueError("Repository context refuses ambiguous path separators.")
    return result


def _policy_status(candidate: dict[str, Any], config: ReviewConfig) -> str | None:
    path = candidate["path"]
    if any(_matches(path, pattern) for pattern in _SENSITIVE):
        return "sensitive"
    names = (path, candidate["changed_path"])
    if (config.context.include and not any(_matches(path, p) for p in config.context.include)
            or any(_matches(name, pattern) for name in names if name is not None
                   for pattern in config.context.exclude)):
        return "excluded"
    if candidate["absent"]:
        return "absent"
    return None


def _artifact(side: str, path: str) -> str:
    return f"repository-{side}-{sha256(path.encode()).hexdigest()}.txt"


def _capture_repository_context(client: RepositoryObjectClient, run: Path, config: ReviewConfig,
                                source: dict[str, Any]) -> None:
    """Capture producer-owned objects only at metadata revisions; never execute source files."""
    if not config.context.enabled:
        return
    metadata = _json(run / "metadata.json")
    candidates = _candidates(_json(run / "files.json"), config)
    revisions = {side: _revision(metadata["pull_request"], side) for side in ("base", "head")}
    trees: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
    roots: dict[str, str | None] = {}
    for side, (repository, sha) in revisions.items():
        roots[side] = client.get_commit(repository, sha) if repository is not None else None
    retained: dict[str, bytes] = {}
    entries = []
    used = 0
    for candidate in candidates:
        side, path = candidate["side"], candidate["path"]
        repository, revision = revisions[side]
        entry = {key: value for key, value in candidate.items() if key != "absent"}
        entry.update(repository=repository, revision=revision, blob_sha=None, mode=None,
                     size_bytes=None, retained_bytes=0, sha256=None, artifact=None)
        status = _policy_status(candidate, config)
        if status is None and repository is None:
            status = "unavailable"
        tree = roots[side]
        if status is None:
            for index, part in enumerate(path.split("/")):
                assert repository is not None and tree is not None
                key = repository, tree
                if key not in trees:
                    if len(trees) >= config.context.max_tree_requests:
                        status = "tree_limit"
                        break
                    trees[key] = client.get_tree(repository, tree)
                node = next((node for node in trees[key] if node["path"] == part), None)
                if node is None:
                    status = "missing"
                    break
                entry.update(blob_sha=node["sha"], mode=node["mode"],
                             size_bytes=node.get("size"))
                if node["mode"] in ("120000", "160000"):
                    status = "symlink" if node["mode"] == "120000" else "submodule"
                    break
                if index < len(path.split("/")) - 1:
                    if node["type"] != "tree":
                        status = "missing"
                        break
                    tree = node["sha"]
                    continue
                if node["type"] != "blob":
                    status = "directory"
                elif node["size"] > config.context.max_file_bytes:
                    status = "oversized"
                elif used + node["size"] > config.context.max_total_bytes:
                    status = "total_limit"
                elif len(retained) >= config.context.max_files:
                    status = "file_limit"
                else:
                    content = client.get_blob(repository, node["sha"],
                                              max_bytes=config.context.max_file_bytes)
                    if len(content) != node["size"]:
                        raise GitHubError("Repository context blob and tree sizes disagree.")
                    try:
                        content.decode("utf-8")
                        if b"\0" in content:
                            raise ValueError
                    except (UnicodeError, ValueError):
                        status = "binary"
                    else:
                        artifact = _artifact(side, path)
                        retained[artifact] = content
                        used += len(content)
                        entry.update(artifact=artifact, retained_bytes=len(content),
                                     sha256=sha256(content).hexdigest())
                        status = "captured"
        entry["status"] = status
        entries.append(entry)
    manifest = {"schema_version": 1, "source": source, "settings": asdict(config.context),
                "revisions": {side: {"repository": repository, "sha": sha,
                                     "tree_sha": roots[side]}
                              for side, (repository, sha) in revisions.items()},
                "entries": entries, "retained_bytes": used,
                "tree_requests": len(trees), "truncated": any(
                    entry["status"] in {"oversized", "total_limit", "file_limit", "tree_limit"}
                    for entry in entries)}
    directory = run / "repository-context"
    directory.mkdir(mode=0o700)
    for name, data in retained.items():
        with (directory / name).open("xb") as stream:
            stream.write(data)
    with (directory / MANIFEST).open("xb") as stream:
        stream.write(encoded(manifest))
    context_contents(run, config)


def _context_contents(run: Path, config: ReviewConfig) -> dict[str, bytes]:
    """Validate immutable context against capture/configuration and return exact copied bytes."""
    if not config.context.enabled:
        return {}
    directory = run / "repository-context"
    manifest_bytes = regular(directory / MANIFEST)
    manifest = _json(directory / MANIFEST)
    metadata = _json(run / "metadata.json")
    candidates = _candidates(_json(run / "files.json"), config)
    # Iterative projections preserve the parent's complete context, separately from child anchors.
    source = run / "context-source-files.json"
    if source.exists() or source.is_symlink():
        candidates = _candidates(_json(source), config)
    if (not isinstance(manifest, dict) or set(manifest) != {
                "schema_version", "source", "settings", "revisions", "entries",
                "retained_bytes", "tree_requests", "truncated"}
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or encoded(manifest.get("settings")) != encoded(asdict(config.context))
            or not isinstance(manifest.get("entries"), list)
            or len(manifest["entries"]) != len(candidates)):
        raise ValueError("Repository context manifest disagrees with capture configuration.")
    provenance = manifest.get("source")
    if (not isinstance(provenance, dict)
            or provenance.get("kind") not in ("github", "corpus_snapshot")):
        raise ValueError("Repository context source provenance is invalid.")
    if provenance["kind"] == "github" and set(provenance) != {"kind"}:
        raise ValueError("Repository context source provenance is invalid.")
    for side in ("base", "head"):
        repository, sha = _revision(metadata["pull_request"], side)
        revision = manifest["revisions"][side]
        if (revision["repository"] != repository or revision["sha"] != sha
                or repository is not None and (
                    not isinstance(revision["tree_sha"], str)
                    or _SHA.fullmatch(revision["tree_sha"]) is None)
                or repository is None and revision["tree_sha"] is not None):
            raise ValueError("Repository context revisions disagree with capture.")
    if provenance["kind"] == "corpus_snapshot":
        snapshots = provenance.get("snapshots")
        if set(provenance) != {"kind", "snapshots"} or not isinstance(snapshots, list):
            raise ValueError("Repository context corpus provenance is invalid.")
        by_identity = {}
        for snapshot in snapshots:
            if (not isinstance(snapshot, dict) or set(snapshot) != {
                    "repository", "revision", "tree_sha", "content_sha256"}
                    or not isinstance(snapshot["content_sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", snapshot["content_sha256"]) is None):
                raise ValueError("Repository context corpus inventory is invalid.")
            key = snapshot["repository"], snapshot["revision"]
            if key in by_identity:
                raise ValueError("Repository context corpus inventory is duplicated.")
            by_identity[key] = snapshot["tree_sha"]
        for revision in manifest["revisions"].values():
            if revision["repository"] is not None and by_identity.get((
                    revision["repository"], revision["sha"])) != revision["tree_sha"]:
                raise ValueError("Repository context corpus identity disagrees with evidence.")
    contents = {MANIFEST: manifest_bytes}
    used = 0
    for candidate, entry in zip(candidates, manifest["entries"]):
        if not isinstance(entry, dict) or set(entry) != {
                "side", "path", "changed_path", "reason", "eligible", "repository", "revision",
                "blob_sha", "mode", "size_bytes", "retained_bytes", "sha256", "artifact", "status"}:
            raise ValueError("Repository context manifest entries are invalid.")
        if any(entry[key] != value or type(entry[key]) is not type(value)
               for key, value in candidate.items() if key != "absent"):
            raise ValueError("Repository context selection changed.")
        revision = manifest["revisions"][entry["side"]]
        if (entry["repository"] != revision["repository"] or entry["revision"] != revision["sha"]
                or entry["status"] not in _STATUSES):
            raise ValueError("Repository context entry identity changed.")
        policy = _policy_status(candidate, config)
        if (policy is not None and entry["status"] != policy
                or policy is None and entry["status"] in {"excluded", "sensitive", "absent"}):
            raise ValueError("Repository context exposure violates selection policy.")
        if (entry["blob_sha"] is not None and (
                not isinstance(entry["blob_sha"], str) or _SHA.fullmatch(entry["blob_sha"]) is None)
                or entry["size_bytes"] is not None and (
                    type(entry["size_bytes"]) is not int or entry["size_bytes"] < 0)
                or type(entry["retained_bytes"]) is not int
                or entry["mode"] not in (None, "100644", "100755", "040000", "120000", "160000")):
            raise ValueError("Repository context object metadata is invalid.")
        if entry["status"] == "captured":
            artifact = _artifact(entry["side"], entry["path"])
            data = regular(directory / artifact)
            data.decode("utf-8")
            blob_sha = sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if (entry["artifact"] != artifact or entry["sha256"] != sha256(data).hexdigest()
                    or entry["blob_sha"] != blob_sha or entry["mode"] not in ("100644", "100755")
                    or entry["size_bytes"] != len(data) or entry["retained_bytes"] != len(data)
                    or len(data) > config.context.max_file_bytes or b"\0" in data):
                raise ValueError("Repository context content changed or exceeded limits.")
            contents[artifact] = data
            used += len(data)
        elif entry["artifact"] is not None or entry["sha256"] is not None or (
                entry["retained_bytes"] != 0):
            raise ValueError("Repository context omission contains exposed content.")
    if (used != manifest["retained_bytes"] or used > config.context.max_total_bytes
            or len(contents) - 1 > config.context.max_files
            or type(manifest.get("tree_requests")) is not int
            or not 0 <= manifest["tree_requests"] <= config.context.max_tree_requests
            or type(manifest.get("truncated")) is not bool
            or manifest["truncated"] != any(entry["status"] in {
                "oversized", "total_limit", "file_limit", "tree_limit"}
                for entry in manifest["entries"])
            or directory.is_symlink() or not directory.is_dir()
            or {p.name for p in directory.iterdir()} != set(contents)):
        raise ValueError("Repository context inventory or limits disagree.")
    return contents


def supporting_dependencies(root: Path) -> list[dict[str, Any]]:
    """Conservatively bind every finding to all exposed content and known omission identities."""
    path = root / MANIFEST
    if not path.exists():
        return []
    manifest = _json(path)
    return [{key: entry[key] for key in ("side", "path", "status", "blob_sha", "sha256", "mode")}
            for entry in manifest["entries"]]


def context_contents(run: Path, config: ReviewConfig) -> dict[str, bytes]:
    """Return the exact verified context, with safe diagnostics for malformed capture data."""
    try:
        return _context_contents(run, config)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise ValueError("Repository context evidence is invalid or incomplete.") from None


def capture_repository_context(client: RepositoryObjectClient, run: Path, config: ReviewConfig,
                               *, source: dict[str, Any] | None = None) -> None:
    """Retain bounded exact-revision evidence or fail with a safe transport diagnostic."""
    try:
        _capture_repository_context(client, run, config, source or {"kind": "github"})
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise GitHubError(
            "Cannot capture immutable repository context; inspect revisions and limits.") from None
