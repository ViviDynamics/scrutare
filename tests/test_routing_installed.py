"""Actual nare keeps routed procedures in one guarded immutable evidence root."""
import asyncio
import json
from pathlib import Path

import pytest
from test_evaluation_context import declared
from test_nare_cli_integration import installed as installed
from test_nare_cli_integration import offline_runtime, text, tool

from scrutare.config import parse_config
from scrutare.engine.repository_context import _artifact, capture_repository_context
from scrutare.engine.repository_snapshot import RepositorySnapshot
from scrutare.engine.session_models import NareRuntime
from scrutare.engine.strategy import run_review
from scrutare.evaluation.corpus import source_contents
from scrutare.replay import replay_run


@pytest.mark.parametrize('strategy', ['panel', 'debate', 'iterative'])
def test_installed_routing_preserves_context_and_all_source_observations(
        tmp_path, installed, strategy):
    cases = declared(tmp_path / 'corpus')
    finding = {'file': 'app.py', 'line': 1, 'side': 'RIGHT', 'category': 'correctness',
               'problem': 'Concrete problem', 'reason': 'Concrete consequence'}
    runtime: NareRuntime = offline_runtime(tmp_path, installed, default={'replies': [
        tool(args={'path': 'routing-focus.json'}),
        tool(args={'path': _artifact('head', 'caller.py')}, call_id='caller'),
        text({'findings': [finding]})]})
    path = tmp_path / 'offline-spec.json'
    spec = json.loads(path.read_text())
    spec['scenarios'] = [{'purpose': 'review', 'attempt': 'attempt-0001',
        'prompt_prefix': 'Arbitrate',
        'scenario': {'replies': [text({'findings': [finding], 'converged': True})]}}]
    path.write_text(json.dumps(spec))
    conf = parse_config(json.dumps({'strategy': strategy, 'personas': ['junior-dev'],
        'models': {'default': {'provider': 'openai', 'model': 'offline-model',
                              'base_url': 'https://offline.invalid/v1'}},
        'budgets': {'per_persona_tokens': 400, 'review_max_tokens': 1200},
        'context': {'enabled': True, 'related_paths': ['caller.py']},
        'routing': {'enabled': True, 'mode': 'manual', 'force': ['performance-concurrency'],
                    'groups': [{'name': 'caller-contract', 'implementation': ['app.py'],
                                'callers': ['caller.py']}]}}))
    run = tmp_path / 'outside-checkout'
    run.mkdir()
    for name in ('diff.patch', 'files.json', 'metadata.json'):
        (run / name).write_bytes((cases[0].capture / name).read_bytes())
    metadata = json.loads((run / 'metadata.json').read_bytes())
    metadata['pull_request']['head']['repo'] = {'full_name': 'pilot/fork'}
    (run / 'metadata.json').write_text(json.dumps(metadata))
    for name in ('comments.json', 'reviews.json', 'review_comments.json'):
        (run / name).write_text('[]')
    for name in ('config.json', 'config.yaml'):
        (run / name).write_text(json.dumps(conf.to_dict()))
    snapshot = RepositorySnapshot({(s.repository, s.revision): source_contents(s.directory)
                                   for s in cases[0].context_sources})
    capture_repository_context(snapshot, run, conf, source=snapshot.provenance())
    result = asyncio.run(run_review(run, conf, runtime=runtime))
    assert result.status == 'complete' and result.accounting_complete
    assert result.verdict is not None
    assert set(p for group in result.verdict.findings for p in group.personas) == {
        'junior-dev', 'senior-dev', 'security', 'performance-concurrency'}
    routed = list(run.rglob('routing.json'))
    assert routed
    for path in routed:
        record = json.loads(path.read_bytes())
        assert record['allocated_total'] <= record['review_ceiling'] == 1200
        assert 'routing-focus.json' in record['inputs_sha256']
        assert 'caller.py' in record['global_supporting_paths']
    observations = list(run.rglob('offline-observations.json'))
    assert observations
    assert any('caller_original()' in p.read_text() for p in observations)
    assert all(json.loads(p.read_bytes())['guard_violations'] == [] for p in observations)
    assert all(json.loads(p.read_bytes())['credential_names'] == [] for p in observations)
    for path in run.rglob('session.json'):
        session = json.loads(path.read_bytes())
        assert session['policy']['tools'] == ['read']
        assert Path(session['policy']['root']).name == 'review-inputs'
    assert not (run / 'posting.json').exists()
    assert replay_run(run).saved_identical
