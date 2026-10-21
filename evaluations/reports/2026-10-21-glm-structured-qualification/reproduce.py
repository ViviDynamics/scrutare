"""Recompute this package's gate, cause breakdown, automated decisions and comparison
from public JSON.

No network, provider, credential or private capture is used. Whether an assessor
actually ran and whether evidence validated are read from
mechanical-qualification.json, produced from the private captures with the measured
wheel; authenticating them needs those captures. The per-session diagnoses are derived
from the private native records too; here they are checked against the mechanical cells
and counted, not authenticated.
"""
import argparse
import collections
import hashlib
import json
import math
import re
import statistics
from pathlib import Path

# Judged frozen schedules of spark/glm-5.3-flash with structured_output in this package.
JOBS = ('s1',)
# Freeze hashes posted on issue #78 before s1's first call (comment 6307641005). They
# tie this package to a record outside it.
POSTED_ON_78 = {
    's1': {'preparation.json': '8a35936426833c293ac5dfed3553b8c9967df97daa4c7028aba0cd6840fc2673',
           'gate.json': '1b55a7bdcee3d95e62ed35d5381d27d59abf37fdb119732d02354418175c311e',
           'evaluation.json': 'ab0d1fe4f6f7ab387bea76ff1cdfc3afc008bd8bcec5083654acba73511d4847'},
}
# The g2 schedule (constraint off, nare 2026.10.4), read from its published package and
# checked against that package's own hashes.
PREDECESSOR = {'g2': ('2026-10-14-glm-qualification', 'g2')}
# The gpt-6.1-sol schedules of the reference profile, read from sibling packages and
# checked against those packages' own published hashes. Different source commit
# (2f1e95c), different day (2026-10-13), a 120-second session timeout and no constraint.
REFERENCE = {
    'sol-r1': ('2026-10-13-qualification-rerun', 'qualification'),
    'sol-r2': ('2026-10-13-tools-reliability-separating', 'reliability-r2'),
    'sol-r3': ('2026-10-13-tools-reliability-separating', 'reliability-r3'),
}
# Copied unchanged from ../2026-10-13-qualification-rerun/reproduce.py (merged on
# 2026-10-13, before any spark/glm-5.3-flash call), so for this model they are
# prospective. They are keyword filters, not a semantic judge: a differently worded
# correct finding stays unmatched and is left pending for a person, never scored false.
RULES = {
    'security-auth-order': ('security-auth-order-defect', 'api.py', (5,), (
        r'(clear|delet)\w*.*before.*(authori|permi|ownership)',
        r'(authori|permi)\w*.*before.*clear')),
    'tests-consumer-fixture': ('tests-consumer-fixture-defect', 'formatting.py', (2,), (
        r'label.*(test|contract)',)),
}


def read(path):
    return json.loads(path.read_text())


def finding_id(run, index):
    """Same identity as scrutare.evaluation.scoring.finding_id."""
    value = [run['run_id'], index, run['findings'][index]]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def decide(run, index):
    finding = run['findings'][index]
    rule = RULES.get(run['case_id'])
    if rule is None:
        return None
    defect, path, lines, patterns = rule
    if (finding['file'] == path and finding['side'] == 'RIGHT' and finding['line'] in lines
            and any(re.search(p, finding['problem'].lower()) for p in patterns)):
        return defect
    return None


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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_hashes(package):
    manifest = read(package / 'published-sha256.json')
    # Only the interpreter cache beside reproduce.py itself is exempt.
    present = {p.relative_to(package).as_posix() for p in package.rglob('*')
               if p.is_file() and p.relative_to(package).parts[0] != '__pycache__'}
    if present - {'published-sha256.json'} != set(manifest):
        raise ValueError(f'files outside the hash manifest: {package.name}')
    for name, expected in manifest.items():
        path = package / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'public artifact hash mismatch: {package.name}/{name}')
    return len(manifest)


