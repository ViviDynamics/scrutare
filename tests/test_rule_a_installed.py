"""Rule A exercised through the installed nare 2026.10.32 console, offline."""
import asyncio
import json
from pathlib import Path

import pytest
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import offline_runtime, text, tool

from scrutare.config import parse_config
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.runner import run_experiment
from scrutare.replay import replay_run


@pytest.mark.parametrize('recover', [True, False])
def test_installed_rule_a_success_failure_and_costs(tmp_path, installed, recover):
    runtime = offline_runtime(tmp_path, installed, default={
        'replies': [tool(), text({'findings': []})],
        'error': 'upstream disconnected', 'error_after_turns': 1,
        'error_unless_path_contains': '/_retries/' if recover else 'never-match'})
    cases = tuple(c for c in load_corpus(
        Path(__file__).resolve().parents[1] / 'evaluations/pilot')
        if not json.loads(c.labels.read_text())['defects'])[:1]
    config = parse_config(b'models: {default: {provider: openai, model: offline-model, '
                          b'base_url: "https://offline.invalid/v1"}}\n'
                          b'budgets: {review_max_tokens: 500, per_persona_tokens: 500}\n')
    result = asyncio.run(run_experiment(cases, config, tmp_path / 'experiment',
        runtime=runtime, evidence_kind='offline', variant_names=('senior',),
        qualification_rule_a=True))
    for row in result['runs']:
        assert len(row['attempts']) == 2
        assert row['attempts'][0]['usage']['total'] > 0
        assert row['cumulative_usage']['total'] == sum(a['usage']['total'] for a in row['attempts'])
        assert row['selected_attempt'] == (2 if recover else None)
        assert row['status'] == ('complete' if recover else 'failed')
        if recover:
            assert replay_run(Path(row['run_dir'])).exit_code == 0
        for attempt in row['attempts']:
            root = Path(attempt['run_dir'])
            assert (root / 'artifacts.json').is_file()
            assert not (root / 'posting.json').exists()


def test_installed_completed_empty_defect_evidence_never_retries(tmp_path, installed):
    runtime = offline_runtime(tmp_path, installed, default={'replies': [text({'findings': []})]})
    cases = load_corpus(Path(__file__).resolve().parents[1] / 'evaluations/pilot')[1:2]
    assert json.loads(cases[0].labels.read_text())['defects']
    config = parse_config(b'models: {default: {provider: openai, model: offline-model, '
                          b'base_url: "https://offline.invalid/v1"}}\n')
    result = asyncio.run(run_experiment(cases, config, tmp_path / 'experiment',
        runtime=runtime, evidence_kind='offline', variant_names=('senior',),
        qualification_rule_a=True))
    assert all(len(r['attempts']) == 1 and r['error'] == 'EmptyEvidence'
               and r['status'] == 'invalid' for r in result['runs'])


@pytest.mark.parametrize('recover', [True, False])
@pytest.mark.parametrize('strategy', ['panel', 'iterative'])
def test_installed_whole_review_retry_before_posting(tmp_path, installed, recover, strategy):
    from test_post_review import REF, SHA, CaptureClient, FakePoster

    from scrutare.engine.review import ReviewRunError, review_pr
    capped = text({'findings': []})
    capped['stop_reason'] = 'max_tokens'
    runtime = offline_runtime(tmp_path, installed, default={
        'replies': [capped], 'alternate_replies_path_contains': '-retry/' if recover else 'never',
        'alternate_replies': [tool(), text({'findings': []})]})
    raw = (b'models: {default: {provider: openai, model: offline-model, '
           b'base_url: "https://offline.invalid/v1", max_output_tokens: 20}}\n'
           b'personas: [security]\n') + f'strategy: {strategy}\n'.encode()
    poster = FakePoster()
    def invoke():
        return asyncio.run(review_pr(REF, parse_config(raw), config_bytes=raw,
            runs_root=tmp_path / 'runs', runtime=runtime, capture_client=CaptureClient(),
            review_client=poster, whole_review_retry=True, expected_head=SHA))
    if recover:
        result = invoke()
        assert result.recovery['selected_attempt'] == 2
        assert all(a['usage']['total'] > 0 for a in result.recovery['attempts'])
        assert replay_run(result.run_dir).exit_code == 0
        assert len(poster.creates) == 1
        if strategy == 'iterative':
            inputs = [p.read_bytes() for p in (tmp_path / 'runs').glob(
                'run-*/review-history-input.json')]
            assert len(inputs) == 2 and inputs[0] == inputs[1]
    else:
        with pytest.raises(ReviewRunError, match='no answer'):
            invoke()
        assert not poster.creates
    assert len(list((tmp_path / 'runs').glob('run-*/artifacts.json'))) == 2
