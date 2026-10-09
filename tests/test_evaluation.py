import pytest

from scrutare.evaluation.scoring import adjudication_packet, finding_id, score_experiment


def record(case='bug', status='complete', findings=None):
    return {'run_id': f'{case}/senior/1', 'case_id': case, 'variant': 'senior',
            'repeat': 1, 'status': status, 'accounting_complete': True,
            'usage': {'total': 100}, 'latency_seconds': 2,
            'findings': findings or []}


def test_metrics_require_human_decisions_and_defect_level_deduplication():
    a = {'persona': 'senior-dev', 'body': 'off by one'}
    b = {'persona': 'security', 'body': 'same off by one'}
    c = {'persona': 'security', 'body': 'unsupported claim'}
    runs = [record(findings=[a, b, c]), record('clean', 'failed')]
    labels = {'bug': {'defects': [{'id': 'd1'}, {'id': 'd2'}]}, 'clean': {'defects': []}}
    decisions = [{'finding_id': finding_id(runs[0], i), 'defect_id': d,
                  'reviewer': 'human@example', 'rationale': 'checked trigger'}
                 for i, d in enumerate(['d1', 'd1', None])]
    metrics = score_experiment(runs, labels, decisions)['senior']
    assert metrics['finding_precision'] == pytest.approx(2 / 3)
    assert metrics['defect_recall'] == 0.5
    assert metrics['duplicate_rate'] == pytest.approx(1 / 3)
    assert metrics['tokens_per_true_positive'] == 200
    assert metrics['clean_pr_false_positive_rate'] is None
    assert metrics['failed_runs'] == 1
    assert metrics['unique_detections'] == {'security': 0, 'senior-dev': 0}


def test_pending_is_not_false_positive_and_partial_is_not_clean_approval():
    runs = [record('clean', 'partial', [{'persona': 'security', 'body': 'maybe'}])]
    result = score_experiment(runs, {'clean': {'defects': []}}, [])['senior']
    assert result['pending_findings'] == 1
    assert result['finding_precision'] is None
    assert result['clean_pr_false_positive_rate'] is None
    assert result['partial_runs'] == 1


def test_packet_is_blinded_and_decisions_are_bound_to_content():
    run = record(findings=[{'persona': 'senior-dev', 'body': 'problem'}])
    packet = adjudication_packet([run])
    assert packet['items'][0]['finding'] == {'body': 'problem'}
    assert 'senior' not in str(packet)
    decision = {'finding_id': finding_id(run, 0), 'defect_id': 'wrong',
                'reviewer': 'human', 'rationale': 'read code'}
    with pytest.raises(ValueError, match='defect'):
        score_experiment([run], {'bug': {'defects': [{'id': 'd1'}]}}, [decision])
    changed = {**run, 'findings': [{'persona': 'senior-dev', 'body': 'changed'}]}
    with pytest.raises(ValueError, match='unknown'):
        score_experiment([changed], {'bug': {'defects': [{'id': 'd1'}]}}, [decision])
