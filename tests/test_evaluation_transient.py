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
from scrutare.evaluation.transient import (
    model_repetition_failures,
    transient_provider_failures,
)

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
    ('failed', 'provider', OVERLOAD.replace('HTTP 500', 'HTTP 501')),
    ('failed', 'provider', OVERLOAD.replace('HTTP 500', 'HTTP 505')),
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


@pytest.mark.parametrize('name,reason,eligible', [
    ('panel.json', 'discovery_incomplete', True), ('debate.json', 'discovery_incomplete', True),
    ('panel.json', 'initial', True), ('panel.json', 'initial_evidence', False),
    ('debate.json', 'perspectives_incomplete', True),
    ('debate.json', 'reassessment_incomplete', True), ('debate.json', 'perspective_budget', False),
    ('debate.json', 'evidence', False), ('panel.json', 'converged', False),
    ('debate.json', None, False),
])
def test_review_level_stop_must_be_one_an_interruption_explains(tmp_path, name, reason, eligible):
    session(tmp_path, 'security', status='failed', reason='provider', error=OVERLOAD)
    (tmp_path / name).write_text(json.dumps({'status': 'failed', 'reason': reason}
                                            if reason else ['malformed']))
    assert (transient_provider_failures(tmp_path) is not None) is eligible


@pytest.mark.parametrize('code', [500, 502, 503, 504, 529])
def test_each_server_unavailability_status_is_reported(tmp_path, code):
    session(tmp_path, 'security', status='failed', reason='provider',
            error=OVERLOAD.replace('HTTP 500', f'HTTP {code}'))
    assert transient_provider_failures(tmp_path) == [
        {'session_key': 'security/attempt-0001', 'http_status': code}]


# nare 2026.10.4+ streaming rail: an error frame mid-stream (#86). The provider's error
# object is reported as its Python repr, and its code may be a string or an integer.
def mid_stream(code="'500'", message='litellm.InternalServerError: upstream connection lost'):
    return ("RuntimeError: stream provider error: {'message': " + repr(message)
            + ", 'type': 'internal_server_error', 'param': None, 'code': " + code + "}")


# The exact c1 record (tests-consumer-fixture/aggregate/3, 2026-10-22): LiteLLM aborted a
# stream whose model had finished its answer and then emitted " {HEAD} |" over and over.
REPETITION = ("RuntimeError: stream provider error: {'message': 'litellm.InternalServerError: "
              "The model is repeating the same chunk =  {HEAD} | .', 'type': "
              "'internal_server_error', 'param': None, 'code': '500'}")


@pytest.mark.parametrize('code,status', [
    ("'500'", 500), ("'502'", 502), ("'503'", 503), ("'504'", 504), ("'529'", 529),
    ('503', 503),
])
def test_a_mid_stream_server_unavailability_is_transient(tmp_path, code, status):
    session(tmp_path, 'security', status='failed', reason='provider', error=mid_stream(code))
    assert transient_provider_failures(tmp_path) == [
        {'session_key': 'security/attempt-0001', 'http_status': status}]
    assert model_repetition_failures(tmp_path) == []


@pytest.mark.parametrize('error', [
    mid_stream("'400'"), mid_stream("'429'"), mid_stream("'501'"), mid_stream("'505'"),
    mid_stream('None'), mid_stream('True'), mid_stream("'5OO'"), mid_stream("' 500'"),
    mid_stream("'0500'"), mid_stream("'" + '5' * 5000 + "'"),
    "RuntimeError: stream provider error: 'upstream went away'",
    "RuntimeError: stream provider error: {'message': 'x'}",
    "RuntimeError: stream provider error: {'code': '500'",
    "RuntimeError: stream provider error: {'code': __import__('os')}",
    "ValueError: stream provider error: {'code': '500'}",
    mid_stream() + ' trailing',
])
def test_other_mid_stream_errors_are_not_transient(tmp_path, error):
    session(tmp_path, 'security', status='failed', reason='provider', error=error)
    assert transient_provider_failures(tmp_path) is None
    assert model_repetition_failures(tmp_path) == []


