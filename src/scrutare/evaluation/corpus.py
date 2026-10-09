"""Versioned captured corpus with evaluator-only labels outside model input roots."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 1024 * 1024
MAX_SOURCE_ENTRIES = 4096


@dataclass(frozen=True)
class SourceSide:
    side: str
    directory: Path
    repository: str
    revision: str


@dataclass(frozen=True)
class Case:
    id: str
    split: str
    domain: str
    capture: Path
    labels: Path
    context_sources: tuple[SourceSide, ...] = ()


def read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected unlinked regular JSON file')
    return parse_json(path.read_bytes())


def parse_json(data: bytes) -> Any:
    """Reject ambiguous captured identities rather than normalize competing JSON keys."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate corpus JSON key')
            result[key] = value
        return result
    def invalid(value: str) -> None:
        raise ValueError('nonfinite corpus JSON value')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def _confined(root: Path, name: str) -> Path:
    path = root / name
    if path.is_absolute() and not path.resolve().is_relative_to(root):
        raise ValueError('corpus path escapes root')
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('corpus symlinks prohibited')
    return path.resolve()


def load_corpus(root: Path, split: str = 'development') -> tuple[Case, ...]:
    if split not in ('development', 'holdout', 'all'):
        raise ValueError('unknown corpus split')
    root = root.resolve()
    manifest = read_json(root / 'manifest.json')
    if manifest['schema_version'] != 1:
        raise ValueError('unsupported corpus schema')
    label_paths = tuple(_confined(root, entry['labels']) for entry in manifest['cases'])
    cases = []
    seen = set()
    for entry in manifest['cases']:
        identifier = entry['id']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', identifier) or identifier in seen:
            raise ValueError('invalid or duplicate case id')
        seen.add(identifier)
        capture = _confined(root, entry['capture'])
        labels = _confined(root, entry['labels'])
        if labels.is_relative_to(capture) or {p.name for p in capture.iterdir()} != {
                'diff.patch', 'files.json', 'metadata.json'}:
            raise ValueError('capture must contain only public capture artifacts')
        if any(p.is_symlink() or not p.is_file() for p in capture.iterdir()):
            raise ValueError('capture must contain regular files')
        gold = read_json(labels)
        if gold['case_id'] != identifier:
            raise ValueError('label case identity mismatch')
        defect_ids = set()
        for defect in gold['defects']:
            if defect['id'] in defect_ids or any(not defect.get(k) for k in (
                    'id', 'trigger', 'impact', 'evidence', 'match_criteria',
                    'fix_or_counterexample')):
                raise ValueError('incomplete or duplicate defect label')
            defect_ids.add(defect['id'])
        if not entry.get('provenance'):
            raise ValueError('corpus provenance required')
        if entry['split'] not in ('development', 'holdout'):
            raise ValueError('invalid case split')
        if split == 'all' or entry['split'] == split:
            sources = _sources(root, entry.get('context_sources'), capture, label_paths)
            cases.append(Case(identifier, entry['split'], entry['domain'],
                              capture, labels, sources))
    return tuple(cases)


def source_contents(directory: Path) -> dict[str, bytes]:
    """Bound a no-follow inventory; bytes remain outside the model root until selection."""
    if any(p.is_symlink() for p in (directory, *directory.parents)) or not directory.is_dir():
        raise ValueError('source directory must be unlinked')
    result = {}
    total = 0
    entries = 0
    pending = [(directory, 0)]
    while pending:
        parent, depth = pending.pop()
        if depth > 32:
            raise ValueError('source inventory depth bound exceeded')
        with os.scandir(parent) as listing:
            for entry in listing:
                entries += 1
                if entries > MAX_SOURCE_ENTRIES:
                    raise ValueError('source inventory entry bound exceeded')
                path = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError('source symlinks prohibited')
                if entry.is_dir(follow_symlinks=False):
                    pending.append((path, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError('source must contain regular files')
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError('source must contain regular files')
                    if info.st_size > MAX_SOURCE_FILE_BYTES:
                        raise ValueError('source file byte bound exceeded')
                    data = stream.read(MAX_SOURCE_FILE_BYTES + 1)
                total += len(data)
                if len(data) > MAX_SOURCE_FILE_BYTES or total > MAX_SOURCE_BYTES:
                    raise ValueError('source inventory byte bound exceeded')
                result[path.relative_to(directory).as_posix()] = data
    return result


def _sources(root: Path, declaration: Any, capture: Path, labels: tuple[Path, ...]
             ) -> tuple[SourceSide, ...]:
    if declaration is None:
        return ()
    if (not isinstance(declaration, dict) or 'base' not in declaration
            or set(declaration) - {'base', 'head'}):
        raise ValueError('source declaration requires base and optional head')
    metadata = read_json(capture / 'metadata.json')
    result = []
    for side, value in sorted(declaration.items()):
        if not isinstance(value, dict) or set(value) != {'directory', 'repository', 'revision'}:
            raise ValueError('invalid source declaration')
        name = value['directory']
        if (not isinstance(name, str) or name.startswith('/') or '\\' in name
                or '\x00' in name or any(p in ('', '.', '..') for p in name.split('/'))):
            raise ValueError('source directory requires a safe relative path')
        directory = _confined(root, name)
        if (any(label.is_relative_to(directory) for label in labels)
                or capture.is_relative_to(directory)
                or directory.is_relative_to(capture)):
            raise ValueError('source directory overlaps evaluator labels or captures')
        repository, revision = value['repository'], value['revision']
        captured = metadata['pull_request'][side]
        identity = captured.get('repo')
        if (not isinstance(repository, str) or not re.fullmatch(r'[^/\s]+/[^/\s]+', repository)
                or not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision)
                or revision != captured['sha']
                or identity is not None and identity.get('full_name') != repository):
            raise ValueError('source declaration identity or revision mismatch')
        source_contents(directory)
        result.append(SourceSide(side, directory, repository, revision))
    if 'head' not in declaration and metadata['pull_request']['head'].get('repo') is not None:
        raise ValueError('source declaration missing available head')
    return tuple(result)
