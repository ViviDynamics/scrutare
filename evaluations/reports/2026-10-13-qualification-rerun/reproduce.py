"""Recompute this package's gates, automated decisions and tables from public JSON.

No network, provider, credential or private capture is used. Authenticating the
normalized evidence against native sessions needs the retained private captures.
"""
import argparse
import collections
import hashlib
import json
import math
import re
from pathlib import Path

JOBS = ('qualification', 'routing', 'dispute')
# Proxy-advertised gpt-6.1-sol rates retained on 2026-10-13 (per token, US dollars).
INPUT_RATE, OUTPUT_RATE = 2e-06, 1e-05
# One labeled defect per case: (defect id, changed file, lines of the labeled change,
# keyword patterns). These are keyword filters written after the outputs existed and
# confirmed by reading every distinct problem statement. They are not a semantic judge:
# a differently worded wrong finding on the same line could match. Anything they do
# not match is left pending for a person and is never counted as a false positive.
RULES = {
    'security-auth-order': ('security-auth-order-defect', 'api.py', (5,), (
        r'(clear|delet)\w*.*before.*(authori|permi|ownership)',
        r'(authori|permi)\w*.*before.*clear')),
    'tests-consumer-fixture': ('tests-consumer-fixture-defect', 'formatting.py', (2,), (
        r'label.*(test|contract)',)),
    'dev2-01': ('dev2-01-defect', 'src/concurrency/counter.py', (11,), (
        r'outside the lock.*(los|overwrit)', r'overwrit.*outside the lock')),
    'dev2-06': ('dev2-06-defect', 'src/ledger_transaction.py', (16, 17), (
        r'commit.*atomic',)),
    'dev2-09': ('dev2-09-defect', 'src/jobs/dispatcher.py', (11,), (
        r'outside the lock.*(same job|duplicate)', r'concurrent claims.*both succeed')),
    'dev2-10': ('dev2-10-defect', 'src/billing/orders.py', (6,), (r'delet\w*.*line items',)),
}


def read(path):
    return json.loads(path.read_text())


def finding_id(run, index):
    """Same identity as scrutare.evaluation.scoring.finding_id."""
    value = [run['run_id'], index, run['findings'][index]]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def decide(run, index):
    """Return the matched defect id, or None when no rule applies (left pending)."""
    finding = run['findings'][index]
    rule = RULES.get(run['case_id'])
    if rule is None:
        return None
    defect, path, lines, patterns = rule
    text = finding['problem'].lower()
    if (finding['file'] == path and finding['side'] == 'RIGHT' and finding['line'] in lines
            and any(re.search(pattern, text) for pattern in patterns)):
        return defect
    return None


def decisions(package):
    rows = []
    for job in JOBS:
        for run in read(package / job / 'experiment.json')['runs']:
            for index in range(len(run['findings'])):
                defect = decide(run, index)
                if defect is not None:
                    rows.append({'job': job, 'finding_id': finding_id(run, index),
                                 'defect_id': defect, 'reviewer': 'reproduce.py RULES',
                                 'method': 'automated', 'ambiguous': False,
                                 'rationale': 'anchored on the labeled changed line; '
                                              'problem statement passes the keyword filter'})
    return rows


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def nearest_rank(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)] if ordered else None