@pytest.mark.parametrize('error', [
    REPETITION,
    REPETITION.replace("'code': '500'", "'code': 500"),
    'RuntimeError: chat completion failed with HTTP 500: {"error":{"message":"litellm.'
    'InternalServerError: The model is repeating the same chunk = x."}}',
])
def test_a_repetition_abort_is_a_model_failure_classified_apart_and_never_rerun(
        tmp_path, error):
    session(tmp_path, 'senior-dev')
    session(tmp_path, 'devops', status='failed', reason='provider', error=error)
    assert transient_provider_failures(tmp_path) is None
    assert model_repetition_failures(tmp_path) == [
        {'session_key': 'devops/attempt-0001', 'http_status': 500}]
    # A genuine interruption beside it does not make the attempt eligible.
    session(tmp_path, 'security', status='failed', reason='provider', error=mid_stream())
    assert transient_provider_failures(tmp_path) is None


@pytest.mark.parametrize('error', [
    REPETITION.replace("'code': '500'", "'code': None"),
    REPETITION.replace(", 'code': '500'", ''),
])
def test_a_repetition_abort_without_a_status_is_still_recorded(tmp_path, error):
    session(tmp_path, 'devops', status='failed', reason='provider', error=error)
    assert transient_provider_failures(tmp_path) is None
    assert model_repetition_failures(tmp_path) == [
        {'session_key': 'devops/attempt-0001', 'http_status': None}]


def test_repetition_text_outside_nare_s_provider_reports_is_not_classified(tmp_path):
    session(tmp_path, 'devops', status='failed', reason='provider',
            error='ValueError: The model is repeating the same chunk')
    assert model_repetition_failures(tmp_path) == []


def test_one_unreadable_session_does_not_hide_another_s_repetition(tmp_path):
    session(tmp_path, 'devops', status='failed', reason='provider', error=REPETITION)
    broken = session(tmp_path, 'security', status='failed', reason='provider',
                     error=mid_stream())
    (broken / 'stdout.jsonl').write_bytes(b'\xff not json\n')
    assert transient_provider_failures(tmp_path) is None
    assert model_repetition_failures(tmp_path) == [
        {'session_key': 'devops/attempt-0001', 'http_status': 500}]


def test_only_a_provider_failure_is_classified_as_repetition(tmp_path):
    session(tmp_path, 'devops', status='failed', reason='protocol', error=REPETITION)
    assert model_repetition_failures(tmp_path) == []


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


