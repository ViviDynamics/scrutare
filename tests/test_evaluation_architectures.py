"""Paired component comparisons freeze real production inputs and participants."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_context import complete, declared
from test_evaluation_runner import config
from test_nare_cli_integration import installed as installed

from scrutare.config import AnalysisSettings, ContextSettings, FindingSettings, RoutingSettings
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.runner import comparison_configs, run_experiment


def test_architecture_arms_isolate_components_with_one_total_ceiling():
    variants = comparison_configs(config(), comparison_set='architectures')
    assert list(variants) == ['baseline', 'context', 'procedures', 'evidence',
                             'assessment', 'tools', 'routing']
    assert all(v.strategy == 'panel' and v.budgets.review_max_tokens == 400
               and v.models == config().models for v in variants.values())
    assert not variants['baseline'].context.enabled
    assert variants['context'].context.enabled
    assert variants['context'].inspection.procedures == 'baseline'
    assert variants['procedures'].inspection.procedures == 'v1'
    assert variants['procedures'].findings.evidence == 'legacy'
    assert variants['evidence'].findings.evidence == 'v2'
    assert variants['assessment'].findings.assessment.enabled
    assert variants['assessment'].findings.assessment.tokens == 80
    assert variants['tools'].analysis.enabled and variants['tools'].findings.assessment.enabled
    assert variants['tools'].findings == variants['assessment'].findings
    assert variants['routing'].routing.enabled
    assert not variants['routing'].analysis.enabled
    assert not variants['routing'].findings.assessment.enabled


def test_architecture_comparison_clears_inherited_optional_features():
    conf = replace(config(), context=ContextSettings(enabled=True),
                   findings=FindingSettings('v2'), analysis=AnalysisSettings(True),
                   routing=RoutingSettings(enabled=True))
    variants = comparison_configs(conf, comparison_set='architectures')
    assert not variants['baseline'].analysis.enabled and not variants['baseline'].routing.enabled
    assert variants['baseline'].findings.evidence == 'legacy'
    assert not variants['evidence'].analysis.enabled and not variants['evidence'].routing.enabled


def test_architecture_snapshot_binds_effective_roster_and_inputs_before_runtime(
        tmp_path, monkeypatch):
    cases = declared(tmp_path / 'corpus')
    conf = replace(config(), context=ContextSettings(related_paths=('caller.py',)),
        routing=RoutingSettings(force=('performance-concurrency',)))
    async def inspect(runtime):
        snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
        cells = snapshot['case_variants']['a']
        assert len(snapshot['expected_runs']) == 21
        routed = cells['routing']
        assert len(routed['personas']) == 5
        assert routed['effective_personas'] == ['senior-dev', 'junior-dev', 'security',
                                               'devops', 'performance-concurrency']
        assert routed['allocations']['performance-concurrency'] == 80
        assert sum(routed['allocations'].values()) == 400
        assert routed['personas']['performance-concurrency']['version'] == 'routing-v1'
        assert cells['assessment']['allocations']['evidence-assessor'] == 80
        assert sum(cells['assessment']['allocations'].values()) == 400
        assessor = cells['assessment']['phase_personas']['evidence-assessor']
        assert assessor['system_prompt_sha256']
        assert assessor['origin'] == 'engine' and assessor['version'] == 'assessment-v1'
        assert 'repository-context.json' not in cells['baseline']['inputs_sha256']
        assert 'repository-context.json' in cells['context']['inputs_sha256']
        assert cells['tools']['analysis_status'] == 'unavailable'
        (tmp_path / 'corpus/sources/a/head/caller.py').write_text('mutated()\n')
        return SimpleNamespace(version='2026.10.4', contract=1)
    async def execute(directory, conf, *, runtime):
        if conf.context.enabled:
            assert 'mutated()' not in ''.join(p.read_text() for p in
                (directory / 'review-inputs').iterdir() if p.is_file())
        return complete()
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime', inspect)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    result = asyncio.run(run_experiment(cases, conf, tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='architectures'))
    assert len(result['runs']) == 21 and all(r['status'] == 'complete' for r in result['runs'])


def test_real_ruff_capture_parses_sources_without_executing_code_or_config(tmp_path):
    import hashlib
    import sys

    from scrutare.evaluation.tool_capture import capture_development_ruff

    root = tmp_path / 'corpus'
    cases = declared(root)
    head = root / 'sources/a/head'
    (head / 'app.py').write_text('value = undefined_name\n')
    (head / 'ruff.toml').write_text('invalid config must not be loaded!')
    (head / 'side_effect.py').write_text(
        f"open({str(tmp_path / 'executed')!r}, 'w').write('executed')\n")
    binary = Path(sys.executable).parent / 'ruff'
    index = capture_development_ruff(root, tmp_path / 'tools', executable=binary)
    entry = index['cases']['a']
    assert entry['status'] == 'complete' and entry['exit_code'] == 1
    assert entry['inspected_paths'] == ['app.py', 'caller.py', 'side_effect.py']
    saved = (tmp_path / 'tools' / entry['capture']).read_bytes()
    assert hashlib.sha256(saved).hexdigest() == entry['capture_sha256']
    data = json.loads(saved)
    assert data['revision'] == cases[0].context_sources[1].revision
    assert data['tool'] == {'name': 'ruff', 'version': '0.16.10'}
    assert any(row['code'] == 'F821' and row['message'] == 'Undefined name `undefined_name`'
               for row in data['diagnostics'])
    assert entry['source_sha256']['app.py'] == hashlib.sha256(
        (head / 'app.py').read_bytes()).hexdigest()
    assert not (tmp_path / 'executed').exists()


def test_tool_capture_refuses_external_provenance_before_spawning(tmp_path, monkeypatch):
    from scrutare.evaluation.tool_capture import capture_development_ruff

    root = tmp_path / 'corpus'
    declared(root)
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['cases'][0]['provenance']['origin'] = 'external-pull-request'
    path.write_text(json.dumps(manifest))
    spawned = []
    monkeypatch.setattr('scrutare.evaluation.tool_capture.subprocess.run',
                        lambda *a, **k: spawned.append(a))
    import pytest
    with pytest.raises(ValueError, match='original'):
        capture_development_ruff(root, tmp_path / 'tools', executable=Path('/unused'))
    assert not spawned and not (tmp_path / 'tools').exists()


def test_tool_capture_rejects_linked_source_root(tmp_path):
    import pytest

    from scrutare.evaluation.tool_capture import capture_development_ruff

    declared(tmp_path / 'real')
    (tmp_path / 'linked').symlink_to(tmp_path / 'real', target_is_directory=True)
    with pytest.raises(ValueError, match='unlinked'):
        capture_development_ruff(tmp_path / 'linked', tmp_path / 'tools',
                                 executable=Path('/unused'))


@pytest.mark.parametrize('excluded_rename', [False, True])
def test_installed_nare_architecture_arms_share_captured_tools_and_accounting(
        tmp_path, installed, monkeypatch, excluded_rename):
    import sys

    from test_nare_cli_integration import offline_runtime, text, tool

    from scrutare.evaluation.corpus import load_corpus
    from scrutare.evaluation.tool_capture import capture_development_ruff

    root = tmp_path / 'corpus'
    cases = declared(root)
    if excluded_rename:
        files = cases[0].capture / 'files.json'
        files.write_text(json.dumps([{'filename': 'async_queue.py',
            'previous_filename': 'app.py', 'status': 'renamed'}]))
        patch = cases[0].capture / 'diff.patch'
        patch.write_text(patch.read_text().replace('b/app.py', 'b/async_queue.py'))
        head = root / 'sources/a/head'
        (head / 'app.py').rename(head / 'async_queue.py')
    capture_development_ruff(root, root / 'tools',
                             executable=Path(sys.executable).parent / 'ruff')
    path = root / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['cases'][0]['analysis_capture'] = 'tools/a.json'
    path.write_text(json.dumps(manifest))
    cases = load_corpus(root)
    runtime = offline_runtime(tmp_path, installed, default={
        'replies': [tool(), text({'findings': []})]})
    binary = tmp_path / 'bin'
    binary.mkdir()
    gh = binary / 'gh'
    gh.write_text('#!/bin/sh\nprintf forbidden > "$GH_FORBIDDEN"\nexit 99\n')
    gh.chmod(0o700)
    monkeypatch.setenv('PATH', f'{binary}:/usr/bin:/bin')
    monkeypatch.setenv('GH_FORBIDDEN', str(tmp_path / 'github-called'))
    conf = replace(config(), context=ContextSettings(related_paths=('caller.py',)),
                   routing=RoutingSettings(force=() if excluded_rename else
                                           ('performance-concurrency',)))
    if excluded_rename:
        from scrutare.config import PathSettings
        conf = replace(conf, github=replace(conf.github,
                       paths=PathSettings(exclude=('async_queue.py',))))
    result = asyncio.run(run_experiment(cases, conf, tmp_path / 'out',
        runtime=runtime, evidence_kind='offline', comparison_set='architectures'))
    assert len(result['runs']) == 21
    assert all(r['status'] == 'complete' and r['accounting_complete'] for r in result['runs'])
    assert all(r['usage']['total'] > 0 for r in result['runs'])
    assert not (tmp_path / 'github-called').exists()
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    for run in result['runs']:
        directory = Path(run['run_dir'])
        cell = snapshot['case_variants']['a'][run['variant']]
        if run['variant'] == 'tools':
            analysis = json.loads((directory / 'review-inputs/static-analysis.json').read_text())
            assert analysis['status'] == 'complete'
            assert any(d['rule'] == 'F821' for d in analysis['diagnostics'])
            assert cell['analysis_status'] == 'complete'
        if run['variant'] == 'routing':
            routing = json.loads((directory / 'routing.json').read_text())
            assert routing['allocations'] == cell['allocations']
            assert routing['effective_personas'] == cell['effective_personas']
            if excluded_rename:
                assert cell['effective_personas'] == ['senior-dev', 'junior-dev',
                                                      'security', 'devops']
                assert set(cell['allocations'].values()) == {100}
        for saved in directory.glob('sessions/*/attempt-*/session.json'):
            session = json.loads(saved.read_text())
            assert session['policy']['root'] == str(directory / 'review-inputs')
            assert session['policy']['tools'] == ['read']
    observations = list((tmp_path / 'out').rglob('offline-observations.json'))
    assert observations
    assert all(json.loads(p.read_bytes())['guard_violations'] == [] for p in observations)
    assert all(json.loads(p.read_bytes())['credential_names'] == [] for p in observations)


def test_legacy_baseline_snapshot_retains_four_original_persona_prompts(tmp_path, monkeypatch):
    from test_evaluation_context import patch_engine
    from test_evaluation_runner import corpus

    cases = corpus(tmp_path / 'corpus')
    async def execute(directory, conf, *, runtime):
        return complete()
    patch_engine(monkeypatch, execute)
    asyncio.run(run_experiment(cases, config(), tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    assert list(snapshot['personas']) == ['devops', 'junior-dev', 'security', 'senior-dev']


def test_snapshot_routing_uses_effective_selection_for_excluded_rename(tmp_path, monkeypatch):
    from scrutare.config import PathSettings
    from scrutare.engine.routing import plan_routing

    cases = declared(tmp_path / 'corpus')
    files = cases[0].capture / 'files.json'
    files.write_text(json.dumps([{'filename': 'async_queue.py', 'previous_filename': 'app.py',
                                 'status': 'renamed'}]))
    patch = cases[0].capture / 'diff.patch'
    patch.write_text(patch.read_text().replace('b/app.py', 'b/async_queue.py'))
    head = tmp_path / 'corpus/sources/a/head'
    (head / 'app.py').rename(head / 'async_queue.py')
    conf = config()
    conf = replace(conf, context=ContextSettings(related_paths=('caller.py',)),
                   github=replace(conf.github, paths=PathSettings(exclude=('async_queue.py',))))
    async def inspect(runtime):
        snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
        cell = snapshot['case_variants']['a']['routing']
        assert cell['effective_personas'] == ['senior-dev', 'junior-dev', 'security', 'devops']
        assert cell['allocations'] == {'senior-dev': 100, 'junior-dev': 100,
                                      'security': 100, 'devops': 100}
        assert cell['routing']['selected_paths'] == []
        return SimpleNamespace(version='2026.10.4', contract=1)
    async def execute(directory, cfg, *, runtime):
        if cfg.routing.enabled:
            root = directory / 'review-inputs'
            focus = json.loads((root / 'routing-focus.json').read_text())
            plan = plan_routing(json.loads((root / 'files.json').read_text()),
                                tuple(focus['global_supporting_paths']), cfg)
            snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
            cell = snapshot['case_variants']['a']['routing']
            assert cell['effective_personas'] == [p.name for p in plan.personas]
            assert cell['routing'] == plan.record
        return complete()
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime', inspect)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    result = asyncio.run(run_experiment(cases, conf, tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='architectures'))
    assert all(r['status'] == 'complete' for r in result['runs'])
