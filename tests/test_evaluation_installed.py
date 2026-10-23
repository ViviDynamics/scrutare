"""Evaluation through separately installed nare with deterministic provider substitution."""
import asyncio
import json
from pathlib import Path

import pytest
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import offline_runtime, text, tool
from test_review_inputs import capture as capture

from scrutare.config import parse_config
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.runner import run_experiment


@pytest.mark.parametrize('comparison_set', ['baseline', 'procedures'])
def test_actual_nare_evaluation_never_posts_and_all_runs_account(
    tmp_path, installed, monkeypatch, comparison_set,
):
    runtime = offline_runtime(tmp_path, installed, default={
        'replies': [tool(), text({'findings': []})]})
    spec_path = tmp_path / 'offline-spec.json'
    spec = json.loads(spec_path.read_text())
    spec['scenarios'] = [{'purpose': 'review', 'attempt': 'attempt-0001',
                         'prompt_prefix': 'Arbitrate',
                         'scenario': {'replies': [text({'findings': [], 'converged': True})]}}]
    spec_path.write_text(json.dumps(spec))
    # Any ambient GitHub use must fail, including read-only calls.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    gh = bin_dir / 'gh'
    gh.write_text('#!/bin/sh\nprintf forbidden > "$GH_FORBIDDEN"\nexit 99\n')
    gh.chmod(0o700)
    monkeypatch.setenv('PATH', f'{bin_dir}:/usr/bin:/bin')
    monkeypatch.setenv('GH_FORBIDDEN', str(tmp_path / 'github-called'))
    cases = load_corpus(Path(__file__).resolve().parents[1] / 'evaluations/pilot')[:1]
    config = parse_config(b'models: {default: {provider: openai, model: offline-model, '
                          b'base_url: "https://offline.invalid/v1"}}\n'
                          b'budgets: {review_max_tokens: 500, per_persona_tokens: 100}\n')
    result = asyncio.run(run_experiment(cases, config, tmp_path / 'experiment',
                                       runtime=runtime, evidence_kind='offline',
                                       comparison_set=comparison_set))
    assert len(result['runs']) == 9
    assert all(r['status'] == 'complete' and r['accounting_complete'] for r in result['runs'])
    assert all(r['usage']['total'] > 0 for r in result['runs'])
    assert not (tmp_path / 'github-called').exists()
    for run in result['runs']:
        directory = Path(run['run_dir'])
        assert not (directory / 'posting.json').exists()
        if run['variant'] == 'revised5':
            assert (directory / 'sessions/testing-verification/attempt-0001/session.json').exists()
        for path in directory.glob('sessions/*/attempt-*/session.json'):
            session = json.loads(path.read_text())
            assert session['policy']['root'] == str(directory / 'review-inputs')
            assert session['policy']['tools'] == ['read']


def test_actual_nare_http_500_reruns_the_cell_and_retains_the_failed_attempt(
    tmp_path, installed,
):
    from scrutare.evaluation.transient import transient_provider_failures
    overload = ('chat completion failed with HTTP 500: {"error":{"message":'
                '"InternalServerError: Our servers are currently overloaded."}}')
    runtime = offline_runtime(tmp_path, installed, default={
        'replies': [tool(), text({'findings': []})], 'error': overload,
        'error_unless_path_contains': '/_retries/'})
    cases = load_corpus(Path(__file__).resolve().parents[1] / 'evaluations/pilot')[:1]
    config = parse_config(b'models: {default: {provider: openai, model: offline-model, '
                          b'base_url: "https://offline.invalid/v1"}}\n'
                          b'budgets: {review_max_tokens: 500, per_persona_tokens: 100}\n')
    result = asyncio.run(run_experiment(cases, config, tmp_path / 'experiment',
                                       runtime=runtime, evidence_kind='offline',
                                       variant_names=('panel',), transient_retries=1))
    assert [r['run_id'] for r in result['runs']] == [
        f'{cases[0].id}/panel/{repeat}' for repeat in (1, 2, 3)]
    for run in result['runs']:
        failed, rerun = run['attempts']
        assert run['status'] == rerun['status'] == 'complete' and run['accounting_complete']
        assert failed['status'] == 'failed' and not failed['accounting_complete']
        assert [f['http_status'] for f in failed['transient_provider_failures']] == [500] * 4
        assert rerun['transient_provider_failures'] is None
        assert run['run_dir'] == rerun['run_dir'] != failed['run_dir']
        assert '/_retries/' in rerun['run_dir'] and rerun['run_dir'].endswith('attempt-0002')
        assert run['usage']['total'] > 0
        first = Path(failed['run_dir'])
        assert transient_provider_failures(first) == failed['transient_provider_failures']
        assert len(list(first.glob('sessions/*/attempt-0001/stdout.jsonl'))) == 4
        assert json.loads((first / 'evaluation.json').read_text())['status'] == 'failed'
        assert (Path(rerun['run_dir']) / 'verdict.json').is_file()


@pytest.mark.parametrize('message,classified', [
    ('litellm.InternalServerError: upstream connection lost', 'unavailable'),
    # The c1 abort (#86): the model finished its answer, then repeated one chunk.
    ('litellm.InternalServerError: The model is repeating the same chunk =  {HEAD} | .',
     'model_repetition'),
])
def test_actual_nare_mid_stream_5xx_is_classified_from_its_own_report(
    tmp_path, installed, capture, message, classified,
):
    from test_fanout import configure
    from test_nare_cli_integration import sse_choice

    from scrutare.engine.strategy import run_review
    from scrutare.evaluation.transient import (
        model_repetition_failures,
        transient_provider_failures,
    )
    config = configure(capture, personas=['senior-dev'], review=100, per=100)
    document = config.to_dict()
    document['models']['default'] = {'provider': 'openai', 'model': 'offline-model',
                                      'base_url': 'https://offline.invalid/v1'}
    config = parse_config(json.dumps(document))
    (capture / 'config.yaml').write_text(json.dumps(document))
    (capture / 'config.json').write_text(json.dumps(config.to_dict()))
    # LiteLLM's mid-stream error frame, after some streamed content.
    frames = [sse_choice({'content': '{"findings": []} | HEAD | HEAD'}),
              {'error': {'message': message, 'type': 'internal_server_error', 'param': None,
                         'code': '500'}}]
    runtime = offline_runtime(tmp_path, installed,
                              default={'http_sse': {'turns': [{'frames': frames}]}})
    spec_path = tmp_path / 'offline-spec.json'
    spec = json.loads(spec_path.read_text())
    spec['environment'] = {'OPENAI_API_KEY': 'offline-test-key'}
    spec_path.write_text(json.dumps(spec))
    result = asyncio.run(run_review(capture, config, runtime=runtime))
    outcome = result.initial.outcomes[0]
    assert (outcome.status, outcome.reason) == ('failed', 'provider')
    events = [json.loads(line) for line in
              (outcome.artifact_directory / 'stdout.jsonl').read_text().splitlines()]
    assert events[-1]['error'].startswith('RuntimeError: stream provider error: {')
    failure = [{'session_key': 'senior-dev/attempt-0001', 'http_status': 500}]
    if classified == 'unavailable':
        assert transient_provider_failures(capture) == failure
        assert model_repetition_failures(capture) == []
    else:
        assert transient_provider_failures(capture) is None
        assert model_repetition_failures(capture) == failure
