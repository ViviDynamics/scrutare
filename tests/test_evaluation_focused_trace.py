"""Public focused traces retain measured rounds without native session paths."""
import hashlib
import json


def trace_document():
    citation = {'side': 'head', 'path': 'app.py', 'revision': 'a'*40,
                'start_line': 1, 'end_line': 1, 'sha256': 'b'*64,
                'validation': 'captured', 'validation_reason': 'Captured'}
    return {'schema_version': 2, 'kind': 'focused_evidence_discussion',
        'status': 'partial', 'reason': 'deadlock', 'bypassed_candidate_ids': ['supported-one'],
        'initial': {'private_runtime_path': '/secret/native/session'},
        'rounds': [{'round': 1, 'candidate_ids': ['uncertain-one'], 'tokens': 22,
            'latency_seconds': 1.2, 'new_citations': [[citation]],
            'proposals': [{'persona': 'security', 'candidate_id': 'uncertain-one',
                          'action': 'unresolved', 'reason': 'Captured gap', 'citations': [citation],
                          'transition_supported': False}],
            'positions': [{'artifact_directory': '/private/position/session'}],
            'reassessment': 'assessment-round-0001.json',
            'disposition_changes': [{'candidate_id': 'uncertain-one', 'before': 'supported',
                                    'after': 'unresolved', 'reason': 'Contradictory code'}]}],
        'final_assessments': [{'candidate_id': 'uncertain-one', 'status': 'unresolved',
            'reason': 'Captured gap', 'supporting_citations': [citation], 'counter_citations': []}],
        'ledger': {'private_native_directory': '/private/ledger'}}


def test_focused_trace_retains_round_costs_citations_and_independent_transitions(tmp_path):
    from scrutare.evaluation.focused_trace import discussion_trace
    path = tmp_path / 'debate.json'
    path.write_text(json.dumps(trace_document()))
    trace = discussion_trace(tmp_path)
    assert trace['availability'] == 'recorded'
    assert trace['artifact_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert trace['status'] == 'partial' and trace['reason'] == 'deadlock'
    assert trace['bypassed_candidate_ids'] == ['supported-one']
    round_ = trace['rounds'][0]
    assert round_['tokens'] == 22 and round_['latency_seconds'] == 1.2
    assert round_['new_citations'][0][0]['path'] == 'app.py'
    assert round_['proposals'][0]['action'] == 'unresolved'
    assert round_['disposition_changes'][0]['after'] == 'unresolved'
    assert trace['final_assessments'][0]['status'] == 'unresolved'
    assert '/private/' not in json.dumps(trace) and '/secret/' not in json.dumps(trace)
    assert 'positions' not in round_ and 'reassessment' not in round_


def test_missing_or_legacy_trace_remains_unavailable_not_zero_round_success(tmp_path):
    from scrutare.evaluation.focused_trace import discussion_trace
    trace = discussion_trace(tmp_path)
    assert trace['availability'] == 'unavailable'
    assert trace['rounds'] is None and trace['artifact_sha256'] is None
    (tmp_path / 'debate.json').write_text('{"schema_version":1,"rounds":[]}')
    trace = discussion_trace(tmp_path)
    assert trace['availability'] == 'unavailable' and trace['rounds'] is None
    assert trace['artifact_sha256']


def test_malformed_round_measurements_do_not_become_clean_trace(tmp_path):
    from scrutare.evaluation.focused_trace import discussion_trace
    document = trace_document()
    document['rounds'][0]['tokens'] = -1
    (tmp_path / 'debate.json').write_text(json.dumps(document))
    trace = discussion_trace(tmp_path)
    assert trace['availability'] == 'unavailable' and trace['rounds'] is None