def cross_hashes(package):
    """Hashes that must agree across files that could each be regenerated alone."""
    record = read(package / 'freeze-record.json')['freezes']
    for job in JOBS:
        directory = package / job
        prep = read(directory / 'preparation.json')
        for name in ('gate.json', 'evaluation.json'):
            if prep['input_sha256'][name] != sha(directory / name):
                raise ValueError(f'{job}/{name} differs from its frozen preparation')
        posted = POSTED_ON_78[job]
        frozen = record[job]
        original = read(directory / 'original-artifact-sha256.json')
        for name in ('preparation.json', 'gate.json', 'evaluation.json'):
            digest = sha(directory / name)
            if (digest != posted[name] or digest != original[name]
                    or digest != frozen[name.replace('.json', '_sha256')]):
                raise ValueError(f'{job}/{name} differs from the hash posted on #78')
        if frozen['wheel_sha256'] != next(
                v for k, v in prep['input_sha256'].items() if k.endswith('.whl')):
            raise ValueError(f'{job} wheel differs from its freeze record')
        if read(directory / 'evaluation.json')['models']['default'].get(
                'structured_output') != 'json_schema' or prep['structured_output'] != 'json_schema':
            raise ValueError(f'{job} did not freeze the constraint')
        mechanical = read(directory / 'mechanical-qualification.json')
        if (mechanical['gate_sha256'] != sha(directory / 'gate.json')
                or mechanical['experiment_sha256'] != original['experiment/experiment.json']):
            raise ValueError(f'{job} mechanical summary is not bound to its gate and experiment')


ASSESSOR = 'evidence-assessor'


def cause(row):
    """One cause per session that did not complete, from the recorded reasons only."""
    error = row['nare_error'] or ''
    if row['reason'] == 'timeout':
        return 'timeout_600s'
    if row['reason'] == 'budget':
        return ('budget_overrun_assessor' if row['persona'] == ASSESSOR
                else 'budget_overrun_discovery')
    if error.startswith('answer does not satisfy the schema'):
        return 'schema_failure'
    if 'reasoning but no answer' in error:
        return 'nare_error_reasoning_without_answer'
    if row['reason'] == 'citation_validation':
        return 'citation_past_file_end'
    return f"other:{row['reason']}"


def diagnosis(path, cells):
    """Check a per-session diagnosis against the mechanical cells, then count causes."""
    document = read(path)
    rows = document['sessions']
    by_cell = collections.defaultdict(list)
    for row in rows:
        by_cell[row['cell']].append(row)
    if set(by_cell) != {c['run_id'] for c in cells}:
        raise ValueError(f'session diagnosis cells disagree with the mechanical cells: {path.name}')
    for cell in cells:
        sessions = by_cell[cell['run_id']]
        discovery = sorted((r['persona'], r['status'], r['reason'], r['tokens'])
                           for r in sessions if r['persona'] != ASSESSOR)
        recorded = sorted((s['persona'], s['status'], s['reason'], s['tokens'])
                          for s in cell['session_statuses'])
        assessors = [r for r in sessions if r['persona'] == ASSESSOR]
        if discovery != recorded or len(assessors) != int(cell['assessor_actual']) or any(
                (r['reason'], r['tokens'], r['status'] == 'complete')
                != (cell['assessor_reason'], cell['assessor_tokens'], cell['assessor_complete'])
                for r in assessors):
            raise ValueError(f"session diagnosis disagrees with cell {cell['run_id']}")
    constrained = [r for r in rows if (r['constrained_turns'] or 0) > 0]
    assessors = [r for r in rows if r['persona'] == ASSESSOR]
    return {
        'sessions': len(rows), 'discovery_sessions': len(rows) - len(assessors),
        'assessor_sessions': len(assessors),
        'complete': sum(r['status'] == 'complete' for r in rows),
        'not_complete_by_cause': dict(sorted(collections.Counter(
            cause(r) for r in rows if r['status'] != 'complete').items())),
        'sessions_requesting_constraint': sum(r['structured_output_mode'] == 'json_schema'
                                              for r in rows),
        'sessions_with_a_constrained_turn': len(constrained),
        'constrained_turns_total': sum(r['constrained_turns'] or 0 for r in rows),
        'constrained_sessions_by_outcome': dict(sorted(collections.Counter(
            'complete' if r['status'] == 'complete' else cause(r) for r in constrained).items())),
        'constrained_sessions_failing_schema': sum(cause(r) == 'schema_failure'
                                                   for r in constrained),
        'assessor_tokens_vs_allocation': [
            [c['run_id'], c['assessor_tokens'], c['assessor_allocation']]
            for c in cells if c['assessor_actual']],
        'private_records_sha256': document['private_records_sha256']}


