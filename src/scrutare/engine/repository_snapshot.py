"""Frozen corpus source bytes implementing the same exact-object read contract as GitHub."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha1, sha256
from typing import Any

from scrutare.engine.github import GitHubError, _repository
from scrutare.engine.repository_context import _SHA, encoded
from scrutare.findings.models import _path


class RepositorySnapshot:
    """Local revision labels bind immutable source snapshots, not hosted commit authenticity.

    Callers supply bytes from an explicitly loaded corpus. This adapter never reads disk,
    branches, credentials, or labels; paths and content are frozen before review execution.
    """

    def __init__(self, snapshots: Mapping[tuple[str, str], Mapping[str, bytes]]) -> None:
        self._commits: dict[tuple[str, str], str] = {}
        self._trees: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}
        self._blobs: dict[tuple[str, str], bytes] = {}
        self._provenance: list[dict[str, str]] = []
        for (repository, revision), contents in sorted(snapshots.items()):
            _repository(repository)
            if _SHA.fullmatch(revision) is None:
                raise GitHubError("Corpus snapshots require pinned revision labels.")
            root: dict[str, Any] = {}
            inventory = []
            for path, data in sorted(contents.items()):
                _path(path)
                if "\\" in path or not isinstance(data, bytes):
                    raise GitHubError("Corpus snapshots require safe paths and immutable bytes.")
                inventory.append({"path": path, "sha256": sha256(data).hexdigest(),
                                  "size_bytes": len(data)})
                parent = root
                parts = path.split("/")
                for part in parts[:-1]:
                    child = parent.setdefault(part, {})
                    if not isinstance(child, dict):
                        raise GitHubError("Corpus snapshot paths collide.")
                    parent = child
                if parts[-1] in parent:
                    raise GitHubError("Corpus snapshot paths collide.")
                parent[parts[-1]] = data
            tree = self._tree(repository, root)
            self._commits[repository, revision] = tree
            self._provenance.append({"repository": repository, "revision": revision,
                                     "tree_sha": tree,
                                     "content_sha256": sha256(encoded(inventory)).hexdigest()})

    def _tree(self, repository: str, directory: dict[str, Any]) -> str:
        entries: list[dict[str, Any]] = []
        raw = b""
        for name, value in sorted(directory.items(), key=lambda item: (
                item[0] + "/" if isinstance(item[1], dict) else item[0])):
            if isinstance(value, dict):
                sha = self._tree(repository, value)
                entry: dict[str, Any] = {"path": name, "mode": "040000", "type": "tree", "sha": sha}
                mode = b"40000"
            else:
                sha = sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest()
                self._blobs[repository, sha] = value
                entry = {"path": name, "mode": "100644", "type": "blob", "sha": sha,
                         "size": len(value)}
                mode = b"100644"
            raw += mode + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(sha)
            entries.append(entry)
        sha = sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        self._trees[repository, sha] = tuple(entries)
        return sha

    def provenance(self) -> dict[str, Any]:
        return {"kind": "corpus_snapshot", "snapshots": deepcopy(self._provenance)}

    def get_commit(self, repository: str, sha: str) -> str:
        try:
            return self._commits[repository, sha]
        except KeyError:
            raise GitHubError("Corpus snapshot revision is unavailable.") from None

    def get_tree(self, repository: str, sha: str) -> tuple[dict[str, Any], ...]:
        try:
            return deepcopy(self._trees[repository, sha])
        except KeyError:
            raise GitHubError("Corpus snapshot tree is unavailable.") from None

    def get_blob(self, repository: str, sha: str, *, max_bytes: int) -> bytes:
        data = self._blobs.get((repository, sha))
        if data is None or len(data) > max_bytes:
            raise GitHubError("Corpus snapshot blob is unavailable or oversized.")
        return data
