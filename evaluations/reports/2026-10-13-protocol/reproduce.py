"""Recompute qualification gates from public normalized evidence, without model calls."""
import argparse
import collections
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def summarize(package):
    gate = read(package / 'qualification-gate.json')
    experiment = read(package / 'experiment.json')
    mechanics = read(package / 'mechanical-qualification.json')
    diagnostics = read(package / 'normalized-mechanical-diagnostics.json')
    expected = {f'{case}/{arm}/{repeat}' for case in gate['cases'] for arm in gate['arms']
                for repeat in range(1, gate['repeats'] + 1)}
    counts = collections.Counter(row['run_id'] for row in experiment['runs'])
    cell_counts = collections.Counter(row['run_id'] for row in mechanics['cells'])
    exact = (set(counts) == expected == set(cell_counts)
             and all(v == 1 for v in counts.values())
             and all(v == 1 for v in cell_counts.values())
             and len(expected) == gate['expected_runs'])
    runs = {row['run_id']: row for row in experiment['runs']}
    cells = mechanics['cells']
    consolidated = read(package / 'consolidated-findings.json')['runs']
    group_ids = collections.Counter(row['run_id'] for row in consolidated)
    if set(group_ids) != expected or any(n != 1 for n in group_ids.values()):
        raise ValueError('consolidated finding matrix disagrees with schedule')
    consolidated_count = 0
    for row in consolidated:
        groups = row['consolidated_findings']
        if row['artifact_available']:
            flattened = [source for group in groups for source in group['sources']]
            consolidated_count += len(groups)
            if flattened != runs[row['run_id']]['findings']:
                raise ValueError('raw consolidated sources disagree with evaluator source records')
        elif groups is not None or runs[row['run_id']]['findings']:
            raise ValueError('unavailable consolidated artifact must remain unknown')
    for cell in cells:
        row = runs[cell['run_id']]
        if (cell['status'] != row['status']
                or cell['accounting_complete'] != row['accounting_complete']
                or cell['tokens'] != row['usage']['total']
                or cell['findings'] != len(row['findings'])):
            raise ValueError('normalized cell disagrees with public experiment')
    bug_cases = set(gate['cases']) - {'contract-explicit-default-clean'}
    aggregates = [c for c in cells if c['run_id'].split('/')[0] in bug_cases
                  and c['run_id'].split('/')[1] == 'aggregate']
    assessed = [c for c in cells if c['run_id'].split('/')[0] in bug_cases
                and c['run_id'].split('/')[1] == 'assessed']
    tests = {
        'matrix_exact': exact,
        'experiment_terminal': experiment['status'] == 'complete',
        'all_runs_complete_and_accounted': len(cells) == gate['expected_runs'] and all(
            c['status'] == 'complete' and c['accounting_complete'] for c in cells),
        'all_runs_within_ceiling': all(c['tokens'] <= gate['max_tokens_per_run'] for c in cells),
        'six_aggregate_bug_cells_nonempty_valid_v2': len(aggregates) == 6 and all(
            c['findings'] > 0 and not c['finding_validation_errors'] for c in aggregates),
        'six_assessed_bug_cells_actual_complete_assessor': len(assessed) == 6 and all(
            c['assessment_status'] == 'complete' and c['assessor_complete'] for c in assessed),
    }
    if tests != mechanics['tests']:
        raise ValueError('recomputed gates disagree with retained mechanical summary')
    native = diagnostics['initial_native_outcomes']
    if any(o['run_id'] not in expected for o in native):
        raise ValueError('unexpected native run identity')
    native_counts = collections.Counter((o['run_id'], o['persona']) for o in native)
    if (len(native) != 144 or len(native_counts) != 144
            or any(v != 1 for v in native_counts.values())
            or any(sum(o['run_id'] == identity for o in native) != 4 for identity in expected)):
        raise ValueError('initial native discovery matrix is not exactly four per run')
    result = 'pass' if all(tests.values()) else 'not_qualified'
    return {'package': package.name, 'qualification': result,
            'tests': tests, 'status_counts': dict(collections.Counter(c['status'] for c in cells)),
            'accounted_runs': sum(c['accounting_complete'] for c in cells),
            'total_tokens': sum(c['tokens'] for c in cells),
            'runs_over_ceiling': sum(c['tokens'] > gate['max_tokens_per_run'] for c in cells),
            'max_run_tokens': max(c['tokens'] for c in cells),
            'retained_source_observations': sum(c['findings'] for c in cells),
            'consolidated_run_findings': consolidated_count,
            'actual_assessor_calls': sum(c['assessor_actual'] for c in cells),
            'complete_assessor_calls': sum(c['assessor_complete'] for c in cells),
            'discussion_attempted_rounds': sum(c['discussion_attempted_rounds'] for c in cells),
            'discussion_completed_rounds': sum(c['discussion_completed_rounds'] for c in cells),
            'initial_native_reason_counts': dict(collections.Counter(o['reason'] for o in native)),
            'initial_native_status_counts': dict(collections.Counter(o['status'] for o in native))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = args.package
    hashes = read(package / 'published-sha256.json')
    for name, expected in hashes.items():
        path = package / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('public artifact hash mismatch')
    summaries = [summarize(path) for path in sorted(package.iterdir())
                 if path.is_dir() and (path / 'qualification-gate.json').exists()]
    unadjudicated = read(package / 'unadjudicated-findings.json')
    observation_ids = [row['observation_id'] for row in unadjudicated['records']]
    observed = {}
    for path in sorted(package.iterdir()):
        if path.is_dir() and (path / 'qualification-gate.json').exists():
            for row in read(path / 'experiment.json')['runs']:
                for number, finding in enumerate(row['findings'], 1):
                    observed[f"{path.name}/{row['run_id']}/{number}"] = finding
    if (len(observation_ids) != len(set(observation_ids))
            or len(observation_ids) != sum(row['retained_source_observations'] for row in summaries)
            or any(row['status'] != 'unadjudicated' for row in unadjudicated['records'])
            or unadjudicated['actual_human_judgments'] != 0
            or unadjudicated['semantic_dispositions'] != 0):
        raise ValueError('unadjudicated inventory disagrees with source observations')
    if any(observed.get(row['observation_id']) != row['finding']
           for row in unadjudicated['records']):
        raise ValueError('unadjudicated observation differs from original public source finding')
    args.output.write_text(json.dumps({'schema_version': 1, 'schedules': summaries,
        'actual_human_judgments': 0, 'semantic_qualification': False, 'holdout_used': False,
        'limitations': 'Recomputes normalized public gates/counts. Authenticating native captures '
        'and revalidating original session outputs requires retained private captures.'},
        sort_keys=True, separators=(',', ':')) + '\n')


if __name__ == '__main__':
    main()