def gate_tests(directory, first_attempt_rule):
    """The checker's tests, recomputed. `first_attempt_rule` adds this package's
    stricter reading: a cell that needed any rerun is not complete and accounted."""
    gate = read(directory / 'gate.json')
    experiment = read(directory / 'experiment.json')
    cells = read(directory / 'mechanical-qualification.json')['cells']
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
        public = row.get('attempts') or [{'status': row['status'], 'usage': row['usage'],
                                          'accounting_complete': row['accounting_complete']}]
        if cell['first_attempt_status'] != public[0]['status'] or any(
                (a['status'], a['accounting_complete'], a['tokens'])
                != (b['status'], b['accounting_complete'], b['usage']['total'])
                for a, b in zip(cell['attempts'], public)):
            raise ValueError('normalized attempt disagrees with public experiment')
    bugs = set(gate['cases']) - set(gate['clean_cases'])

    def arm(name):
        return [c for c in cells if c['run_id'].split('/')[0] in bugs
                and c['run_id'].split('/')[1] == name]
    policy = gate['transient_retry']
    tests = {
        'matrix_exact': set(runs) == expected == set(by_cell)
        and len(expected) == gate['expected_runs'],
        'experiment_terminal': experiment['status'] == 'complete',
        'all_runs_complete_and_accounted': len(cells) == gate['expected_runs'] and all(
            c['status'] == 'complete' and c['accounting_complete'] for c in cells),
        'all_attempts_within_ceiling': all(a['tokens'] <= gate['max_tokens_per_run']
                                           for c in cells for a in c['attempts']),
        'retries_within_declared_policy': all(
            len(c['attempts']) - 1 <= policy['per_cell'] and all(
                a['transient_provider_failures'] for a in c['attempts'][:-1])
            for c in cells) and sum(len(c['attempts']) - 1 for c in cells) <= policy[
                'job_budget']}
    if first_attempt_rule:
        tests['all_cells_complete_and_accounted_on_first_attempt'] = all(
            len(c['attempts']) == 1 and c['status'] == 'complete' and c['accounting_complete']
            for c in cells) and len(cells) == gate['expected_runs']
    rows = arm('aggregate')
    tests['aggregate_bug_cells_nonempty_valid_v2'] = (
        len(rows) == gate['aggregate_defect_case_repeats_nonempty_valid_v2'] and all(
            c['findings'] > 0 and not c['finding_validation_errors'] for c in rows))
    rows = arm('assessed')
    tests['assessed_bug_cells_actual_complete_assessor'] = (
        len(rows) == gate['assessed_defect_case_repeats_actual_complete_assessor'] and all(
            c['assessment_status'] == 'complete' and c['assessor_complete'] for c in rows))
    return gate, tests, cells, runs


def gate_items(gate, cells):
    """The three brief-level gate items as counts, for the side-by-side."""
    bugs = set(gate['cases']) - set(gate['clean_cases'])

    def arm(name):
        return [c for c in cells if c['run_id'].split('/')[0] in bugs
                and c['run_id'].split('/')[1] == name]
    return {
        'cells_complete_and_accounted': sum(
            c['status'] == 'complete' and c['accounting_complete'] for c in cells),
        'cells_complete_and_accounted_first_attempt': sum(
            len(c['attempts']) == 1 and c['status'] == 'complete' and c['accounting_complete']
            for c in cells),
        'cells': len(cells),
        'aggregate_defect_cells_nonempty_valid_v2': sum(
            c['findings'] > 0 and not c['finding_validation_errors'] for c in arm('aggregate')),
        'assessed_defect_cells_actual_complete_assessor': sum(
            c['assessment_status'] == 'complete' and c['assessor_complete']
            for c in arm('assessed'))}


