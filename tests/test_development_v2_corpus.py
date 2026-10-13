"""Development-v2 fixtures: specialist paths, tempting allegations, sealed gold controls."""
import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest
from test_evaluation_context import complete, patch_engine

from scrutare.config import parse_config
from scrutare.engine.routing import plan_routing
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.runner import run_experiment

ROOT = Path(__file__).parents[1] / 'evaluations' / 'development-v2'
PRIVATE = ('labels', 'controls', 'build.py', 'verify.py', 'manifest.json',
           'mechanical-report.json', 'freeze-sha256.json')


def verifier():
    spec = importlib.util.spec_from_file_location('development_v2_verify', ROOT / 'verify.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gold(case):
    return json.loads(case.labels.read_text())


def test_corpus_is_development_only_original_and_isolated():
    cases = load_corpus(ROOT, 'development')
    assert len(cases) == 12 and load_corpus(ROOT, 'holdout') == ()
    assert all(re.fullmatch(r'dev2-[0-9]{2}', case.id) for case in cases)
    assert not {c.id for c in cases} & {c.id for c in load_corpus(ROOT.parent / 'pilot', 'all')}
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for entry in manifest['cases']:
        assert entry['provenance']['origin'] == 'original'
        assert entry['provenance']['license'] == 'Elastic-2.0'
        assert entry['provenance']['external_material'] is False
        assert set(entry) == {'id', 'split', 'domain', 'capture', 'labels', 'provenance',
                              'context_sources'}
    defects = clean = 0
    for case in cases:
        label = gold(case)
        assert {p.name for p in case.capture.iterdir()} == {
            'diff.patch', 'files.json', 'metadata.json'}
        assert case.capture not in case.labels.parents
        assert len(case.context_sources) == 2 and label['counterexamples']
        metadata = json.loads((case.capture / 'metadata.json').read_text())
        for side in case.context_sources:
            assert metadata['pull_request'][side.side]['repo']['full_name'] == side.repository
            assert not any(part in PRIVATE for part in side.directory.parts)
        if label['defects']:
            defects += 1
            assert len(label['defects']) == 1 and not label['tempting_allegations']
        else:
            clean += 1
            assert all(a['allegation'] and a['why_wrong'] and a['evidence']
                       for a in label['tempting_allegations'])
            assert label['tempting_allegations']
    assert (defects, clean) == (8, 4)
    supporting = sorted({p for case in cases for p in gold(case)['unchanged_supporting_paths']})
    assert json.loads((ROOT / 'related-paths.json').read_text()) == supporting
    assert len(supporting) >= 4


def test_trusted_gold_controls_and_sealed_inventory():
    module = verifier()
    report = module.verify(ROOT)
    assert report['failures'] == []
    assert report['cases'] == report['patches_verified'] == 12
    assert report['commit_pairs_reconstructed'] == 12
    assert report['defects_verified'] == 8 and report['clean_controls_verified'] == 4
    assert report['control_executions'] == 24 and report['tempting_allegation_cases'] == 4
    assert report['routing_classes'] == {'covered': 8, 'missed': 3, 'none': 1}
    assert report == json.loads((ROOT / 'mechanical-report.json').read_text())
    assert module.inventory(ROOT) == json.loads((ROOT / 'freeze-sha256.json').read_text())


def test_verifier_refuses_other_paths_and_changed_bytes(tmp_path, monkeypatch):
    module = verifier()
    with pytest.raises(ValueError, match='Only checked-in original'):
        module.verify(tmp_path)
    sealed = module.inventory(ROOT)
    changed = {**sealed, 'files': {**sealed['files'], 'README.md': '0' * 64}}
    monkeypatch.setattr(module, 'inventory', lambda root: changed)
    with pytest.raises(ValueError, match='sealed inventory'):
        module.verify(ROOT)


def routed_config():
    return parse_config(b'models: {default: {provider: openai, model: frozen-v1}}\n'
                        b'budgets: {review_max_tokens: 600, per_persona_tokens: 100}\n'
                        b'routing: {enabled: true}\n')


def test_specialists_activate_from_changed_paths_alone_never_from_labels():
    activated = {}
    for case in load_corpus(ROOT, 'development'):
        expectation = gold(case)['routing_expectation']
        plan = plan_routing(json.loads((case.capture / 'files.json').read_text()), (),
                            routed_config())
        names = [row['name'] for row in plan.record['activated']]
        assert all(row['reason'] == 'matched_path' for row in plan.record['activated'])
        assert names == expectation['expected_specialists'], case.id
        assert (expectation['class'] == 'covered') == bool(names)
        assert plan.record['manual_force'] == []
        activated[case.id] = names
    assert sum(n == ['performance-concurrency'] for n in activated.values()) == 4
    assert sum(n == ['data-integrity-migrations'] for n in activated.values()) == 4
    assert sum(not n for n in activated.values()) == 4


def test_production_projection_exposes_sources_but_no_evaluator_artifact(tmp_path, monkeypatch):
    cases = load_corpus(ROOT, 'development')
    related = json.loads((ROOT / 'related-paths.json').read_text())
    config = parse_config(json.dumps({
        'models': {'default': {'provider': 'openai', 'model': 'frozen-v1'}},
        'budgets': {'review_max_tokens': 600, 'per_persona_tokens': 100},
        'context': {'enabled': True, 'related_paths': related}}).encode())
    secrets = [text for case in cases for text in (
        *(d['match_criteria'] for d in gold(case)['defects']),
        *(a['why_wrong'] for a in gold(case)['tempting_allegations']),
        'HookLock', 'LostAcknowledgement', 'routing_expectation')]
    roots = []
    async def execute(directory, conf, *, runtime):
        roots.append(directory / 'review-inputs')
        return complete()
    patch_engine(monkeypatch, execute)
    result = asyncio.run(run_experiment(cases, config, tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='architectures', variant_names=('evidence', 'routing')))
    assert len(result['runs']) == len(roots) == 72
    for root in roots:
        visible = b''.join(p.read_bytes() for p in root.rglob('*') if p.is_file()).decode()
        assert not any(secret in visible for secret in secrets)
        assert not any(p.name in PRIVATE for p in root.rglob('*'))
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_text())
    for case in cases:
        label = gold(case)
        cells = snapshot['case_variants'][case.id]
        assert cells['evidence']['routing'] is None
        assert cells['evidence']['effective_personas'] == [
            'senior-dev', 'junior-dev', 'security', 'devops']
        assert cells['routing']['effective_personas'][4:] == label['routing_expectation'][
            'expected_specialists']
        assert sum(cells['routing']['allocations'].values()) == 600
        root = tmp_path / 'out' / case.id / 'routing/1/review-inputs'
        manifest = json.loads((root / 'repository-context.json').read_text())
        present = {e['path'] for e in manifest['entries'] if not e.get('absent')}
        assert set(label['unchanged_supporting_paths']) <= present