def gate_tests(package, job):
    gate = read(package / job / 'gate.json')
    experiment = read(package / job / 'experiment.json')
    cells = read(package / job / 'mechanical-qualification.json')['cells']
    expected = {f'{case}/{arm}/{repeat}' for case in gate['cases'] for arm in gate['arms']
                for repeat in range(1, gate['repeats'] + 1)}
    runs = {row['run_id']: row for row in experiment['runs']}
    by_cell = {cell['run_id']: cell for cell in cells}
    if len(experiment['runs']) != len(runs) or len(cells) != len(by_cell):
        raise ValueError('duplicate run identity')
    for identity, cell in by_cell.items():
        row = runs[identity]
        if (cell['status'] != row['status'] or cell['tokens'] != row['usage']['total']
                or cell['accounting_complete'] != row['accounting_complete']
                or cell['findings'] != len(row['findings'])
                or len(cell['attempts']) != len(row.get('attempts') or [1])):
            raise ValueError('normalized cell disagrees with public experiment')
    bugs = set(gate['cases']) - set(gate['clean_cases'])

    def arm(name):
        return [c for c in cells if c['run_id'].split('/')[0] in bugs
                and c['run_id'].split('/')[1] == name]
    policy = gate['transient_retry']
    tests = {
        'matrix_exact': set(runs) == expected == set(by_cell)
        and len(expected) == gate['expected_runs'],
        'experiment_terminal': experiment['status'] == 'complete',
        'all_runs_complete_and_accounted': all(
            c['status'] == 'complete' and c['accounting_complete'] for c in cells),
        'all_attempts_within_ceiling': all(a['tokens'] <= gate['max_tokens_per_run']
                                           for c in cells for a in c['attempts']),
        'retries_within_declared_policy': all(
            len(c['attempts']) - 1 <= policy['per_cell'] and all(
                a['transient_provider_failures'] for a in c['attempts'][:-1])
            for c in cells) and sum(len(c['attempts']) - 1 for c in cells) <= policy[
                'job_budget']}
    if 'aggregate_defect_case_repeats_nonempty_valid_v2' in gate:
        rows = arm('aggregate')
        tests['aggregate_bug_cells_nonempty_valid_v2'] = (
            len(rows) == gate['aggregate_defect_case_repeats_nonempty_valid_v2'] and all(
                c['findings'] > 0 and not c['finding_validation_errors'] for c in rows))
    if 'assessed_defect_case_repeats_actual_complete_assessor' in gate:
        rows = arm('assessed')
        tests['assessed_bug_cells_actual_complete_assessor'] = (
            len(rows) == gate['assessed_defect_case_repeats_actual_complete_assessor'] and all(
                c['assessment_status'] == 'complete' and c['assessor_complete'] for c in rows))
    return gate, tests, cells, runs


