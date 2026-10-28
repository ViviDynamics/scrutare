"""A qualification inspector binds costs, terminal records and accepted evidence."""
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from scrutare.config import parse_config
from scrutare.engine.recovery import RULE_A
from scrutare.engine.session_models import TokenUsage
from scrutare.evaluation.qualification import inspect_rule_a
from scrutare.provenance import write_artifact_manifest


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'experiment'
    directory = root / 'a/aggregate/1'
    directory.mkdir(parents=True)
    config = parse_config(b'models: {default: {provider: openai, '
        b'base_url: "https://litellm.vividynamics.com/v1", model: spark/glm-5.3-flash}}\n'
        b'personas: [senior-dev]\n')
    usage = TokenUsage(input=1, output=1).to_dict()
    session = directory / 'sessions/senior-dev/attempt-0001'
    terminal = {'persona': 'senior-dev', 'status': 'complete', 'reason': 'done',
        'accounting_complete': True, 'usage': usage, 'artifact_directory': str(session)}
    write(session / 'result.json', terminal)
    write(session / 'invocation.json', {'rail': config.models.default.record()})
    write(session / 'stdout.jsonl', {'type': 'cost', 'text': '', 'timestamp': 'frozen',
        'detail': {k: v for k, v in usage.items() if k != 'total'} | {'cost': None}})
    for name in ('config.yaml', 'config.json'):
        write(directory / name, config.to_dict())
    write(directory / 'qualification-policy.json', RULE_A)
    write(directory / 'fanout.json', {'personas': [{'name': 'senior-dev'}],
        'result': {'outcomes': [terminal]}})
    write(directory / 'panel.json', {'status': 'complete', 'reason': 'converged',
        'initial': {'result': {'outcomes': [terminal]}}})
    write(directory / 'verdict.json', {'findings': []})
    (directory / 'review-inputs').mkdir()
    write_artifact_manifest(directory)
    attempt = {'attempt': 1, 'run_dir': str(directory), 'status': 'complete',
        'accounting_complete': True, 'usage': usage, 'error': None, 'latency_seconds': 1}
    row = {**attempt, 'run_id': 'a/aggregate/1', 'selected_attempt': 1,
           'findings': [], 'attempts': [attempt], 'cumulative_usage': usage}
    write(root / 'experiment.json', {'status': 'complete', 'runs': [row],
                                     'cumulative_usage': usage})
    write(root / 'snapshot.json', {'qualification': RULE_A, 'cases': [
        {'id': case, 'split': 'development'} for case in ('a', 'b', 'c')],
        'configs': {'aggregate': config.to_dict()},
        'case_variants': {'a': {'aggregate': {'inputs_sha256': {}}}}})
    request = {'id': 'request-1', 'run_id': 'a/aggregate/1', 'attempt': 1,
        'session': 'sessions/senior-dev/attempt-0001', 'source_sha256': 'a' * 64,
        'model': 'openai/glm-5.3-flash', 'api_base': 'http://192.168.3.40:8045',
        'paid_calls': 0, 'fallbacks': 0, 'usage': usage, 'stream': 'stdout.jsonl', 'event': 0}
    ledger = {'requests': [request], 'supplements': []}
    gate = {'cases': ['a', 'b', 'c'], 'clean_cases': ['a'], 'holdout_used': False,
            'max_tokens_per_run': 200000}
    monkeypatch.setattr('scrutare.engine.review_inputs.prepare_review_inputs', lambda *a: None)
    monkeypatch.setattr('scrutare.engine.review_inputs.prepared_content_hashes', lambda *a: {})
    monkeypatch.setattr('scrutare.replay.replay_run', lambda *a: SimpleNamespace(exit_code=0))
    return root, directory, row, ledger, gate


def test_inspector_validates_a_bound_attempt(tmp_path, monkeypatch):
    root, _, _, ledger, gate = fixture(tmp_path, monkeypatch)
    result = inspect_rule_a(root, gate, ledger)
    assert result['tests']['every_attempt_terminal'] is True
    assert result['tests']['all_attempts_reconciled'] is True
    assert result['cells'][0]['replay_valid'] is True
    assert result['tests']['all_attempts_provenance_routing'] is True
    assert result['qualification'] == 'not_qualified'  # one cell is not 36


