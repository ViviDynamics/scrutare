"""Development-v3 fixtures: real Ruff diagnostics, separating and outside-capture cases."""
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

ROOT = Path(__file__).parents[1] / 'evaluations' / 'development-v3'
PRIVATE = ('labels', 'controls', 'build.py', 'verify.py', 'manifest.json',
           'mechanical-report.json', 'freeze-sha256.json')


def verifier():
    spec = importlib.util.spec_from_file_location('development_v3_verify', ROOT / 'verify.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gold(case):
    return json.loads(case.labels.read_text())


def test_corpus_is_development_only_original_and_isolated():
    cases = load_corpus(ROOT, 'development')
    assert len(cases) == 12 and load_corpus(ROOT, 'holdout') == ()
    assert all(re.fullmatch(r'dev3-[0-9]{2}', case.id) for case in cases)
    assert not {c.id for c in cases} & {c.id for c in load_corpus(ROOT.parent / 'pilot', 'all')}
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for entry in manifest['cases']:
        assert entry['provenance']['origin'] == 'original'
        assert entry['provenance']['license'] == 'Elastic-2.0'
        assert entry['provenance']['external_material'] is False
        assert set(entry) == {'id', 'split', 'domain', 'capture', 'labels', 'provenance',
                              'context_sources'}
        assert 'analysis_capture' not in entry  # tool evidence is captured per job, privately
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
    assert (defects, clean) == (7, 5)
    supporting = sorted({p for case in cases for p in gold(case)['unchanged_supporting_paths']})
    assert json.loads((ROOT / 'related-paths.json').read_text()) == supporting
    assert len(supporting) >= 4
    outside = {case.id: gold(case)['evidence_outside_capture'] for case in cases}
    assert {k for k, v in outside.items() if v} == {'dev3-11', 'dev3-12'}
    assert not {p for v in outside.values() for p in v} & set(supporting)


def test_trusted_gold_controls_and_sealed_inventory():
    module = verifier()
    report = module.verify(ROOT)
    assert report['failures'] == []
    assert report['cases'] == report['patches_verified'] == 12
    assert report['commit_pairs_reconstructed'] == 12
    assert report['defects_verified'] == 7 and report['clean_controls_verified'] == 5
    assert report['control_executions'] == 24 and report['tempting_allegation_cases'] == 5
    assert report['clean_labels_requiring_context'] == 0
    assert report['evidence_outside_capture_cases'] == 2
    assert report['routing_classes'] == {'covered': 5, 'missed': 0, 'none': 7}
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
    monkeypatch.undo()
    monkeypatch.setattr(module.Path, 'is_file', lambda self: self.name != 'freeze-sha256.json'
                        and Path.exists(self) and not Path.is_dir(self))
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
    assert sum(n == ['performance-concurrency'] for n in activated.values()) == 3
    assert sum(n == ['data-integrity-migrations'] for n in activated.values()) == 2
    assert sum(not n for n in activated.values()) == 7


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
        'HookLock', 'RecordingLock', 'CrashAfterFirstCommit', 'routing_expectation',
        'tool_expectation', 'ruff_codes')]
    for case in cases:
        head = next(s.directory for s in case.context_sources if s.side == 'head')
        secrets.extend((head / path).read_text() for path in gold(case)['evidence_outside_capture'])
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
        captured = {e['path']: e for e in manifest['entries']
                    if e['status'] == 'captured' and e['side'] == 'head'}
        for path in label['unchanged_supporting_paths']:
            head = next(s.directory for s in case.context_sources if s.side == 'head')
            assert (root / captured[path]['artifact']).read_bytes() == (head / path).read_bytes()


