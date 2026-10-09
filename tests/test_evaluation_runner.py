import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrutare.config import parse_config
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.runner import comparison_configs, run_experiment


def config():
    return parse_config(b'models: {default: {provider: openai, model: frozen-v1}}\n'
                        b'budgets: {review_max_tokens: 400, per_persona_tokens: 100}\n')


def corpus(tmp_path):
    capture = tmp_path / 'captures/a'
    capture.mkdir(parents=True)
    diff = 'diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x=1\n+x=2\n'
    (capture / 'diff.patch').write_text(diff)
    (capture / 'files.json').write_text('[{"filename":"app.py","status":"modified"}]')
    (capture / 'metadata.json').write_text(json.dumps({
        'schema_version': 1, 'status': 'ingested', 'repository': 'pilot/local',
        'pr_number': 1, 'head_sha': 'a'*40, 'pull_request': {
            'number': 1, 'changed_files': 1, 'state': 'open', 'merged': False,
            'head': {'sha': 'a'*40}, 'base': {'sha': 'b'*40, 'ref': 'main',
                                          'repo': {'full_name': 'pilot/local'}}}}))
    labels = tmp_path / 'labels/a.json'
    labels.parent.mkdir()
    labels.write_text('{"case_id":"a","defects":[],"counterexamples":[]}')
    (tmp_path / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'cases': [{
        'id': 'a', 'split': 'development', 'domain': 'correctness',
        'capture': 'captures/a', 'labels': 'labels/a.json',
        'provenance': {'license': 'Elastic-2.0', 'origin': 'original'}}]}))
    return load_corpus(tmp_path)


def test_comparison_preserves_models_and_total_ceiling():
    variants = comparison_configs(config())
    assert set(variants) == {'senior', 'panel', 'debate'}
    assert all(c.budgets.review_max_tokens == 400 for c in variants.values())
    assert variants['senior'].personas == ('senior-dev',)
    assert variants['senior'].budgets.per_persona_tokens == 400
    assert variants['debate'].strategy == 'debate'
    assert all(c.models == config().models for c in variants.values())


def test_local_runner_three_repeats_never_calls_github_and_retains_failure(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    async def execute(run_dir, conf, *, runtime):
        calls.append((run_dir, conf))
        assert {p.name for p in (run_dir / 'review-inputs').iterdir()} == {
            'diff.patch', 'files.json', 'context.json'}
        assert not any(p.name == 'labels' for p in run_dir.rglob('*'))
        return SimpleNamespace(status='complete', accounting_complete=True,
            usage=TokenUsage(input=5), verdict=SimpleNamespace(findings=()))
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
                        lambda runtime: asyncio.sleep(
                            0, result=SimpleNamespace(version='2026.10.4', contract=1)))
    result = asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
                        runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    assert len(calls) == len(result['runs']) == 9
    assert result['evidence_kind'] == 'offline'
    assert all(r['usage']['total'] == 5 for r in result['runs'])
    assert (tmp_path / 'out/experiment.json').exists()
    assert all(not (directory / 'posting.json').exists() for directory, _ in calls)
    with pytest.raises(FileExistsError):
        asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
                     runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))


def test_loader_rejects_label_inside_capture_and_traversal(tmp_path):
    corpus(tmp_path)
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    manifest['cases'][0]['labels'] = 'captures/a/labels.json'
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_corpus(tmp_path)


def test_cli_help_is_available_without_provider_access():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'scrutare.evaluation', '--help'],
                            capture_output=True)
    assert result.returncode == 0
    assert b'adjudicate' in result.stdout and b'run' in result.stdout and b'score' in result.stdout


def test_concurrency_is_bounded_and_incremental_snapshot_survives(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    active = 0
    maximum = 0
    async def execute(run_dir, conf, *, runtime):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(status='complete', accounting_complete=True,
            usage=TokenUsage(input=1), verdict=SimpleNamespace(findings=()))
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='2026.10.4', contract=1)))
    result = asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline', concurrency=3))
    assert maximum == 3
    assert result['status'] == 'complete'
    assert [r['run_id'] for r in result['runs']] == [
        f'a/{variant}/{repeat}' for variant in ('senior', 'panel', 'debate')
        for repeat in range(1, 4)]


