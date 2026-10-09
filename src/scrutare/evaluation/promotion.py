"""Frozen product promotion policy; explicit evidence declarations, no default mutation."""
from __future__ import annotations

import math
from typing import Any

POLICY = {'minimum_gain_percentage_points': 5, 'maximum_other_loss_percentage_points': 2,
          'maximum_tokens_increase_fraction': .2, 'maximum_p95_increase_fraction': .2,
          'maximum_new_critical_security_misses': 0}


def _finite(value: Any, *, low: float, high: float = math.inf) -> bool:
    return (type(value) in (int, float) and math.isfinite(value) and low <= value <= high)


def evaluate_promotion(baseline: dict[str, Any], candidate: dict[str, Any],
                       evidence: dict[str, Any]) -> dict[str, Any]:
    """Evaluate complete scorer metrics and independently supplied verification evidence.

    References and booleans record caller declarations; this function does not authenticate
    a human or infer statistical adequacy. Missing evidence is inconclusive, never a pass.
    """
    result: dict[str, Any] = {'schema_version': 1, 'policy': dict(POLICY),
                             'defaults_changed': False, 'evidence': dict(evidence)}
    uncertain = []
    for guard in ('holdout_unseen', 'independent_human_verification', 'uncertainty_sufficient',
                  'same_model_rail', 'equal_total_token_ceiling', 'same_case_repeat_schedule'):
        if evidence.get(guard) is not True:
            uncertain.append(guard)
    for reference in ('verification_reference', 'holdout_reference'):
        if not isinstance(evidence.get(reference), str) or not evidence[reference].strip():
            uncertain.append(reference)
    if evidence.get('evidence_kind') != 'model':
        uncertain.append('real_model_measurements_required')
    if baseline.get('complete_runs') != candidate.get('complete_runs'):
        uncertain.append('unequal_run_denominators')
    misses = evidence.get('new_critical_security_misses')
    if type(misses) is not int or misses < 0:
        uncertain.append('unknown_new_critical_security_misses')
    percentiles: list[float | None] = []
    for name, metrics in (('baseline', baseline), ('candidate', candidate)):
        if metrics.get('quality_final') is not True:
            uncertain.append(name + '_quality_not_final')
        for count in ('pending_findings', 'accounting_failures', 'missing_runs', 'failed_runs',
                      'partial_runs', 'invalid_runs'):
            if type(metrics.get(count)) is not int or metrics[count] != 0:
                uncertain.append(name + '_' + count)
        if not _finite(metrics.get('complete_runs'), low=1):
            uncertain.append(name + '_complete_runs')
        for metric in ('finding_precision', 'defect_recall'):
            if not _finite(metrics.get(metric), low=0, high=1):
                uncertain.append(name + '_' + metric)
        if not _finite(metrics.get('tokens'), low=1):
            uncertain.append(name + '_tokens')
        latency = metrics.get('latency_seconds')
        if (not isinstance(latency, list) or not latency
                or not all(_finite(v, low=0) for v in latency)
                or len(latency) != metrics.get('complete_runs')):
            uncertain.append(name + '_latency')
            percentiles.append(None)
        else:
            percentiles.append(sorted(latency)[math.ceil(.95 * len(latency)) - 1])
    if percentiles[0] == 0:
        uncertain.append('baseline_zero_latency')
    if uncertain:
        return {**result, 'status': 'inconclusive', 'reasons': uncertain}
    baseline_p95, candidate_p95 = percentiles
    assert baseline_p95 is not None and candidate_p95 is not None
    precision_gain = candidate['finding_precision'] - baseline['finding_precision']
    recall_gain = candidate['defect_recall'] - baseline['defect_recall']
    tolerance = 1e-12
    improvement = ((recall_gain >= .05 - tolerance and precision_gain >= -.02 - tolerance)
                   or (precision_gain >= .05 - tolerance and recall_gain >= -.02 - tolerance))
    reasons = []
    if not improvement:
        reasons.append('quality_improvement_below_policy')
    if misses != 0:
        reasons.append('new_critical_security_miss')
    if candidate['tokens'] > baseline['tokens'] * 1.2 + tolerance:
        reasons.append('tokens_above_policy')
    if candidate_p95 > baseline_p95 * 1.2 + tolerance:
        reasons.append('p95_latency_above_policy')
    return {**result, 'status': 'fail' if reasons else 'pass', 'reasons': reasons,
            'measurements': {'precision_gain_percentage_points': precision_gain * 100,
                'recall_gain_percentage_points': recall_gain * 100,
                'baseline_p95_seconds': percentiles[0], 'candidate_p95_seconds': percentiles[1]}}