def summarize(package, job, matched):
    gate, tests, cells, runs = gate_tests(package, job)
    retained = read(package / job / 'mechanical-qualification.json')
    if tests != retained['tests']:
        raise ValueError('recomputed gate disagrees with retained mechanical summary')
    clean = set(gate['clean_cases'])
    arms = {}
    for name in gate['arms']:
        selected = [c for c in cells if c['run_id'].split('/')[1] == name]
        counts = collections.Counter()
        per_case = {}
        for cell in selected:
            case = cell['run_id'].split('/')[0]
            run = runs[cell['run_id']]
            ids = [finding_id(run, i) for i in range(len(run['findings']))]
            hit = sum(identity in matched for identity in ids)
            usable = cell['status'] == 'complete' and cell['accounting_complete']
            counts['matched_findings'] += hit
            counts['pending_findings'] += len(ids) - hit
            row = per_case.setdefault(case, {'cells': 0, 'complete': 0, 'detected': 0,
                                             'cells_with_findings': 0, 'tokens': [],
                                             'latency_seconds': [], 'reviewers': set(),
                                             'specialists': set()})
            row['cells'] += 1
            row['complete'] += usable
            row['detected'] += bool(usable and hit)
            row['cells_with_findings'] += bool(ids)
            row['tokens'].append(cell['tokens'])
            row['latency_seconds'].append(round(cell['latency_seconds'], 3))
            row['reviewers'].add(len(cell['effective_personas']))
            row['specialists'].update(cell['routing_activated'])
            if case in clean:
                counts['clean_cells'] += 1
                counts['clean_cells_usable'] += usable
                counts['clean_cells_with_findings'] += bool(usable and ids)
            else:
                counts['defect_cells'] += 1
                counts['defect_cells_detected'] += bool(usable and hit)
        for row in per_case.values():
            row['reviewers'] = sorted(row['reviewers'])
            row['specialists'] = sorted(row['specialists'])
            row['mean_tokens'] = round(sum(row['tokens']) / len(row['tokens']), 1)
            row['mean_latency_seconds'] = round(
                sum(row['latency_seconds']) / len(row['latency_seconds']), 2)
        tokens = [c['tokens'] for c in selected]
        latency = [c['latency_seconds'] for c in selected]
        failed = sum(c['status'] != 'complete' for c in selected)
        usage = collections.Counter()
        for cell in selected:
            usage.update({k: v for k, v in cell['usage'].items() if k != 'total'})
        judged = counts['matched_findings']
        arms[name] = {
            'cells': len(selected), 'complete_and_accounted': sum(
                c['status'] == 'complete' and c['accounting_complete'] for c in selected),
            'not_complete': failed, 'not_complete_rate_wilson95': wilson(failed, len(selected)),
            'not_complete_reasons': dict(collections.Counter(
                s['reason'] for c in selected if c['status'] != 'complete'
                for s in c['session_statuses'] if s['status'] != 'complete')),
            'cells_needing_rerun': sum(len(c['attempts']) > 1 for c in selected),
            'findings': sum(c['findings'] for c in selected),
            'automated_matched_findings': judged,
            'pending_human_findings': counts['pending_findings'],
            'defect_cells': counts['defect_cells'],
            'defect_cells_detected': counts['defect_cells_detected'],
            'provisional_defect_recall': (round(counts['defect_cells_detected']
                                                / counts['defect_cells'], 4)
                                          if counts['defect_cells'] else None),
            'provisional_defect_recall_wilson95': wilson(counts['defect_cells_detected'],
                                                         counts['defect_cells']),
            'duplicate_matched_findings': judged - counts['defect_cells_detected'],
            'clean_cells': counts['clean_cells'],
            'clean_cells_with_findings': counts['clean_cells_with_findings'],
            'clean_false_positive_rate_wilson95': wilson(counts['clean_cells_with_findings'],
                                                         counts['clean_cells_usable']),
            'total_tokens': sum(tokens), 'mean_tokens': round(sum(tokens) / len(tokens), 1),
            'max_tokens': max(tokens), 'usage': dict(usage),
            'mean_latency_seconds': round(sum(latency) / len(latency), 2),
            'p95_latency_seconds_nearest_rank': round(nearest_rank(latency, 0.95), 2),
            'actual_assessor_calls': sum(c['assessor_actual'] for c in selected),
            'complete_assessor_calls': sum(c['assessor_complete'] for c in selected),
            'discussion_attempted_rounds': sum(c['discussion_attempted_rounds']
                                               for c in selected),
            'discussion_completed_rounds': sum(c['discussion_completed_rounds']
                                               for c in selected),
            'cells_with_specialist': sum(bool(c['routing_activated']) for c in selected),
            'per_case': per_case}
    usage = collections.Counter()
    for cell in cells:
        for attempt_usage in [cell['usage']]:
            usage.update({k: v for k, v in attempt_usage.items() if k != 'total'})
    attempts = sum(len(c['attempts']) for c in cells)
    return {
        'job': job, 'tests': tests,
        'all_gate_tests_pass': all(tests.values()),
        'status_counts': dict(collections.Counter(c['status'] for c in cells)),
        'first_attempt_status_counts': dict(collections.Counter(
            c['first_attempt_status'] for c in cells)),
        'rerun_attempts': attempts - len(cells), 'arms': arms,
        'total_tokens_final_attempts': sum(c['tokens'] for c in cells),
        'retry_tokens': sum(c['retry_tokens'] for c in cells), 'usage': dict(usage),
        'nominal_ceiling_estimate_usd': round(
            attempts * gate['max_tokens_per_run'] * OUTPUT_RATE, 2),
        'observed_usage_upper_estimate_usd': round(
            (usage['input'] + usage['cache_read'] + usage['cache_write']) * INPUT_RATE
            + usage['output'] * OUTPUT_RATE, 2)}