def session_rows(cells):
    rows = [s for c in cells for s in c.get('session_statuses', [])]
    assessors = [c for c in cells if c['assessor_actual']]
    return {'discovery_sessions': len(rows),
            'discovery_not_complete_by_reason': dict(collections.Counter(
                s['reason'] for s in rows if s['status'] != 'complete')),
            'assessor_sessions': len(assessors),
            'assessor_not_complete_by_reason': dict(collections.Counter(
                c['assessor_reason'] for c in assessors if not c['assessor_complete'])),
            'discussion_rounds_attempted': sum(c['discussion_attempted_rounds'] for c in cells)}


def summarize(directory, first_attempt_rule, retained_tests=True):
    gate, tests, cells, runs = gate_tests(directory, first_attempt_rule)
    if retained_tests:
        retained = read(directory / 'mechanical-qualification.json')['tests']
        if tests != retained:
            raise ValueError(f'recomputed gate disagrees with retained summary: {directory}')
    clean = set(gate['clean_cases'])
    decisions = []
    arms = {}
    for name in gate['arms']:
        selected = [c for c in cells if c['run_id'].split('/')[1] == name]
        per_case = {}
        for cell in selected:
            case = cell['run_id'].split('/')[0]
            run = runs[cell['run_id']]
            matched = 0
            for index in range(len(run['findings'])):
                defect = decide(run, index)
                if defect is not None:
                    matched += 1
                    decisions.append({'run_id': run['run_id'],
                                      'finding_id': finding_id(run, index),
                                      'defect_id': defect, 'method': 'automated',
                                      'reviewer': 'reproduce.py RULES'})
            usable = cell['status'] == 'complete' and cell['accounting_complete']
            row = per_case.setdefault(case, {'cells': 0, 'complete': 0, 'detected': 0,
                                             'cells_with_findings': 0, 'findings': 0,
                                             'matched_findings': 0, 'tokens': [],
                                             'latency_seconds': []})
            row['cells'] += 1
            row['complete'] += usable
            row['detected'] += bool(usable and matched)
            row['cells_with_findings'] += bool(usable and run['findings'])
            row['findings'] += len(run['findings'])
            row['matched_findings'] += matched
            row['tokens'].append(cell['tokens'] + cell.get('retry_tokens', 0))
            row['latency_seconds'].append(round(cell['latency_seconds'], 3))
        for row in per_case.values():
            row['mean_tokens'] = round(sum(row['tokens']) / len(row['tokens']), 1)
            row['mean_latency_seconds'] = round(
                sum(row['latency_seconds']) / len(row['latency_seconds']), 2)
        defect_cells = sum(r['cells'] for c, r in per_case.items() if c not in clean)
        detected = sum(r['detected'] for c, r in per_case.items() if c not in clean)
        clean_usable = sum(r['complete'] for c, r in per_case.items() if c in clean)
        clean_with = sum(r['cells_with_findings'] for c, r in per_case.items() if c in clean)
        tokens = [c['tokens'] + c.get('retry_tokens', 0) for c in selected]
        latency = [c['latency_seconds'] for c in selected]
        usage = collections.Counter()
        for cell in selected:
            usage.update({k: v for k, v in cell['usage'].items() if k != 'total'})
        arms[name] = {
            'cells': len(selected),
            'complete_and_accounted': sum(c['status'] == 'complete' and c['accounting_complete']
                                          for c in selected),
            'status_counts': dict(collections.Counter(c['status'] for c in selected)),
            'findings': sum(c['findings'] for c in selected),
            'automated_matched_findings': sum(r['matched_findings'] for r in per_case.values()),
            'defect_cells': defect_cells, 'defect_cells_detected': detected,
            'defect_detection_wilson95': wilson(detected, defect_cells),
            'clean_cells_usable': clean_usable, 'clean_cells_with_findings': clean_with,
            'actual_assessor_calls': sum(c['assessor_actual'] for c in selected),
            'complete_assessor_calls': sum(c['assessor_complete'] for c in selected),
            'discussion_attempted_rounds': sum(c['discussion_attempted_rounds']
                                               for c in selected),
            'discussion_completed_rounds': sum(c['discussion_completed_rounds']
                                               for c in selected),
            'total_tokens': sum(tokens), 'mean_tokens': round(sum(tokens) / len(tokens), 1),
            'max_attempt_tokens': max(a['tokens'] for c in selected for a in c['attempts']),
            'usage': dict(sorted(usage.items())),
            'mean_latency_seconds': round(sum(latency) / len(latency), 2),
            'max_latency_seconds': round(max(latency), 2),
            'p95_latency_seconds_nearest_rank': round(nearest_rank(latency, 0.95), 2),
            'per_case': per_case}
    usage = collections.Counter()
    for row in runs.values():
        usage.update({k: v for k, v in row['usage'].items()})
        if row.get('retry_usage'):
            usage.update({k: v for k, v in row['retry_usage'].items()})
    latency = [c['latency_seconds'] for c in cells]
    pending = sum(len(r['findings']) for r in runs.values()) - len(decisions)
    return decisions, {
        'model_alias': read(directory / 'preparation.json')['model_alias'],
        'source_commit': read(directory / 'preparation.json')['source_commit'],
        'timeout_seconds': read(directory / 'preparation.json')['timeout_seconds'],
        'tests': tests, 'all_gate_tests_pass': all(tests.values()),
        'gate_items': gate_items(gate, cells),
        'status_counts': dict(collections.Counter(c['status'] for c in cells)),
        'cells_accounting_incomplete': sum(not c['accounting_complete'] for c in cells),
        'first_attempt_status_counts': dict(collections.Counter(
            c['first_attempt_status'] for c in cells)),
        'rerun_attempts': sum(len(c['attempts']) - 1 for c in cells),
        'transient_http_statuses': dict(collections.Counter(
            str(f['http_status']) for c in cells for a in c['attempts']
            for f in (a['transient_provider_failures'] or []))),
        'session_outcomes': session_rows(cells),
        'usage_all_attempts': dict(sorted(usage.items())),
        'cell_latency_seconds': {'mean': round(sum(latency) / len(latency), 2),
                                 'median': round(statistics.median(latency), 2),
                                 'max': round(max(latency), 2)},
        'findings': sum(len(r['findings']) for r in runs.values()),
        'automated_matched_findings': len(decisions),
        'findings_left_pending_for_a_person': pending,
        'arms': arms}


