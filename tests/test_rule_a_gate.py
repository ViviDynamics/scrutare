"""Common operational predicates do not trade away evidence or charged failures."""
import pytest

from scrutare.evaluation.qualification import rule_a_predicates


def good():
    rows = []
    for case in ('clean', 'bug1', 'bug2'):
        for arm in ('aggregate', 'assessed', 'focused1', 'focused3'):
            for repeat in range(1, 4):
                attempt = {'attempt': 1, 'status': 'complete', 'accounting_complete': True,
                           'tokens': 10, 'ledger_reconciled': True, 'provenance_valid': True,
                           'routing_valid': True, 'paid_calls': 0, 'fallbacks': 0,
                           'failed_sessions': 0, 'terminal': True}
                rows.append({'run_id': f'{case}/{arm}/{repeat}', 'status': 'complete',
                             'accounting_complete': True, 'selected_attempt': 1,
                             'attempts': [attempt], 'evidence_valid': True,
                             'findings': 0 if case == 'clean' else 1,
                             'assessor_complete': True, 'replay_valid': True})
    return rows


def check(rows, **kwargs):
    return rule_a_predicates(rows, cases=['clean', 'bug1', 'bug2'], clean_cases=['clean'],
                             experiment_terminal=True, holdout_used=False, **kwargs)


def test_rule_a_common_predicates_allow_one_successful_recovery_and_charge_both():
    rows = good()
    original = rows[0]['attempts'][0]
    original.update(status='failed', failed_sessions=1, retryable=True)
    rows[0]['attempts'].append(dict(original, attempt=2, status='complete', failed_sessions=0))
    rows[0]['selected_attempt'] = 2
    assert all(check(rows).values())


@pytest.mark.parametrize('change,gate', [
    ('empty', 'all_24_defect_cells_nonempty_valid'),
    ('clean_failed', 'all_36_complete_accounted'),
    ('assessor', 'six_assessed_actual_complete'),
    ('missing', 'matrix_exact'), ('extra', 'matrix_exact'),
    ('paid', 'zero_paid_calls_and_fallbacks'), ('fallback', 'zero_paid_calls_and_fallbacks'),
    ('usage', 'all_attempts_reconciled'), ('provenance', 'all_attempts_provenance_routing'),
    ('cap', 'all_attempts_within_ceiling'), ('third', 'bounded_rule_a_selection'),
    ('successful_retry', 'bounded_rule_a_selection'), ('replay', 'accepted_replay_valid'),
    ('unknown', 'every_attempt_terminal'),
])
def test_common_predicate_failure_is_never_waived(change, gate):
    rows = good()
    if change == 'empty':
        rows[12]['findings'] = 0
    if change == 'clean_failed':
        rows[0]['status'] = 'failed'
    if change == 'assessor':
        rows[15]['assessor_complete'] = False
    if change == 'missing':
        rows.pop()
    if change == 'extra':
        rows.append(rows[0])
    if change == 'paid':
        rows[0]['attempts'][0]['paid_calls'] = 1
    if change == 'fallback':
        rows[0]['attempts'][0]['fallbacks'] = 1
    if change == 'usage':
        rows[0]['attempts'][0]['ledger_reconciled'] = False
    if change == 'provenance':
        rows[0]['attempts'][0]['provenance_valid'] = False
    if change == 'cap':
        rows[0]['attempts'][0]['tokens'] = 200001
    if change == 'third':
        rows[0]['attempts'] *= 3
    if change == 'successful_retry':
        rows[0]['attempts'] *= 2
    if change == 'replay':
        rows[0]['replay_valid'] = False
    if change == 'unknown':
        rows[0]['attempts'][0]['terminal'] = False
    assert check(rows)[gate] is False


def test_inspector_requires_provider_records_for_every_attempt(tmp_path, monkeypatch):
    from test_rule_a import execute

    from scrutare.evaluation.qualification import inspect_rule_a
    result, _ = execute(tmp_path, monkeypatch)
    checked = inspect_rule_a(tmp_path / 'out', {
        'cases': ['a', 'b', 'c'], 'clean_cases': ['a'], 'holdout_used': False,
        'max_tokens_per_run': 400}, {'requests': [], 'supplements': []})
    assert checked['qualification'] == 'not_qualified'
    assert checked['tests']['all_attempts_reconciled'] is False
    assert checked['cumulative_usage'] == result['cumulative_usage']


def test_reported_cumulative_cost_must_include_failed_attempts(tmp_path, monkeypatch):
    import json

    from test_rule_a import execute

    from scrutare.evaluation.qualification import inspect_rule_a
    execute(tmp_path, monkeypatch)
    path = tmp_path / 'out/experiment.json'
    data = json.loads(path.read_text())
    data['cumulative_usage']['input'] -= 1
    path.write_text(json.dumps(data))
    result = inspect_rule_a(tmp_path / 'out', {'cases': ['a', 'b', 'c'], 'clean_cases': ['a'],
        'holdout_used': False, 'max_tokens_per_run': 400}, {'requests': [], 'supplements': []})
    assert result['tests']['cumulative_cost_complete'] is False
