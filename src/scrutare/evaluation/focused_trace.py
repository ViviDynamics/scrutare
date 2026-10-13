"""Export focused phase evidence without private native session directory metadata."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from scrutare.evaluation.corpus import parse_json


def discussion_trace(directory: Path) -> dict[str, Any]:
    """Missing/invalid artifacts remain unknown rather than successful zero-round work."""
    result: dict[str, Any] = {'availability': 'unavailable', 'artifact': 'debate.json',
                             'artifact_sha256': None, 'rounds': None}
    try:
        path = directory / 'debate.json'
        if path.is_symlink() or not path.is_file():
            return result
        raw = path.read_bytes()
        result['artifact_sha256'] = hashlib.sha256(raw).hexdigest()
        document = parse_json(raw)
        if (document['schema_version'] != 2
                or document['kind'] != 'focused_evidence_discussion'):
            return result
        rounds = []
        for row in document['rounds']:
            if (type(row['round']) is not int or row['round'] <= 0
                    or type(row['tokens']) is not int or row['tokens'] < 0
                    or type(row['latency_seconds']) not in (int, float)
                    or not math.isfinite(row['latency_seconds']) or row['latency_seconds'] < 0):
                return result
            rounds.append({key: row[key] for key in (
                'round', 'candidate_ids', 'tokens', 'latency_seconds', 'new_citations',
                'proposals', 'disposition_changes')})
        result.update(availability='recorded', status=document['status'],
                      reason=document['reason'],
                      bypassed_candidate_ids=document['bypassed_candidate_ids'],
                      rounds=rounds, final_assessments=document['final_assessments'])
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result