def test_interruption_keeps_incomplete_schedule_and_accounting_failure(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    async def execute(run_dir, conf, *, runtime):
        await asyncio.sleep(10)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='2026.10.4', contract=1)))
    async def job():
        task = asyncio.create_task(run_experiment(cases, config(), tmp_path / 'out',
             runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(job())
    saved = json.loads((tmp_path / 'out/experiment.json').read_text())
    assert saved['status'] == 'incomplete'
    assert len(saved['runs']) == 9
    assert saved['runs'][0]['status'] == 'failed'
    assert saved['runs'][0]['accounting_complete'] is False


@pytest.mark.parametrize('aggregate', [False, True])
def test_cancel_recovers_session_usage_without_double_counting(tmp_path, monkeypatch, aggregate):
    cases = corpus(tmp_path / 'corpus')
    async def execute(run_dir, conf, *, runtime):
        attempt = run_dir / 'sessions/senior-dev/attempt-0001'
        attempt.mkdir(parents=True)
        (attempt / 'result.json').write_text(json.dumps({
            'session_key': 'senior-dev/attempt-0001',
            'usage': {'input': 80, 'output': 20, 'cache_read': 3, 'cache_write': 2, 'total': 105}}))
        if aggregate:
            (run_dir / 'panel.json').write_text(json.dumps({'ledger': {'usage': {
                'input': 80, 'output': 20, 'cache_read': 3, 'cache_write': 2, 'total': 105}}}))
        raise asyncio.CancelledError
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='2026.10.4', contract=1)))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
                     runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    saved = json.loads((tmp_path / 'out/experiment.json').read_text())
    assert saved['runs'][0]['usage']['total'] == 105
    assert saved['runs'][0]['accounting_complete'] is False
    assert saved['runs'][0]['status'] == 'failed'


def test_queued_cases_use_same_frozen_bytes_as_snapshot(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    original = (cases[0].capture / 'diff.patch').read_bytes()
    seen = []
    async def execute(run_dir, conf, *, runtime):
        seen.append((run_dir / 'diff.patch').read_bytes())
        (cases[0].capture / 'diff.patch').write_bytes(original.replace(b'+x=2', b'+x=3'))
        return SimpleNamespace(status='complete', accounting_complete=True,
            usage=TokenUsage(input=1), verdict=SimpleNamespace(findings=()))
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='2026.10.4', contract=1)))
    asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
                runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    assert seen == [original] * 9
    import hashlib
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    expected_hash = hashlib.sha256(original).hexdigest()
    assert snapshot['cases'][0]['capture_sha256']['diff.patch'] == expected_hash


def test_procedure_comparison_has_equal_total_and_three_explicit_rosters():
    variants = comparison_configs(config(), comparison_set='procedures')
    assert tuple(variants) == ('current4', 'revised4', 'revised5')
    assert [v.inspection.procedures for v in variants.values()] == ['baseline', 'v1', 'v1']
    assert [len(v.personas) for v in variants.values()] == [4, 4, 5]
    assert variants['revised5'].personas[-1] == 'testing-verification'
    assert [v.budgets.per_persona_tokens for v in variants.values()] == [100,100,80]
    assert all(v.budgets.review_max_tokens == 400 and v.strategy == 'panel'
               and v.models == config().models for v in variants.values())
    with pytest.raises(ValueError, match='comparison'):
        comparison_configs(config(), comparison_set='invented')


def test_procedure_job_freezes_profiles_before_first_await(tmp_path, monkeypatch):
    from hashlib import sha256

    from scrutare.personas import load_persona
    cases = corpus(tmp_path / 'corpus')
    seen = []
    async def inspect(runtime):
        snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
        assert len(snapshot['expected_runs']) == 9
        for name, profile in [('current4','baseline'),('revised4','v1'),('revised5','v1')]:
            saved = snapshot['variant_personas'][name]
            expected = load_persona('senior-dev', procedures=profile).system_prompt
            assert saved['senior-dev']['system_prompt'] == expected
            assert saved['senior-dev']['system_prompt_sha256'] == (
                sha256(expected.encode()).hexdigest())
        assert 'testing-verification' in snapshot['variant_personas']['revised5']
        return SimpleNamespace(version='2026.10.4', contract=1)
    async def execute(run_dir, conf, *, runtime):
        seen.append(conf.inspection.procedures)
        return SimpleNamespace(status='complete',accounting_complete=True,
            usage=TokenUsage(input=1),verdict=SimpleNamespace(findings=()))
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',inspect)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review',execute)
    result = asyncio.run(run_experiment(cases,config(),tmp_path/'out',
        runtime=NareRuntime(Path('/unused')),evidence_kind='offline',comparison_set='procedures'))
    assert seen == ['baseline']*3 + ['v1']*6
    assert [r['run_id'] for r in result['runs']] == [
        f'a/{variant}/{repeat}' for variant in ('current4','revised4','revised5')
        for repeat in [1,2,3]]
