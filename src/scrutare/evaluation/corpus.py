"""Versioned captured corpus with evaluator-only labels outside model input roots."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Case:
    id: str
    split: str
    domain: str
    capture: Path
    labels: Path


def read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected unlinked regular JSON file')
    return json.loads(path.read_bytes())


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
            cases.append(Case(identifier, entry['split'], entry['domain'], capture, labels))
    return tuple(cases)
