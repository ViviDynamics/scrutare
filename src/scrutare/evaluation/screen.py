"""A short development screen a candidate rail passes before a full qualification is frozen.

A screen is a fixed manifest: a development corpus, the cases it runs and the gate it
must pass. It runs as an ordinary rule A job of one arm, so it fails the way a
qualification would, in about six cells instead of thirty-six. It never reads a holdout
and never promotes anything; passing only makes a candidate eligible to be frozen.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from scrutare.evaluation.corpus import Case, read_json

SCREEN_GATE = 'every cell complete with complete accounting, under rule A'


def load_screen(path: Path, corpus: Path) -> dict[str, Any]:
    """The manifest's record for the snapshot; refuses a corpus other than its own."""
    data = path.read_bytes()
    manifest = read_json(path)
    cases = manifest.get('cases')
    if (manifest.get('schema_version') != 1 or not isinstance(manifest.get('id'), str)
            or not isinstance(cases, list) or not cases
            or not all(isinstance(case, str) for case in cases)
            or len(set(cases)) != len(cases) or manifest.get('gate') != SCREEN_GATE):
        raise ValueError('a screen manifest needs schema 1, an id, distinct cases and the '
                         'screen gate')
    digest = hashlib.sha256((corpus / 'manifest.json').read_bytes()).hexdigest()
    if digest != manifest.get('corpus_manifest_sha256'):
        raise ValueError(f"screen {manifest['id']} is bound to another corpus manifest")
    return {'id': manifest['id'], 'sha256': hashlib.sha256(data).hexdigest(),
            'cases': list(cases), 'gate': SCREEN_GATE}


def screen_cases(cases: tuple[Case, ...], screen: dict[str, Any]) -> tuple[Case, ...]:
    """The screen's development cases in corpus order; a missing or holdout case refuses."""
    by_id = {case.id: case for case in cases}
    missing = [case for case in screen['cases'] if case not in by_id]
    if missing:
        raise ValueError('screen cases missing from the corpus: ' + ', '.join(missing))
    chosen = tuple(case for case in cases if case.id in set(screen['cases']))
    if any(case.split != 'development' for case in chosen):
        raise ValueError('a screen runs development cases only')
    return chosen


def screen_gate(experiment: Path) -> dict[str, Any]:
    """Pass only when every frozen cell completed with complete accounting."""
    snapshot = read_json(experiment / 'snapshot.json')
    result = read_json(experiment / 'experiment.json')
    screen = snapshot.get('screen')
    if not isinstance(screen, dict) or snapshot.get('qualification') is None:
        raise ValueError('not a screen: the snapshot records no screen under rule A')
    if len(snapshot['variant_selection']) != 1:
        raise ValueError('a screen runs exactly one arm')
    runs = result['runs']
    failed = sorted(row['run_id'] for row in runs if row['status'] != 'complete'
                    or row.get('accounting_complete') is not True)
    expected = len(screen['cases']) * 3
    passed = (result['status'] == 'complete' and len(runs) == expected and not failed)
    return {'schema_version': 1, 'screen': screen, 'variant': snapshot['variant_selection'][0],
            'cells': len(runs), 'expected_cells': expected,
            'complete': len(runs) - len(failed), 'failed_cells': failed,
            'experiment_status': result['status'],
            'decision': 'pass' if passed else 'fail'}
