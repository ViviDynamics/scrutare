import copy

import pytest

import scrutare.evaluation as evaluation


def metrics(precision=.8, recall=.7, tokens=1000, latency=10):
    return {'finding_precision':precision,'defect_recall':recall,'tokens':tokens,
            'latency_seconds':[latency]*30,'quality_final':True,'pending_findings':0,
            'accounting_failures':0,'complete_runs':30,'partial_runs':0,'failed_runs':0,
            'missing_runs':0,'invalid_runs':0}


def evidence():
    return {'holdout_unseen':True,'independent_human_verification':True,
            'uncertainty_sufficient':True,'same_model_rail':True,
            'equal_total_token_ceiling':True,'new_critical_security_misses':0,
            'evidence_kind':'model', 'same_case_repeat_schedule':True,
            'verification_reference':'human-review-record','holdout_reference':'new-frozen-corpus'}


def gate(base, candidate, ev):
    function = getattr(evaluation,'evaluate_promotion',None)
    assert callable(function), 'Missing explicit measured promotion gate'
    return function(base,candidate,ev)


def test_gate_passes_recall_gain_at_frozen_cost_and_precision_boundaries():
    result = gate(metrics(),metrics(precision=.78,recall=.75,tokens=1200,latency=12),evidence())
    assert result['status'] == 'pass'
    assert result['defaults_changed'] is False
    assert result['policy']['minimum_gain_percentage_points'] == 5


def test_gate_passes_precision_gain_and_refuses_cost_or_security_regression():
    candidate = metrics(precision=.85,recall=.68)
    assert gate(metrics(),candidate,evidence())['status'] == 'pass'
    candidate['tokens'] = 1201
    assert gate(metrics(),candidate,evidence())['status'] == 'fail'
    ev = evidence()
    ev['new_critical_security_misses'] = 1
    assert gate(metrics(),metrics(precision=.95,recall=.95),ev)['status'] == 'fail'


@pytest.mark.parametrize('key', ['holdout_unseen','independent_human_verification',
    'uncertainty_sufficient','same_model_rail','equal_total_token_ceiling'])
def test_gate_is_inconclusive_without_each_required_evidence_guard(key):
    ev = evidence()
    ev[key] = False
    assert gate(metrics(),metrics(precision=.9,recall=.9),ev)['status'] == 'inconclusive'


@pytest.mark.parametrize('key', ['pending_findings','accounting_failures','missing_runs',
                                 'failed_runs','partial_runs','invalid_runs'])
def test_gate_is_inconclusive_for_unresolved_or_incomplete_measurements(key):
    candidate=metrics(precision=.9,recall=.9)
    candidate[key]=1
    assert gate(metrics(),candidate,evidence())['status'] == 'inconclusive'


def test_gate_refuses_no_improvement_and_null_nan_zero_cost_denominators():
    assert gate(metrics(),metrics(),evidence())['status'] == 'fail'
    for key,value in [('finding_precision',None),('defect_recall',float('nan')),('tokens',0)]:
        base=copy.deepcopy(metrics())
        base[key]=value
        assert gate(base,metrics(precision=.9,recall=.9),evidence())['status'] == 'inconclusive'


def test_gate_cli_writes_explicit_inconclusive_report_without_changing_defaults(tmp_path):
    import json
    import subprocess
    import sys
    ev = evidence()
    ev['holdout_unseen'] = False
    source = tmp_path/'gate-input.json'
    source.write_text(json.dumps({'baseline':metrics(), 'candidate':metrics(.9,.9), 'evidence':ev}))
    output = tmp_path/'gate.json'
    process = subprocess.run([sys.executable,'-m','scrutare.evaluation','gate',
        '--input',str(source),'--output',str(output)],capture_output=True)
    assert process.returncode == 0, process.stderr
    result=json.loads(output.read_text())
    assert result['status']=='inconclusive'
    assert result['defaults_changed'] is False


def test_gate_refuses_offline_evidence_or_unequal_case_repeat_denominators():
    ev = evidence()
    ev['evidence_kind'] = 'offline'
    assert gate(metrics(),metrics(.9,.9),ev)['status'] == 'inconclusive'
    candidate = metrics(.9,.9)
    candidate['complete_runs'] = 60
    candidate['latency_seconds'] = [10]*60
    assert gate(metrics(),candidate,evidence())['status'] == 'inconclusive'