def test_cancellation_during_the_wait_keeps_the_spent_attempt_in_the_job(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    async def execute(directory, conf, *, runtime):
        return overloaded(directory)
    patch_engine(monkeypatch, execute)
    async def job():
        task = asyncio.create_task(run_experiment(cases, config(), tmp_path / 'out',
            runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
            transient_retries=1, transient_retry_delay_seconds=30, variant_names=('senior',)))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(job())
    saved = json.loads((tmp_path / 'out/experiment.json').read_text())
    lines = [json.loads(line) for line in (tmp_path / 'out/runs.jsonl').read_text().splitlines()]
    assert saved['status'] == 'incomplete' and len(lines) == 1
    first = saved['runs'][0]
    assert first == lines[0] and first['error'] == 'Interrupted'
    assert first['status'] == 'failed' and first['usage']['total'] == 7
    assert [a['status'] for a in first['attempts']] == ['failed', 'refused']
    assert first['attempts'][0]['transient_provider_failures']
    assert [r['status'] for r in saved['runs'][1:]] == ['missing', 'missing']


def test_retry_preparation_failure_is_recorded_without_stopping_the_job(tmp_path, monkeypatch):
    from scrutare.evaluation import runner
    cases = corpus(tmp_path / 'corpus')
    original = runner.prepare_review_inputs
    def prepare(directory, conf):
        if 'attempt-' in directory.name:
            raise OSError('disk')
        return original(directory, conf)
    async def execute(directory, conf, *, runtime):
        return overloaded(directory) if directory.name == '2' else complete()
    patch_engine(monkeypatch, execute)
    monkeypatch.setattr(runner, 'prepare_review_inputs', prepare)
    result = run(cases, tmp_path / 'out', transient_retries=1, variant_names=('senior',))
    assert result['status'] == 'complete'
    assert [r['status'] for r in result['runs']] == ['complete', 'failed', 'complete']
    assert result['runs'][1]['error'] == 'OSError'
    assert [a['status'] for a in result['runs'][1]['attempts']] == ['failed', 'refused']


def test_focused_arm_interrupted_upstream_reruns_with_its_trace(tmp_path, monkeypatch):
    from test_evaluation_context import declared
    cases = declared(tmp_path / 'corpus')
    trace = {'schema_version': 2, 'kind': 'focused_evidence_discussion',
             'bypassed_candidate_ids': [], 'rounds': [], 'final_assessments': []}
    async def execute(directory, conf, *, runtime):
        if directory.name == '1':
            (directory / 'debate.json').write_text(json.dumps(
                {**trace, 'status': 'failed', 'reason': 'discovery_incomplete'}))
            return overloaded(directory)
        (directory / 'debate.json').write_text(json.dumps(
            {**trace, 'status': 'complete', 'reason': 'undisputed'}))
        return complete()
    patch_engine(monkeypatch, execute)
    from test_evaluation_context import context_config
    result = asyncio.run(run_experiment(cases, context_config(), tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='focused', variant_names=('focused1',), transient_retries=1))
    first = result['runs'][0]
    assert first['status'] == 'complete' and len(first['attempts']) == 2
    assert first['discussion_trace']['reason'] == 'undisputed'
    assert first['attempts'][0]['transient_provider_failures'] == [
        {'session_key': 'senior-dev/attempt-0001', 'http_status': 500}]
    assert all(len(r['attempts']) == 1 for r in result['runs'][1:])


def test_scoring_counts_retry_spend_and_marks_its_accounting_uncertain():
    from scrutare.evaluation.scoring import score_experiment
    usage = {'input': 5, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 5}
    plain = {'run_id': 'a/senior/1', 'case_id': 'a', 'variant': 'senior', 'repeat': 1,
             'status': 'complete', 'accounting_complete': True, 'usage': usage,
             'findings': [], 'latency_seconds': 1.0}
    retried = {**plain, 'run_id': 'a/senior/2', 'repeat': 2,
               'retry_usage': {**usage, 'input': 7, 'total': 7},
               'attempts': [{'attempt': 1, 'status': 'failed', 'accounting_complete': False},
                            {'attempt': 2, 'status': 'complete', 'accounting_complete': True}]}
    labels = {'a': {'case_id': 'a', 'defects': [], 'counterexamples': []}}
    before = score_experiment([plain], labels, [])['senior']
    assert (before['tokens'], before['retried_runs'], before['retry_tokens'],
            before['accounting_failures'], before['quality_final']) == (5, 0, 0, 0, True)
    after = score_experiment([plain, retried], labels, [])['senior']
    assert (after['tokens'], after['retried_runs'], after['retry_tokens']) == (17, 1, 7)
    assert after['accounting_failures'] == 1 and after['quality_final'] is False
    assert after['complete_runs'] == 2
    refused = {**plain, 'run_id': 'a/senior/3', 'repeat': 3, 'status': 'failed',
               'accounting_complete': False, 'retry_usage': {**usage, 'input': 0, 'total': 0},
               'attempts': [{'attempt': 1, 'status': 'failed', 'accounting_complete': False},
                            {'attempt': 2, 'status': 'refused', 'accounting_complete': True}]}
    assert score_experiment([plain, refused], labels, [])['senior']['retried_runs'] == 0


def test_job_retry_budget_bounds_total_reruns_and_records_the_denial(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    async def execute(directory, conf, *, runtime):
        calls.append(directory)
        return overloaded(directory)
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=3, transient_retry_budget=2,
                 variant_names=('senior',))
    assert len(calls) == 5
    assert sorted(len(r['attempts']) for r in result['runs']) == [1, 1, 3]
    assert sum(len(r['attempts']) - 1 for r in result['runs']) == 2
    denied = [r for r in result['runs'] if r.get('retry_denied') == 'job_retry_budget']
    # The cell that spent both reruns was still eligible for a third and is denied too.
    assert len(denied) == 3 and all(r['status'] == 'failed' and r['attempts'][-1][
        'transient_provider_failures'] for r in denied)
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    assert snapshot['transient_retry']['max_job_retries'] == 2


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
    assert all(set(r) == {'run_id', 'case_id', 'variant', 'repeat', 'run_dir', 'status',
                          'accounting_complete', 'usage', 'findings', 'error',
                          'latency_seconds'} for r in result['runs'])
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
    for record in result['runs']:
        failed, refused = record['attempts']
        assert record['status'] == failed['status'] == 'failed'
        assert record['error'] == refused['error'] == 'RetryInputsDiffer'
        assert refused['status'] == 'refused' and refused['usage']['total'] == 0
        assert record['run_dir'] == failed['run_dir'] != refused['run_dir']
        assert Path(refused['run_dir']).is_dir()
        assert not (Path(refused['run_dir']) / 'evaluation.json').exists()
        assert record['usage']['total'] == 7 and record['retry_usage']['total'] == 0


@pytest.mark.parametrize('options', [{'transient_retries': -1}, {'transient_retries': 6},
                                     {'transient_retries': True},
                                     {'transient_retry_delay_seconds': -1},
                                     {'transient_retry_delay_seconds': float('nan')},
                                     {'transient_retry_delay_seconds': 601},
                                     {'transient_retry_budget': -1},
                                     {'transient_retry_budget': 1.5}])
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
        '--transient-retry-delay-seconds', '1.5', '--transient-retry-budget', '4'])
    cli.main()
    assert seen['variant_names'] == ('senior', 'debate')
    assert seen['transient_retries'] == 2 and seen['transient_retry_delay_seconds'] == 1.5
    assert seen['transient_retry_budget'] == 4
    monkeypatch.setattr(sys, 'argv', sys.argv[:12])
    cli.main()
    assert seen['variant_names'] is None and seen['transient_retries'] == 0
    assert seen['transient_retry_budget'] is None


