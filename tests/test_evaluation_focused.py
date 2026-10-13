"""Focused protocols compare identical evidence rails and keep fixed phase quotas."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_context import complete, declared
from test_evaluation_runner import config
from test_nare_cli_integration import installed as installed

from scrutare.config import ContextSettings
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.runner import comparison_configs, run_experiment


def test_focused_arms_compare_zero_one_three_rounds_at_fixed_total():
    variants = comparison_configs(config(), comparison_set='focused')
    assert list(variants) == ['aggregate', 'assessed', 'focused1', 'focused3']
    assert all(v.context.enabled and v.inspection.procedures == 'v1'
        and v.findings.evidence == 'v2' and not v.routing.enabled and not v.analysis.enabled
        and v.budgets.review_max_tokens == 400 and v.models == config().models
        for v in variants.values())
    assert variants['aggregate'].strategy == 'panel'
    assert not variants['aggregate'].findings.assessment.enabled
    assert variants['assessed'].strategy == 'panel'
    assert variants['assessed'].findings.assessment.enabled
    assert variants['focused1'].strategy == variants['focused3'].strategy == 'debate'
    assert variants['focused1'].findings.discussion.enabled
    assert variants['focused3'].findings.discussion.enabled
    assert variants['focused1'].rounds.max == 1 and variants['focused3'].rounds.max == 3
    assert all(v.findings.assessment.tokens == 80 for name, v in variants.items()
               if name != 'aggregate')


def test_focused_snapshot_matches_actual_reserved_participants_before_await(tmp_path, monkeypatch):
    cases = declared(tmp_path / 'corpus')
    async def inspect(runtime):
        snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
        assert len(snapshot['expected_runs']) == 12
        for name, cell in snapshot['case_variants']['a'].items():
            assert not any(n.startswith('debate-chair') for n in cell['allocations'])
            assert sum(cell['allocations'].values()) == 400
            if name != 'aggregate':
                assert cell['allocations'] == {n: 80 for n in (
                    'senior-dev', 'junior-dev', 'security', 'devops', 'evidence-assessor')}
            if name.startswith('focused'):
                assert cell['phase_budget_policy'] == 'reuse_discovery_and_assessor_grants'
                assert cell['protocol_sources']['discussion_inputs_sha256']
                assert cell['protocol_sources']['assessment_inputs_sha256']
        return SimpleNamespace(version='2026.10.4', contract=1)
    async def execute(directory, conf, *, runtime):
        if conf.findings.discussion.enabled:
            (directory / 'debate.json').write_text(json.dumps({
                'schema_version': 2, 'kind': 'focused_evidence_discussion',
                'status': 'complete', 'reason': 'undisputed', 'bypassed_candidate_ids': [],
                'rounds': [], 'final_assessments': []}))
        return complete()
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime', inspect)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    result = asyncio.run(run_experiment(cases, replace(config(), context=ContextSettings(
        related_paths=('caller.py',))), tmp_path / 'out', runtime=NareRuntime(Path('/unused')),
        evidence_kind='offline', comparison_set='focused'))
    assert all(r['status'] == 'complete' for r in result['runs'])
    for row in result['runs']:
        if row['variant'].startswith('focused'):
            assert row['discussion_trace']['availability'] == 'recorded'
            assert row['discussion_trace']['rounds'] == []
        else:
            assert 'discussion_trace' not in row


@pytest.mark.parametrize('engine_status', ['complete', 'failed'])
def test_missing_focused_trace_invalidates_successful_cell_without_erasing_failure(
        tmp_path, monkeypatch, engine_status):
    from test_evaluation_context import patch_engine

    cases = declared(tmp_path / 'corpus')
    async def execute(directory, conf, *, runtime):
        result = complete()
        result.status = engine_status
        return result
    patch_engine(monkeypatch, execute)
    result = asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline', comparison_set='focused'))
    focused = [r for r in result['runs'] if r['variant'].startswith('focused')]
    expected = 'invalid' if engine_status == 'complete' else 'failed'
    assert len(focused) == 6 and all(r['status'] == expected for r in focused)
    assert all(r['discussion_trace']['rounds'] is None for r in focused)
    if engine_status == 'complete':
        assert all(r['error'] == 'UnavailableFocusedTrace' for r in focused)
    else:
        assert all(r['error'] is None for r in focused)


def test_installed_focused_matrix_records_real_rounds_on_same_ledger(
        tmp_path, installed, monkeypatch):
    import hashlib

    from test_nare_cli_integration import offline_runtime, text, tool

    cases = declared(tmp_path / 'corpus')
    source = tmp_path / 'corpus/sources/a/head/app.py'
    finding = {'file': 'app.py', 'line': 1, 'side': 'RIGHT', 'category': 'correctness',
        'problem': 'Fixture claim', 'reason': 'Fixture assertion; no human quality judgment.',
        'evidence': {'version': 2, 'trigger': 'Fixture input', 'preconditions': [],
            'expected': 'Fixture expected', 'observed': 'Fixture observed',
            'impact': 'Fixture impact',
            'citations': [{'side': 'head', 'revision': 'a'*40, 'path': 'app.py',
                          'start_line': 1, 'end_line': 1,
                          'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}}
    runtime = offline_runtime(tmp_path, installed, default={
        'replies': [tool(), text({'findings': [finding]})]})
    path = tmp_path / 'offline-spec.json'
    spec = json.loads(path.read_text())
    spec['scenarios'] = [
        {'purpose': 'review', 'attempt': f'attempt-{number:04d}',
         'prompt_prefix': 'Independently assess',
         'scenario': {'replies': [{'assess_candidates': 'unresolved'}]}}
        for number in range(1, 5)] + [
        {'purpose': 'review', 'attempt': f'attempt-{number:04d}',
         'prompt_prefix': 'Focused evidence discussion',
         'scenario': {'replies': [{'discuss_candidates': 'retain'}]}}
        for number in range(3, 6)]
    path.write_text(json.dumps(spec))
    binary = tmp_path / 'bin'
    binary.mkdir()
    gh = binary / 'gh'
    gh.write_text('#!/bin/sh\nprintf forbidden > "$GH_FORBIDDEN"\nexit 99\n')
    gh.chmod(0o700)
    monkeypatch.setenv('PATH', f'{binary}:/usr/bin:/bin')
    monkeypatch.setenv('GH_FORBIDDEN', str(tmp_path / 'github-called'))
    result = asyncio.run(run_experiment(cases, config(), tmp_path / 'out', runtime=runtime,
                                       evidence_kind='offline', comparison_set='focused'))
    assert len(result['runs']) == 12
    assert all(r['status'] not in ('failed', 'invalid') and r['accounting_complete']
               for r in result['runs'])
    for row in result['runs']:
        if row['variant'].startswith('focused'):
            expected = 1 if row['variant'] == 'focused1' else 3
            trace = row['discussion_trace']
            assert trace['availability'] == 'recorded'
            assert trace['status'] == 'partial' and trace['reason'] == 'deadlock'
            assert len(trace['rounds']) == expected
            assert all(r['tokens'] > 0 and r['latency_seconds'] > 0 for r in trace['rounds'])
            assert all(r['new_citations'] == [] for r in trace['rounds'])
            assert all(r['proposals'] for r in trace['rounds'])
            assert all(r['status'] == 'unresolved' for r in trace['final_assessments'])
            directory = Path(row['run_dir'])
            saved = json.loads((directory / 'debate.json').read_text())
            assert saved['ledger']['allocations'] == {n: 80 for n in (
                'senior-dev', 'junior-dev', 'security', 'devops', 'evidence-assessor')}
            assert row['usage']['total'] == saved['ledger']['usage']['total']
    assert not (tmp_path / 'github-called').exists()
    observations = list((tmp_path / 'out').rglob('offline-observations.json'))
    assert observations
    assert all(json.loads(p.read_bytes())['guard_violations'] == [] for p in observations)
    assert all(json.loads(p.read_bytes())['credential_names'] == [] for p in observations)
