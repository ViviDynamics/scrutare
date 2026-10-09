"""Evaluation through separately installed nare with deterministic provider substitution."""
import asyncio
import json
from pathlib import Path

from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import offline_runtime, text, tool

from scrutare.config import parse_config
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.runner import run_experiment


def test_actual_nare_evaluation_never_posts_and_all_runs_account(tmp_path, installed, monkeypatch):
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
                                       runtime=runtime, evidence_kind='offline'))
    assert len(result['runs']) == 9
    assert all(r['status'] == 'complete' and r['accounting_complete'] for r in result['runs'])
    assert all(r['usage']['total'] > 0 for r in result['runs'])
    assert not (tmp_path / 'github-called').exists()
    for run in result['runs']:
        directory = Path(run['run_dir'])
        assert not (directory / 'posting.json').exists()
        for path in directory.glob('sessions/*/attempt-*/session.json'):
            session = json.loads(path.read_text())
            assert session['policy']['root'] == str(directory / 'review-inputs')
            assert session['policy']['tools'] == ['read']
