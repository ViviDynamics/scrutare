"""The g3 report refuses unsupported qualification and correction claims."""
import importlib.util
from pathlib import Path

import pytest

PACKAGE = (Path(__file__).resolve().parents[1] /
           'evaluations/reports/2026-10-25-glm-g3-qualification')


def module():
    spec = importlib.util.spec_from_file_location('glm_g3_report', PACKAGE / 'reproduce.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def correction_fixture(outcome='corrected', status='complete'):
    row = {'cell': 'security-auth-order/aggregate/1', 'persona': 'senior-dev',
           'attempt': 'attempt-0001', 'status': status, 'reason': 'done',
           'tokens': 150, 'first_run_tokens': 100, 'nare_status': 'done',
           'citation_correction': {'offered': True, 'defects': 1, 'outcome': outcome,
                                   'exit_code': 0},
           'citation_correction_request': {'refusal': {'reason': 'citation_validation'},
                                           'defects': [
                                               {'problems': ['line_range_outside_capture']}]},
           'correction_terminal': {'status': 'done', 'turns': 4}}
    first = {'session': f"{row['cell']}/sessions/{row['persona']}/{row['attempt']}",
             'status': status, 'reason': 'done',
             'first_answer': 'citation:line_range_outside_capture',
             'correctable': [['line_range_outside_capture']]}
    return row, first


def test_correction_counts_refusals_outcomes_and_incremental_usage():
    row, first = correction_fixture()
    summary = module().correction_summary([row], [first])
    assert summary['offered'] == 1 and summary['by_outcome'] == {'corrected': 1}
    assert summary['tokens'] == 50 and summary['corrected_sessions_complete'] == 1
    assert summary['first_answers_refused_for_citations'] == 1


def test_a_refused_correction_cannot_be_a_complete_session():
    row, first = correction_fixture(outcome='refused')
    with pytest.raises(ValueError, match='correction outcome'):
        module().correction_summary([row], [first])


def test_refused_correction_is_retained_as_a_failure():
    row, first = correction_fixture(outcome='refused', status='failed')
    result = module().correction_summary([row], [first])
    assert result['by_outcome'] == {'refused': 1}
    assert result['corrected_sessions_complete'] == 0


@pytest.mark.parametrize('change', ['valid_first', 'no_usage', 'missing_record'])
def test_correction_requires_a_refusal_record_and_incremental_usage(change):
    row, first = correction_fixture()
    if change == 'valid_first':
        first['first_answer'] = 'valid'
        first.pop('correctable')
    elif change == 'no_usage':
        row['tokens'] = row['first_run_tokens']
    else:
        row['citation_correction'] = None
    with pytest.raises(ValueError, match='correction'):
        module().correction_summary([row], [first])


def test_provider_verifier_accepts_spark_trailing_slash_and_refuses_paid_or_fallback():
    row = {'model_group': 'spark/glm-5.3-flash', 'model': 'openai/glm-5.3-flash',
           'api_base': 'http://192.168.3.40:8045/v1/', 'attempted_fallbacks': 0}
    module().verify_serving_rows([row])
    for changed in ({**row, 'model': 'openai/gpt-4.1'},
                    {**row, 'api_base': 'https://api.openai.com/v1'},
                    {**row, 'attempted_fallbacks': 1}):
        with pytest.raises(ValueError, match='serving'):
            module().verify_serving_rows([changed])


def extractor():
    spec = importlib.util.spec_from_file_location('glm_g3_extractor',
                                                  PACKAGE / 'extract-sessions.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_exporter_retains_original_and_transient_rerun_identity():
    function = extractor().session_identity
    assert function(Path('security-auth-order/aggregate/1/sessions/'
                         'senior-dev/attempt-0001/result.json')) == (
        'security-auth-order/aggregate/1', 1, 'security-auth-order/aggregate/1',
        'senior-dev', 'attempt-0001')
    assert function(Path('_retries/security-auth-order/aggregate/1/attempt-0002/'
                         'sessions/senior-dev/attempt-0001/result.json')) == (
        'security-auth-order/aggregate/1', 2,
        '_retries/security-auth-order/aggregate/1/attempt-0002', 'senior-dev', 'attempt-0001')


def test_exporter_counts_correction_request_usage_separately(tmp_path):
    import json

    first = tmp_path / 'stdout.jsonl'
    correction = tmp_path / 'correction.stdout.jsonl'
    first.write_text(json.dumps({'type': 'cost', 'detail': {'output': 100}}) + '\n')
    correction.write_text(json.dumps({'type': 'cost', 'detail': {'output': 12}}) + '\n')
    combined = extractor().request_outputs(tmp_path)
    assert combined == {'initial': [100], 'correction': [12], 'all': [100, 12]}


def test_public_package_reproduces_and_preserves_the_gate(tmp_path):
    import json

    report = module()
    helper = report.load_c1(PACKAGE.parent)
    helper.verify_hashes(PACKAGE)
    summary = report.build(PACKAGE, PACKAGE.parent)
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['qualified'] == all(summary['jobs']['g3']['tests'].values())
    assert not summary['holdout_used'] and not summary['defaults_promoted']
    assert summary['actual_human_judgments'] == 0
    assert summary['side_by_side']['c1']['cells_complete_and_accounted'] == 31
    assert summary['side_by_side']['s2']['cells_complete_and_accounted'] == 34
    assert summary['provider']['g3']['evaluator_paid_provider_rows'] == 0
    assert summary['provider']['g3']['evaluator_attempted_fallbacks_nonzero'] == 0
    assert summary['session_causes']['g3']['sessions'] >= 144
    assert summary['session_causes']['g3']['corrections']['discovery_sessions'] >= 144


@pytest.mark.parametrize('change', ['frozen_setting', 'session_status', 'paid_serving'])
def test_rehashed_report_cannot_change_settings_status_or_serving(tmp_path, change):
    import hashlib
    import json
    import shutil

    target = tmp_path / 'report'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    if change == 'frozen_setting':
        name = 'g3/evaluation.json'
        document = json.loads((target / name).read_text())
        document['context']['line_counts'] = False
    elif change == 'session_status':
        name = 'g3/session-diagnosis.json'
        document = json.loads((target / name).read_text())
        row = document['sessions'][0]
        row['status'] = 'failed' if row['status'] == 'complete' else 'complete'
    else:
        name = 'provider-evidence.json'
        document = json.loads((target / name).read_text())
        row = next(r for r in document['windows']['g3']['rows'] if r['agent'] == 'python-httpx2')
        row['model'] = 'openai/gpt-4.1'
    (target / name).write_text(json.dumps(document))
    manifest = json.loads((target / 'published-sha256.json').read_text())
    manifest[name] = hashlib.sha256((target / name).read_bytes()).hexdigest()
    (target / 'published-sha256.json').write_text(json.dumps(manifest))
    report = module()
    report.load_c1(PACKAGE.parent).verify_hashes(target)
    with pytest.raises(ValueError, match='posted on #51|disagrees|serving'):
        report.build(target, PACKAGE.parent)


def test_corrected_citation_does_not_turn_a_partial_session_into_a_complete_one():
    row, first = correction_fixture(status='partial')
    summary = module().correction_summary([row], [first])
    assert summary['by_outcome'] == {'corrected': 1}
    assert summary['corrected_sessions_complete'] == 0


def test_report_rejects_more_than_one_correction_turn():
    row, first = correction_fixture()
    row['correction_request_output_tokens'] = [12, 13]
    with pytest.raises(ValueError, match='bounded correction'):
        module().correction_summary([row], [first])


def test_empty_aggregate_evidence_is_a_gate_loss_even_when_the_cell_completed():
    gate = {'cases': ['bug'], 'clean_cases': [], 'max_tokens_per_run': 200000}
    cell = {'run_id': 'bug/aggregate/1', 'status': 'complete', 'accounting_complete': True,
            'attempts': [{'tokens': 100}], 'findings': 0, 'finding_validation_errors': []}
    assert module().gate_losses([cell], gate, []) == [
        {'cell': 'bug/aggregate/1', 'causes': ['aggregate_empty_or_invalid_evidence']}]


def test_protocol_causes_distinguish_bad_anchors_and_invented_assessor_candidates():
    from types import SimpleNamespace

    helper = SimpleNamespace(cause=lambda row: 'answer_evidence_refused')
    cells = {'bug/assessed/1': {'candidates': [{'candidate_id': 'a'}]}}
    bad_anchor = {'cell': 'bug/assessed/1', 'persona': 'junior-dev', 'reason': 'protocol',
                  'nare_error': None, 'finding_answer_anchors': [{'line': 0}]}
    invented = {'cell': 'bug/assessed/1', 'persona': 'evidence-assessor', 'reason': 'protocol',
                'nare_error': None, 'assessment_answer_candidate_ids': ['a', 'invented']}
    assert module().session_cause(bad_anchor, cells, helper) == 'invalid_finding_anchor'
    assert module().session_cause(invented, cells, helper) == 'assessment_candidate_identity'


def test_spend_reconciliation_requires_every_successful_evaluator_request_and_token():
    usage = {'input': 10, 'output': 5}
    sessions = {'requests': 2}
    provider = {'evaluator_rows': 2, 'evaluator_prompt_tokens': 10,
                'evaluator_completion_tokens': 5, 'evaluator_failed_rows': []}
    assert module().reconcile_usage(usage, sessions, provider)['exact_match']
    for key in ('evaluator_rows', 'evaluator_prompt_tokens', 'evaluator_completion_tokens'):
        changed = {**provider, key: provider[key] + 1}
        with pytest.raises(ValueError, match='serving usage'):
            module().reconcile_usage(usage, sessions, changed)
