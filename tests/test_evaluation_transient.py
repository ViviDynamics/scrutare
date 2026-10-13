"""Upstream HTTP 5xx cells rerun in fresh directories; every attempt stays recorded."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_context import complete, patch_engine
from test_evaluation_runner import config, corpus

from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.evaluation.runner import comparison_configs, run_experiment
from scrutare.evaluation.transient import transient_provider_failures

OVERLOAD = ('RuntimeError: chat completion failed with HTTP 500: {"error":{"message":'
            '"InternalServerError: Our servers are currently overloaded."}}')


def session(directory, persona, *, status='complete', reason='done', error=None, attempt=1):
    path = directory / f'sessions/{persona}/attempt-{attempt:04d}'
    path.mkdir(parents=True)
    (path / 'result.json').write_text(json.dumps({
        'session_key': f'{persona}/attempt-{attempt:04d}', 'persona': persona,
        'status': status, 'reason': reason,
        'usage': {'input': 7, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 7}}))
    events = [{'type': 'progress', 'text': 'context window'}]
    if error is not None or status == 'complete':
        events.append({'type': 'result', 'status': 'error' if error else 'done',
                       'error': error})
    (path / 'stdout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    return path


def test_only_retained_http_5xx_provider_failures_are_transient(tmp_path):
    session(tmp_path, 'senior-dev')
    session(tmp_path, 'security', status='failed', reason='provider', error=OVERLOAD)
    session(tmp_path, 'devops', status='not_started', reason='review_budget')
    assert transient_provider_failures(tmp_path) == [
        {'session_key': 'security/attempt-0001', 'http_status': 500}]


@pytest.mark.parametrize('status,reason,error', [
    ('failed', 'provider', 'RuntimeError: chat completion failed with HTTP 400: bad request'),
    ('failed', 'provider', 'RuntimeError: chat completion failed with HTTP 429: slow down'),
    ('failed', 'provider', 'ValueError: something mentioning HTTP 500 elsewhere'),
    ('failed', 'provider', None),
    ('failed', 'protocol', OVERLOAD),
    ('failed', 'citation_validation', OVERLOAD),
    ('failed', 'timeout', OVERLOAD),
    ('partial', 'budget', OVERLOAD),
])
def test_other_failures_make_the_whole_attempt_ineligible(tmp_path, status, reason, error):
    session(tmp_path, 'security', status='failed', reason='provider', error=OVERLOAD)
    session(tmp_path, 'devops', status=status, reason=reason, error=error)
    assert transient_provider_failures(tmp_path) is None


def test_complete_missing_or_unreadable_evidence_is_never_transient(tmp_path):
    assert transient_provider_failures(tmp_path) is None
    session(tmp_path, 'senior-dev')
    assert transient_provider_failures(tmp_path) is None
    broken = session(tmp_path, 'security', status='failed', reason='provider', error=OVERLOAD)
    (broken / 'stdout.jsonl').write_bytes(b'\xff not json\n')
    assert transient_provider_failures(tmp_path) is None
    (broken / 'stdout.jsonl').unlink()
    assert transient_provider_failures(tmp_path) is None
    (broken / 'result.json').write_text('[]')
    assert transient_provider_failures(tmp_path) is None


def overloaded(directory):
    session(directory, 'senior-dev', status='failed', reason='provider', error=OVERLOAD)
    return SimpleNamespace(status='failed', accounting_complete=False,
                           usage=TokenUsage(input=7), verdict=None)


def run(cases, output, **options):
    return asyncio.run(run_experiment(cases, config(), output,
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline', **options))


def test_transient_cell_reruns_fresh_and_retains_every_attempt(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    seen = []
    async def execute(directory, conf, *, runtime):
        seen.append(directory)
        assert not (directory / 'sessions').exists()
        assert (directory / 'review-inputs/diff.patch').read_bytes() == (
            cases[0].capture / 'diff.patch').read_bytes()
        if directory.name == '1' and directory.parent.name == 'senior':
            return overloaded(directory)
        if directory.name == 'attempt-0002':
            return overloaded(directory)
        return complete()
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=2, variant_names=('senior',))
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    assert snapshot['transient_retry']['max_retries'] == 2
    assert snapshot['transient_retry']['delay_seconds'] == 0
    assert [r['run_id'] for r in result['runs']] == [f'a/senior/{n}' for n in (1, 2, 3)]
    first, second, third = result['runs']
    retries = tmp_path / 'out/_retries/a/senior/1'
    assert seen[0] == tmp_path / 'out/a/senior/1'
    assert [str(d) for d in seen if 'attempt-' in d.name] == [
        str(retries / 'attempt-0002'), str(retries / 'attempt-0003')]
    assert first['status'] == 'complete' and first['accounting_complete']
    assert first['run_dir'] == str(retries / 'attempt-0003')
    assert first['usage']['total'] == 1
    assert first['retry_usage']['total'] == 14
    assert [(a['attempt'], a['status'], a['run_dir']) for a in first['attempts']] == [
        (1, 'failed', str(tmp_path / 'out/a/senior/1')),
        (2, 'failed', str(retries / 'attempt-0002')),
        (3, 'complete', str(retries / 'attempt-0003'))]
    assert [a['transient_provider_failures'] for a in first['attempts']] == [
        [{'session_key': 'senior-dev/attempt-0001', 'http_status': 500}]] * 2 + [None]
    assert first['latency_seconds'] >= sum(a['latency_seconds'] for a in first['attempts'])
    for attempt in first['attempts']:
        saved = json.loads((Path(attempt['run_dir']) / 'evaluation.json').read_text())
        assert saved['status'] == attempt['status'] and saved['attempt'] == attempt['attempt']
        assert (Path(attempt['run_dir']) / 'artifacts.json').is_file()
    for untouched in (second, third):
        assert len(untouched['attempts']) == 1 and untouched['retry_usage']['total'] == 0
    lines = [json.loads(line) for line in (tmp_path / 'out/runs.jsonl').read_text().splitlines()]
    assert len(lines) == 3 and sorted(r['run_id'] for r in lines) == [
        r['run_id'] for r in result['runs']]


def test_retry_bound_is_exhausted_and_failure_stays_in_the_schedule(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    async def execute(directory, conf, *, runtime):
        calls.append(directory)
        return overloaded(directory)
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=1, variant_names=('senior',))
    assert len(calls) == 6
    assert all(r['status'] == 'failed' and not r['accounting_complete']
               and len(r['attempts']) == 2 and r['retry_usage']['total'] == 7
               for r in result['runs'])
    assert result['status'] == 'complete'


def test_ineligible_failure_and_exception_are_not_retried(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    async def execute(directory, conf, *, runtime):
        calls.append(directory)
        if directory.name == '1':
            session(directory, 'senior-dev', status='partial', reason='budget')
            return SimpleNamespace(status='partial', accounting_complete=True,
                                   usage=TokenUsage(input=7), verdict=None)
        if directory.name == '2':
            session(directory, 'senior-dev', status='failed', reason='provider', error=OVERLOAD)
            raise RuntimeError('evaluator defect')
        return complete()
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=3, variant_names=('senior',))
    assert len(calls) == 3
    assert [r['status'] for r in result['runs']] == ['invalid', 'failed', 'complete']
    assert all(len(r['attempts']) == 1 for r in result['runs'])
    assert result['runs'][1]['error'] == 'RuntimeError'
    assert not (tmp_path / 'out/_retries').exists()


def test_default_never_retries_and_keeps_existing_record_shape(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    async def execute(directory, conf, *, runtime):
        calls.append(directory)
        return overloaded(directory)
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out')
    assert len(calls) == 9
    assert all('attempts' not in r and 'retry_usage' not in r for r in result['runs'])
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    assert snapshot['transient_retry']['max_retries'] == 0


def test_retry_refuses_inputs_that_differ_from_the_frozen_snapshot(tmp_path, monkeypatch):
    from scrutare.evaluation import runner
    cases = corpus(tmp_path / 'corpus')
    calls = []
    original = runner.prepared_content_hashes
    async def execute(directory, conf, *, runtime):
        calls.append(directory)
        monkeypatch.setattr(runner, 'prepared_content_hashes',
                            lambda inputs: {**original(inputs), 'diff.patch': 'changed'})
        return overloaded(directory)
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=2, variant_names=('senior',))
    assert len(calls) == 3
    assert all(r['status'] == 'failed' and r['error'] == 'RetryInputsDiffer'
               and len(r['attempts']) == 1 for r in result['runs'])


@pytest.mark.parametrize('options', [{'transient_retries': -1}, {'transient_retries': 6},
                                     {'transient_retries': True},
                                     {'transient_retry_delay_seconds': -1},
                                     {'transient_retry_delay_seconds': float('nan')},
                                     {'transient_retry_delay_seconds': 601}])
def test_retry_policy_is_bounded(tmp_path, monkeypatch, options):
    patch_engine(monkeypatch, lambda *a, **k: None)
    with pytest.raises(ValueError):
        run(corpus(tmp_path / 'corpus'), tmp_path / 'out', **options)
    assert not (tmp_path / 'out').exists()


def test_retry_waits_the_declared_delay_between_attempts(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    async def execute(directory, conf, *, runtime):
        return overloaded(directory) if directory.name == '1' else complete()
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=1,
                 transient_retry_delay_seconds=0.05, variant_names=('senior',))
    assert result['runs'][0]['status'] == 'complete'
    assert result['runs'][0]['latency_seconds'] >= 0.05


def test_variant_subset_keeps_comparison_order_and_rejects_unknown_arms(tmp_path, monkeypatch):
    assert list(comparison_configs(config(), comparison_set='architectures',
                                   variant_names=('routing', 'evidence'))) == [
        'evidence', 'routing']
    for names in (('invented',), (), ('panel', 'panel'), 'panel'):
        with pytest.raises(ValueError, match='variant'):
            comparison_configs(config(), variant_names=names)
    cases = corpus(tmp_path / 'corpus')
    async def execute(directory, conf, *, runtime):
        return complete()
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', variant_names=('debate', 'senior'))
    assert [r['run_id'] for r in result['runs']] == [
        f'a/{variant}/{repeat}' for variant in ('senior', 'debate') for repeat in (1, 2, 3)]
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    assert set(snapshot['configs']) == set(snapshot['variant_personas']) == {'senior', 'debate'}
    assert snapshot['variant_selection'] == ['senior', 'debate']


def test_cli_passes_explicit_arms_and_retry_policy(tmp_path, monkeypatch):
    import sys

    from scrutare.evaluation import __main__ as cli
    corpus(tmp_path / 'corpus')
    (tmp_path / 'config.yaml').write_text(
        'models: {default: {provider: openai, model: frozen-v1}}\n'
        'budgets: {review_max_tokens: 400, per_persona_tokens: 100}\n')
    seen = {}
    async def experiment(cases, conf, output, **options):
        seen.update(options)
    monkeypatch.setattr(cli, 'run_experiment', experiment)
    monkeypatch.setattr(sys, 'argv', ['scrutare.evaluation', 'run', '--corpus',
        str(tmp_path / 'corpus'), '--config', str(tmp_path / 'config.yaml'), '--output',
        str(tmp_path / 'out'), '--nare-executable', '/unused', '--evidence-kind', 'offline',
        '--variants', 'senior,debate', '--transient-retries', '2',
        '--transient-retry-delay-seconds', '1.5'])
    cli.main()
    assert seen['variant_names'] == ('senior', 'debate')
    assert seen['transient_retries'] == 2 and seen['transient_retry_delay_seconds'] == 1.5
    monkeypatch.setattr(sys, 'argv', sys.argv[:12])
    cli.main()
    assert seen['variant_names'] is None and seen['transient_retries'] == 0