def test_a_repetition_abort_is_recorded_and_not_rerun(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    async def execute(directory, conf, *, runtime):
        calls.append(directory)
        if directory.name == '1':
            session(directory, 'senior-dev', status='failed', reason='provider',
                    error=REPETITION)
            return SimpleNamespace(status='failed', accounting_complete=False,
                                   usage=TokenUsage(input=7), verdict=None)
        return complete()
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=3, variant_names=('senior',))
    assert len(calls) == 3 and not (tmp_path / 'out/_retries').exists()
    first, *others = result['runs']
    assert first['status'] == 'failed' and len(first['attempts']) == 1
    attempt = first['attempts'][0]
    assert attempt['transient_provider_failures'] is None
    assert attempt['model_repetition'] == [
        {'session_key': 'senior-dev/attempt-0001', 'http_status': 500}]
    # Attempts without a repetition abort keep the existing record shape.
    assert all('model_repetition' not in a for run in others for a in run['attempts'])


def test_an_evaluator_error_is_not_recorded_as_a_repetition(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    async def execute(directory, conf, *, runtime):
        if directory.name == '1':
            session(directory, 'senior-dev', status='failed', reason='provider',
                    error=REPETITION)
            raise RuntimeError('evaluator defect')
        return complete()
    patch_engine(monkeypatch, execute)
    result = run(cases, tmp_path / 'out', transient_retries=3, variant_names=('senior',))
    first = result['runs'][0]
    assert first['error'] == 'RuntimeError'
    assert 'model_repetition' not in first['attempts'][0]
