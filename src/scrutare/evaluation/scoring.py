"""Explicit human match adjudication; no model judge or inferred clean successes."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any


def finding_id(run: dict[str, Any], index: int) -> str:
    """Bind a match to run identity, ordinal and exact finding contents."""
    value = [run['run_id'], index, run['findings'][index]]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def adjudication_packet(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Blind model, variant, repeat and perspective; preserve semantic evidence."""
    items = [{'finding_id': finding_id(run, i), 'case_id': run['case_id'],
              'finding': {k: v for k, v in finding.items() if k != 'persona'}}
             for run in runs for i, finding in enumerate(run['findings'])]
    return {'schema_version': 1, 'items': sorted(items, key=lambda item: item['finding_id'])}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_experiment(runs: list[dict[str, Any]], labels: dict[str, Any],
                     decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Score per variant with failed runs in recall denominators and explicit pending counts.

    A null defect_id means adjudicated false positive. No decision means pending.
    TP finding precision includes corroborations; recall and cost count distinct defects
    per case/repeat. Clean FP rate uses only complete accounted fully judged runs.
    """
    identities = [run['run_id'] for run in runs]
    if len(set(identities)) != len(identities):
        raise ValueError('duplicate run identity')
    known = {finding_id(run, i): (run, finding) for run in runs
             for i, finding in enumerate(run['findings'])}
    matches: dict[str, str | None] = {}
    for decision in decisions:
        key = decision['finding_id']
        if key not in known:
            raise ValueError('unknown finding identity')
        if key in matches:
            raise ValueError('duplicate decision')
        if not decision.get('reviewer') or not decision.get('rationale'):
            raise ValueError('human reviewer and rationale required')
        run, _ = known[key]
        defect = decision['defect_id']
        if defect is not None and defect not in {d['id'] for d in labels[run['case_id']]['defects']}:
            raise ValueError('unknown defect match')
        matches[key] = defect
    results = {}
    for variant in sorted({run['variant'] for run in runs}):
        selected = [run for run in runs if run['variant'] == variant]
        counts: Counter[str] = Counter()
        unique: Counter[str] = Counter()
        latency = []
        for run in selected:
            defects = labels[run['case_id']]['defects']
            counts['possible_defects'] += len(defects)
            counts[f"{run['status']}_runs"] += 1
            counts['accounting_failures'] += not run['accounting_complete']
            counts['tokens'] += run['usage']['total']
            latency.append(run['latency_seconds'])
            detections: dict[str, set[str]] = defaultdict(set)
            pending = 0
            false = 0
            usable = run['status'] in ('complete', 'partial') and run['accounting_complete']
            for i, finding in enumerate(run['findings']):
                unique[finding['persona']] += 0
                key = finding_id(run, i)
                if key not in matches:
                    pending += 1
                elif matches[key] is None:
                    false += 1
                    counts['false_positive_findings'] += 1
                elif usable:
                    counts['true_positive_findings'] += 1
                    detections[str(matches[key])].add(finding['persona'])
            counts['pending_findings'] += pending
            counts['true_positive_defects'] += len(detections)
            judged_tp = sum(1 for i in range(len(run['findings']))
                            if matches.get(finding_id(run, i)) is not None) if usable else 0
            counts['duplicates'] += judged_tp - len(detections)
            for personas in detections.values():
                if len(personas) == 1:
                    unique[next(iter(personas))] += 1
            if not defects and run['status'] == 'complete' and usable and not pending:
                counts['eligible_clean_runs'] += 1
                counts['clean_false_positive_runs'] += false > 0
        judged = counts['true_positive_findings'] + counts['false_positive_findings']
        results[variant] = {
            'finding_precision': _ratio(counts['true_positive_findings'], judged),
            'defect_recall': _ratio(counts['true_positive_defects'], counts['possible_defects']),
            'clean_pr_false_positive_rate': _ratio(counts['clean_false_positive_runs'],
                                                  counts['eligible_clean_runs']),
            'duplicate_rate': _ratio(counts['duplicates'], judged),
            'tokens_per_true_positive': _ratio(counts['tokens'], counts['true_positive_defects']),
            'unique_detections': dict(sorted(unique.items())),
            'latency_seconds': latency,
            **{key: counts[key] for key in ('complete_runs', 'partial_runs', 'failed_runs',
                'missing_runs', 'invalid_runs', 'accounting_failures', 'tokens',
                'pending_findings', 'true_positive_defects', 'possible_defects',
                'eligible_clean_runs')},
        }
    return results