def paired(summary, baseline, candidate):
    """Case-level differences between two arms of one job; means over three repeats."""
    rows = {}
    for case, base in summary['arms'][baseline]['per_case'].items():
        other = summary['arms'][candidate]['per_case'][case]
        rows[case] = {
            'detected': {baseline: f"{base['detected']}/{base['cells']}",
                         candidate: f"{other['detected']}/{other['cells']}"},
            'complete': {baseline: base['complete'], candidate: other['complete']},
            'mean_tokens': {baseline: base['mean_tokens'], candidate: other['mean_tokens']},
            'token_ratio': round(other['mean_tokens'] / base['mean_tokens'], 3),
            'mean_latency_seconds': {baseline: base['mean_latency_seconds'],
                                     candidate: other['mean_latency_seconds']},
            'latency_ratio': round(other['mean_latency_seconds']
                                   / base['mean_latency_seconds'], 3),
            'specialists': other['specialists']}
    ratios = [row['token_ratio'] for row in rows.values()]
    return {'baseline': baseline, 'candidate': candidate, 'cases': rows,
            'token_ratio_range': [min(ratios), max(ratios)],
            'detection_difference_cells': sum(
                int(r['detected'][candidate].split('/')[0])
                - int(r['detected'][baseline].split('/')[0]) for r in rows.values())}


def build(package):
    rows = decisions(package)
    matched = {row['finding_id'] for row in rows}
    summaries = {job: summarize(package, job, matched) for job in JOBS}
    return rows, {
        'schema_version': 1, 'judgments': 'automated keyword filters, provisional',
        'actual_human_judgments': 0, 'holdout_used': False, 'defaults_promoted': False,
        'automated_decisions': len(rows),
        'pending_human_findings': sum(a['pending_human_findings'] for s in summaries.values()
                                      for a in s['arms'].values()),
        'jobs': summaries,
        'paired': {
            'assessment_vs_aggregate': paired(summaries['qualification'], 'aggregate',
                                              'assessed'),
            'one_round_vs_assessment': paired(summaries['qualification'], 'assessed',
                                              'focused1'),
            'three_rounds_vs_one': paired(summaries['qualification'], 'focused1', 'focused3'),
            'routing_vs_fixed': paired(summaries['routing'], 'evidence', 'routing'),
            'dispute_case_assessment_vs_aggregate': paired(summaries['dispute'], 'aggregate',
                                                           'assessed')},
        'spend': {
            'method': 'attempts x configured ceiling x advertised output rate',
            'qualification': summaries['qualification']['nominal_ceiling_estimate_usd'],
            'aborted_routing_three_started_cells': 3.0,
            'routing': summaries['routing']['nominal_ceiling_estimate_usd'],
            'dispute': summaries['dispute']['nominal_ceiling_estimate_usd'],
            'total_nominal_usd': round(3.0 + sum(
                s['nominal_ceiling_estimate_usd'] for s in summaries.values()), 2),
            'limit_usd': 75.0,
            'observed_usage_estimate_usd': round(sum(
                (s['usage']['input'] + s['usage']['cache_read'] + s['usage']['cache_write'])
                * INPUT_RATE + s['usage']['output'] * OUTPUT_RATE
                for s in summaries.values()), 2),
            'observed_usage_note': 'completed jobs only, all cached and uncached input at the '
                                   'advertised input rate; in-flight calls of the aborted job '
                                   'are unknown; no invoice or billed amount is asserted'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = args.package
    for name, expected in read(package / 'published-sha256.json').items():
        path = package / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'public artifact hash mismatch: {name}')
    rows, summary = build(package)
    if rows != read(package / 'automated-decisions.json'):
        raise ValueError('recomputed automated decisions differ from the published ones')
    if summary != read(package / 'summary.json'):
        raise ValueError('recomputed summary differs from the published one')
    diagnosis = read(package / 'residual-diagnosis.json')
    if (sum(t['eligible_cells'] for t in diagnosis['trials']) != 6
            or sum(t['cells'] for t in diagnosis['trials']) != 180):
        raise ValueError('residual diagnosis totals changed')
    args.output.write_text(json.dumps(summary, sort_keys=True, separators=(',', ':')) + '\n')
    print(json.dumps({'verified_artifacts': len(read(package / 'published-sha256.json')),
                      'automated_decisions': len(rows),
                      'qualification': summary['jobs']['qualification']['all_gate_tests_pass']}))


if __name__ == '__main__':
    main()