def test_pinned_ruff_reports_exactly_the_labeled_diagnostics(tmp_path):
    import sys

    from scrutare.evaluation.tool_capture import capture_development_ruff

    index = capture_development_ruff(ROOT, tmp_path / 'tools',
                                     executable=Path(sys.executable).parent / 'ruff')
    relations = {}
    for case in load_corpus(ROOT, 'development'):
        expectation = gold(case)['tool_expectation']
        assert index['cases'][case.id]['status'] == 'complete'
        capture = json.loads((tmp_path / 'tools' / f'{case.id}.json').read_text())
        files = {f['filename'] for f in json.loads((case.capture / 'files.json').read_text())}
        codes = sorted(d['code'] for d in capture['diagnostics'])
        assert codes == sorted(expectation['ruff_codes']), case.id
        # Every diagnostic is in a changed file, so the review adapter can bind it.
        assert all(d['filename'].split('/src/', 1)[1] in {f.split('src/', 1)[1] for f in files}
                   for d in capture['diagnostics'])
        relations[case.id] = expectation['relation']
    assert [k for k, v in relations.items() if v == 'names_the_defect'] == ['dev3-01', 'dev3-02']
    assert [k for k, v in relations.items() if v == 'suggests_the_tempting_allegation'] == [
        'dev3-03']
    assert [k for k, v in relations.items() if v == 'silent_on_a_real_defect'] == ['dev3-04']


def test_lint_exemptions_are_exactly_the_labeled_fixture_diagnostics():
    """The repository lint skips only the fixture lines the labels say Ruff must report."""
    text = (ROOT.parents[1] / 'pyproject.toml').read_text()
    section = text.split('[tool.ruff.lint.per-file-ignores]', 1)[1].split('\n[', 1)[0]
    declared = {match[0]: json.loads(match[1]) for match in re.findall(
        r'^"([^"]+)" = (\[[^\]]*\])$', section, flags=re.MULTILINE)}
    expected = {}
    for case in load_corpus(ROOT, 'development'):
        codes = sorted(set(gold(case)['tool_expectation']['ruff_codes']))
        if not codes:
            continue
        changed, = json.loads((case.capture / 'files.json').read_text())
        sides = ['head'] if gold(case)['defects'] else ['base', 'head']
        for side in sides:
            expected[f"evaluations/development-v3/sources/{case.id}/{side}/"
                     f"{changed['filename']}"] = codes
    assert declared == expected


def test_tools_arm_shows_bound_diagnostics_and_assessment_arm_shows_none(tmp_path, monkeypatch):
    import shutil
    import sys

    from scrutare.evaluation.tool_capture import capture_development_ruff

    private = tmp_path / 'corpus'
    shutil.copytree(ROOT, private)
    capture_development_ruff(private, tmp_path / 'tools',
                             executable=Path(sys.executable).parent / 'ruff')
    manifest = json.loads((private / 'manifest.json').read_text())
    manifest['cases'] = [e for e in manifest['cases'] if e['id'] in (
        'dev3-01', 'dev3-03', 'dev3-04')]
    (private / 'analysis').mkdir()
    for entry in manifest['cases']:
        shutil.copy(tmp_path / 'tools' / f"{entry['id']}.json", private / 'analysis')
        entry['analysis_capture'] = f"analysis/{entry['id']}.json"
    (private / 'manifest.json').write_text(json.dumps(manifest))
    config = parse_config(json.dumps({
        'models': {'default': {'provider': 'openai', 'model': 'frozen-v1'}},
        'budgets': {'review_max_tokens': 600, 'per_persona_tokens': 100},
        'context': {'enabled': True, 'related_paths': json.loads(
            (ROOT / 'related-paths.json').read_text())}}).encode())
    async def execute(directory, conf, *, runtime):
        return complete()
    patch_engine(monkeypatch, execute)
    out = tmp_path / 'out'
    asyncio.run(run_experiment(load_corpus(private, 'development'), config, out,
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='architectures', variant_names=('assessment', 'tools')))
    seen = {}
    for case in ('dev3-01', 'dev3-03', 'dev3-04'):
        assert not (out / case / 'assessment/1/review-inputs/static-analysis.json').exists()
        analysis = json.loads(
            (out / case / 'tools/1/review-inputs/static-analysis.json').read_text())
        assert analysis['status'] == 'complete'
        seen[case] = sorted(d['rule'] for d in analysis['diagnostics'])
    assert seen == {'dev3-01': ['F811'], 'dev3-03': ['F401', 'F401'], 'dev3-04': []}
