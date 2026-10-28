"""Rule A replays a failed pipeline once, never searches for better answers."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_runner import config, corpus
from test_evaluation_transient import session

from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.evaluation.runner import run_experiment


def execute(tmp_path, monkeypatch, *, recovery='complete', first='output_cap', **options):
    cases = corpus(tmp_path / 'corpus')
    original = (cases[0].capture / 'diff.patch').read_bytes()
    calls = []
    async def review(directory, conf, *, runtime):
        retry = '_retries' in directory.parts
        calls.append((directory, retry))
        assert (directory / 'diff.patch').read_bytes() == original
        (cases[0].capture / 'diff.patch').write_bytes(b'changed after capture')
        reason = recovery if retry else first
        status = 'complete' if reason == 'complete' else 'failed'
        path = session(directory, 'senior-dev', status=status, reason=reason)
        (path / 'invocation.json').write_text(json.dumps(
            {'rail': conf.models.for_persona('senior-dev').record()}))
        with (path / 'stdout.jsonl').open('a') as stream:
            stream.write(json.dumps({'type': 'cost', 'text': '', 'timestamp': 'frozen',
                'detail': {'input': 7, 'output': 0, 'cache_read': 0, 'cache_write': 0,
                           'cost': None}}) + '\n')
        events = [dict(e, text=e.get('text', ''), detail=e.get('detail', {}),
                       timestamp='frozen') for e in map(json.loads,
                        (path / 'stdout.jsonl').read_text().splitlines())]
        (path / 'stdout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        saved = json.loads((path / 'result.json').read_text())
        saved['accounting_complete'] = True
        saved['nare_status'] = 'done' if reason in ('complete', 'protocol') else 'error'
        saved['artifact_directory'] = str(path)
        (directory / 'panel.json').write_text(json.dumps({'status': 'failed', 'reason': 'initial'}))
        (directory / 'fanout.json').write_text(json.dumps({
            'personas': [{'name': saved['persona']}], 'result': {'outcomes': [saved]}}))
        (path / 'result.json').write_text(json.dumps(saved))
        return SimpleNamespace(status=status, accounting_complete=True,
            usage=TokenUsage(input=7), verdict=SimpleNamespace(findings=())
            if status == 'complete' else None)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', review)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='2026.10.12', contract=2)))
    result = asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        variant_names=('senior',), qualification_rule_a=True, **options))
    return result, calls


def test_rule_a_replays_whole_cell_after_all_originals_with_frozen_inputs(tmp_path, monkeypatch):
    result, calls = execute(tmp_path, monkeypatch)
    assert [retry for _, retry in calls] == [False] * 3 + [True] * 3
    for row in result['runs']:
        assert row['status'] == 'complete' and row['selected_attempt'] == 2
        assert len(row['attempts']) == 2
        assert row['usage']['total'] == 7
        assert row['cumulative_usage']['total'] == 14
        assert row['attempts'][0]['status'] == 'failed'
        for attempt in row['attempts']:
            root = Path(attempt['run_dir'])
            assert (root / 'artifacts.json').is_file()
            assert json.loads((root / 'qualification-policy.json').read_text())['rule'] == 'A'
    assert result['cumulative_usage']['total'] == 42


def test_rule_a_second_failure_stops_and_discards_failed_answers(tmp_path, monkeypatch):
    result, calls = execute(tmp_path, monkeypatch, recovery='output_cap')
    assert len(calls) == 6
    assert all(row['status'] == 'failed' and row['findings'] == []
               and row['selected_attempt'] is None for row in result['runs'])


@pytest.mark.parametrize('reason', ['complete', 'protocol', 'citation_validation', 'inputs',
                                   'artifacts'])
def test_success_or_invalid_answer_or_hard_failure_never_retries(tmp_path, monkeypatch, reason):
    result, calls = execute(tmp_path, monkeypatch, first=reason)
    assert len(calls) == 3
    assert all(len(row['attempts']) == 1 for row in result['runs'])


def test_rule_a_cannot_combine_with_transient_retries(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='transient'):
        execute(tmp_path, monkeypatch, transient_retries=1)


def test_hard_cost_mismatch_is_not_retryable(tmp_path):
    from scrutare.engine.recovery import retryable_sessions
    root = tmp_path / 'run'
    path = session(root, 'senior-dev', status='failed', reason='timeout')
    data = json.loads((path / 'result.json').read_text())
    data['accounting_complete'] = False
    (path / 'result.json').write_text(json.dumps(data))
    assert retryable_sessions(root) is None


@pytest.mark.parametrize('reason', ['inputs', 'artifacts', 'initial_evidence'])
def test_pipeline_hard_failure_prevents_recovery_even_with_timeout(tmp_path, monkeypatch, reason):
    from scrutare.engine.recovery import retryable_sessions
    result, calls = execute(tmp_path, monkeypatch)
    directory = calls[0][0]
    (directory / 'panel.json').write_text(json.dumps({'status': 'failed', 'reason': reason}))
    assert retryable_sessions(directory) is None


def test_missing_pipeline_session_terminal_is_a_hard_failure(tmp_path, monkeypatch):
    from scrutare.engine.recovery import retryable_sessions
    _, calls = execute(tmp_path, monkeypatch)
    directory = calls[0][0]
    fanout = json.loads((directory / 'fanout.json').read_text())
    fanout['result']['outcomes'].append(dict(fanout['result']['outcomes'][0],
        persona='missing', artifact_directory=str(directory / 'sessions/missing/attempt-0001')))
    fanout['personas'].append({'name': 'missing'})
    (directory / 'fanout.json').write_text(json.dumps(fanout))
    assert retryable_sessions(directory) is None