def proxy_tokens(package):
    """The proxy's own count of this evaluator's requests in the s1 window, from
    provider-evidence.json (derived from private spend-log reads; not recomputable here)."""
    rows = read(package / 'provider-evidence.json')['concurrent_load']['run_window'][
        'by_client_group_base']
    row = rows['python-httpx2 | spark/glm-5.3-flash | http://192.168.3.40:8045/v1']
    return {'requests': row['rows'], 'prompt_tokens': row['prompt_tokens'],
            'completion_tokens': row['completion_tokens'],
            'total_tokens': row['prompt_tokens'] + row['completion_tokens'],
            'note': 'exceeds recorded cell usage because turns cut off by the session timeout '
                    'or past a budget are logged by the proxy but not all are counted in cell '
                    'accounting'}


def build(package, reports=None):
    reports = package.parent if reports is None else reports
    cross_hashes(package)
    decisions, jobs, causes = [], {}, {}
    for job in JOBS:
        rows, jobs[job] = summarize(package / job, first_attempt_rule=True)
        decisions += [{'job': job, **row} for row in rows]
        causes[job] = diagnosis(package / job / 'session-diagnosis.json',
                                read(package / job / 'mechanical-qualification.json')['cells'])
    predecessor = {}
    for name, (sibling, job) in PREDECESSOR.items():
        verify_hashes(reports / sibling)
        _, predecessor[name] = summarize(reports / sibling / job, first_attempt_rule=True)
        causes[name] = diagnosis(package / f'{name}-session-diagnosis.json',
                                 read(reports / sibling / job / 'mechanical-qualification.json')[
                                     'cells'])
    reference = {}
    for name, (sibling, job) in REFERENCE.items():
        verify_hashes(reports / sibling)
        _, reference[name] = summarize(reports / sibling / job, first_attempt_rule=True,
                                       retained_tests=False)
    side = {}
    for name, row in {**jobs, **predecessor, **reference}.items():
        side[name] = {'model_alias': row['model_alias'], 'source_commit': row['source_commit'],
                      'timeout_seconds': row['timeout_seconds'],
                      'gate_pass_under_this_package_rule': row['all_gate_tests_pass'],
                      **row['gate_items'],
                      'rerun_attempts': row['rerun_attempts'],
                      'status_counts': row['status_counts'],
                      'total_tokens': row['usage_all_attempts']['total'],
                      'output_tokens': row['usage_all_attempts']['output'],
                      'cell_latency_seconds': row['cell_latency_seconds'],
                      'mean_tokens_by_arm': {a: r['mean_tokens'] for a, r in row['arms'].items()},
                      'mean_latency_seconds_by_arm': {a: r['mean_latency_seconds']
                                                      for a, r in row['arms'].items()},
                      'defect_cells_detected_by_arm': {
                          a: f"{r['defect_cells_detected']}/{r['defect_cells']}"
                          for a, r in row['arms'].items()},
                      'clean_cells_with_findings_by_arm': {
                          a: f"{r['clean_cells_with_findings']}/{r['clean_cells_usable']}"
                          for a, r in row['arms'].items()},
                      'clean_cells_complete': sum(
                          r['per_case'].get('contract-explicit-default-clean', {}).get(
                              'complete', 0) for r in row['arms'].values())}
    return decisions, {
        'schema_version': 1, 'judgments': 'automated keyword filters, provisional',
        'actual_human_judgments': 0, 'holdout_used': False, 'defaults_promoted': False,
        'qualified': any(row['all_gate_tests_pass'] for row in jobs.values()),
        'jobs': jobs, 'predecessor_g2': predecessor, 'reference_gpt_6_1_sol': reference,
        'session_causes': causes,
        'side_by_side': side,
        'proxy_logged_evaluator_tokens_s1_window': proxy_tokens(package),
        'side_by_side_caveat': 'g2: same profile and gate, constraint off, nare 2026.10.4, '
                               'source 5c4a8b6, 2026-10-14, heavier concurrent load. Sol: '
                               'different model, source 2f1e95c, 2026-10-13, 120 s timeout. '
                               'A side-by-side, not a paired comparison'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reports', type=Path, default=None,
                        help='directory holding the sibling reference packages')
    parser.add_argument('--write', action='store_true',
                        help='author only: (re)write the derived files instead of checking')
    args = parser.parse_args()
    package = args.package.resolve()
    count = None if args.write else verify_hashes(package)
    decisions, summary = build(package, args.reports.resolve() if args.reports else None)
    if args.write:
        (package / 'automated-decisions.json').write_text(json.dumps(decisions, indent=2) + '\n')
        (package / 'summary.json').write_text(
            json.dumps(summary, indent=2, sort_keys=True) + '\n')
    else:
        if decisions != read(package / 'automated-decisions.json'):
            raise ValueError('recomputed automated decisions differ from the published ones')
        if summary != read(package / 'summary.json'):
            raise ValueError('recomputed summary differs from the published one')
        print(json.dumps({'verified_artifacts': count, 'automated_decisions': len(decisions),
                          'qualified': summary['qualified']}))
    args.output.write_text(json.dumps(summary, sort_keys=True, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    main()