@pytest.mark.parametrize('matching', [False, True])
def test_supplement_must_charge_its_own_cell_attempt_session(tmp_path, monkeypatch, matching):
    root, directory, _, ledger, gate = fixture(tmp_path, monkeypatch)
    path = directory / 'sessions/senior-dev/attempt-0001/result.json'
    terminal = json.loads(path.read_text())
    terminal.update(status='failed', reason='timeout', accounting_complete=False)
    write(path, terminal)
    fanout = json.loads((directory / 'fanout.json').read_text())
    fanout['result']['outcomes'] = [terminal]
    write(directory / 'fanout.json', fanout)
    write(directory / 'panel.json', {'status': 'failed', 'reason': 'initial',
                                   'initial': {'result': {'outcomes': [terminal]}}})
    (directory / 'artifacts.json').unlink()
    write_artifact_manifest(directory)
    ledger['requests'].append(dict(ledger['requests'][0], id='missing',
        attempt=1 if matching else 2,
        usage=TokenUsage(input=200000).to_dict(), stream=None, event=None))
    ledger['supplements'].append({'id': 'missing', 'run_id': 'a/aggregate/1', 'attempt': 1,
        'session': 'sessions/senior-dev/attempt-0001', 'reason': 'missing_failed_request',
        'session_result_sha256': sha256(path.read_bytes()).hexdigest()})
    result = inspect_rule_a(root, gate, ledger)
    assert result['tests']['all_attempts_reconciled'] is matching
    if matching:
        assert result['tests']['all_attempts_within_ceiling'] is False
        assert result['cells'][0]['attempts'][0]['tokens'] == 200002


def test_accepted_directory_must_be_the_selected_attempt(tmp_path, monkeypatch):
    root, _, row, ledger, gate = fixture(tmp_path, monkeypatch)
    borrowed = root / 'unlisted'
    write(borrowed / 'verdict.json', {'findings': []})
    row['run_dir'] = str(borrowed)
    write(root / 'experiment.json', {'status': 'complete', 'runs': [row],
                                   'cumulative_usage': row['usage']})
    assert inspect_rule_a(root, gate, ledger)['cells'][0]['replay_valid'] is False


def test_missing_required_terminal_cannot_be_an_accepted_complete_attempt(tmp_path, monkeypatch):
    root, directory, _, ledger, gate = fixture(tmp_path, monkeypatch)
    fanout = json.loads((directory / 'fanout.json').read_text())
    missing = dict(fanout['result']['outcomes'][0], persona='security',
                  artifact_directory=str(directory / 'sessions/security/attempt-0001'))
    fanout['personas'].append({'name': 'security'})
    fanout['result']['outcomes'].append(missing)
    write(directory / 'fanout.json', fanout)
    (directory / 'artifacts.json').unlink()
    write_artifact_manifest(directory)
    result = inspect_rule_a(root, gate, ledger)
    assert result['tests']['every_attempt_terminal'] is False
    assert result['cells'][0]['replay_valid'] is False


def test_snapshot_holdout_use_cannot_be_hidden_by_gate_flag(tmp_path, monkeypatch):
    root, _, _, ledger, gate = fixture(tmp_path, monkeypatch)
    path = root / 'snapshot.json'
    snapshot = json.loads(path.read_text())
    snapshot['cases'][0]['split'] = 'holdout'
    write(path, snapshot)
    result = inspect_rule_a(root, gate, ledger)
    assert result['tests']['holdout_untouched'] is False
    assert result['holdout_used'] is True


@pytest.mark.parametrize('base', ['http://192.168.3.40:8045/v1',
                                 'http://192.168.3.40:8045/v1/'])
def test_spark_v1_addresses_with_trailing_slash_are_self_hosted(tmp_path, monkeypatch, base):
    root, _, _, ledger, gate = fixture(tmp_path, monkeypatch)
    ledger['requests'][0]['api_base'] = base
    assert inspect_rule_a(root, gate, ledger)['tests']['all_attempts_provenance_routing'] is True
