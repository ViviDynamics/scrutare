"""Explicit original corpus source declarations feed production immutable context."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_runner import config, corpus
from test_nare_cli_integration import installed as installed

from scrutare.config import parse_config
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.runner import run_experiment


def declared(root, *, fork='pilot/fork'):
    corpus(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    sources = {}
    for side, revision, repository in [('base', 'b'*40, 'pilot/local'),
                                       ('head', 'a'*40, fork)]:
        directory = root / 'sources/a' / side
        directory.mkdir(parents=True)
        (directory / 'app.py').write_text('x=1\n' if side == 'base' else 'x=2\n')
        (directory / 'caller.py').write_text('caller_original()\n')
        sources[side] = {'directory': f'sources/a/{side}', 'repository': repository,
                         'revision': revision}
    manifest['cases'][0]['context_sources'] = sources
    (root / 'manifest.json').write_text(json.dumps(manifest))
    return load_corpus(root)


def context_config():
    return parse_config(b'models: {default: {provider: openai, model: frozen-v1}}\n'
                        b'budgets: {review_max_tokens: 400, per_persona_tokens: 100}\n'
                        b'context: {enabled: true, related_paths: [caller.py]}\n')


def patch_engine(monkeypatch, execute):
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='2026.10.4', contract=1)))


def complete():
    return SimpleNamespace(status='complete', accounting_complete=True,
                           usage=TokenUsage(input=1), verdict=SimpleNamespace(findings=()))


def test_context_freezes_declared_fork_sources_before_first_await(tmp_path, monkeypatch):
    root = tmp_path / 'corpus'
    cases = declared(root)
    original = (cases[0].capture / 'metadata.json').read_bytes()
    async def execute(run_dir, conf, *, runtime):
        manifest = json.loads((run_dir / 'review-inputs/repository-context.json').read_bytes())
        assert manifest['source']['kind'] == 'corpus_snapshot'
        assert manifest['revisions']['head']['repository'] == 'pilot/fork'
        entries = [e for e in manifest['entries'] if e['path'] == 'caller.py']
        assert len(entries) == 2
        assert all((run_dir / 'review-inputs' / e['artifact']).read_bytes()
                   == b'caller_original()\n' for e in entries)
        assert (run_dir / 'metadata.original.json').read_bytes() == original
        assert not any('labels' in p.name for p in (run_dir / 'review-inputs').iterdir())
        (root / 'sources/a/head/caller.py').write_text('mutated()\n')
        return complete()
    patch_engine(monkeypatch, execute)
    result = asyncio.run(run_experiment(cases, context_config(), tmp_path / 'out',
                         runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    assert len(result['runs']) == 9 and all(r['status'] == 'complete' for r in result['runs'])
    snapshot = json.loads((tmp_path / 'out/snapshot.json').read_bytes())
    source = snapshot['cases'][0]['context_source']
    assert source['metadata_transformations'][0]['side'] == 'head'
    assert source['snapshots'][0]['content_sha256']
    assert (cases[0].capture / 'metadata.json').read_bytes() == original


@pytest.mark.parametrize('problem', [
    'escape', 'absolute', 'backslash', 'symlink', 'labels', 'revision', 'identity'])
def test_declared_context_rejects_unsafe_or_mismatched_source(tmp_path, problem):
    declared(tmp_path)
    path = tmp_path / 'manifest.json'
    manifest = json.loads(path.read_text())
    source = manifest['cases'][0]['context_sources']['base']
    if problem == 'escape':
        source['directory'] = '../outside'
    elif problem == 'absolute':
        source['directory'] = str(tmp_path / 'sources/a/base')
    elif problem == 'backslash':
        source['directory'] = 'sources\\a\\base'
    elif problem == 'symlink':
        (tmp_path / 'sources/a/base/link.py').symlink_to(tmp_path / 'labels/a.json')
    elif problem == 'labels':
        source['directory'] = 'labels'
    elif problem == 'revision':
        source['revision'] = 'c'*40
    else:
        source['repository'] = 'wrong/repo'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_corpus(tmp_path)


def test_context_requires_explicit_sources_and_diff_only_stays_identical(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    async def execute(run_dir, conf, *, runtime):
        assert ((run_dir / 'metadata.json').read_bytes()
                == (cases[0].capture / 'metadata.json').read_bytes())
        assert not (run_dir / 'metadata.original.json').exists()
        return complete()
    patch_engine(monkeypatch, execute)
    asyncio.run(run_experiment(cases, config(), tmp_path / 'plain',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    with pytest.raises(ValueError, match='source'):
        asyncio.run(run_experiment(cases, context_config(), tmp_path / 'context',
            runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))


def test_source_inventory_bound_is_enforced_before_runtime(tmp_path, monkeypatch):
    declared(tmp_path / 'corpus')
    monkeypatch.setattr('scrutare.evaluation.corpus.MAX_SOURCE_BYTES', 8, raising=False)
    with pytest.raises(ValueError, match='bound'):
        load_corpus(tmp_path / 'corpus')


def test_unavailable_head_is_preserved_without_declared_head(tmp_path, monkeypatch):
    declared(tmp_path / 'corpus')
    path = tmp_path / 'corpus/manifest.json'
    manifest = json.loads(path.read_text())
    del manifest['cases'][0]['context_sources']['head']
    path.write_text(json.dumps(manifest))
    cases = load_corpus(path.parent)
    async def execute(run_dir, conf, *, runtime):
        context = json.loads((run_dir / 'review-inputs/repository-context.json').read_bytes())
        assert context['revisions']['head']['repository'] is None
        assert all(e['status'] == 'unavailable' for e in context['entries'] if e['side'] == 'head')
        return complete()
    patch_engine(monkeypatch, execute)
    result = asyncio.run(run_experiment(cases, context_config(), tmp_path / 'out',
                         runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
    assert all(r['status'] == 'complete' for r in result['runs'])


def test_pilot_explicit_source_declarations_cover_all_cases():
    cases = load_corpus(Path(__file__).resolve().parents[1] / 'evaluations/pilot', 'all')
    assert len(cases) == 30
    assert all({s.side for s in case.context_sources} == {'base', 'head'} for case in cases)


def test_native_context_reads_declared_fork_source_without_corpus_access(
        tmp_path, installed, monkeypatch):
    from test_nare_cli_integration import offline_runtime, text, tool

    from scrutare.engine.repository_context import _artifact

    root = tmp_path / 'corpus'
    cases = declared(root)
    runtime = offline_runtime(tmp_path, installed, default={'replies': [
        tool(args={'path': 'repository-context.json'}),
        tool(args={'path': _artifact('head', 'caller.py')}, call_id='caller'),
        text({'findings': []})]})
    path = tmp_path / 'offline-spec.json'
    spec = json.loads(path.read_text())
    spec['scenarios'] = [{'purpose': 'review', 'attempt': 'attempt-0001',
        'prompt_prefix': 'Arbitrate',
        'scenario': {'replies': [text({'findings': [], 'converged': True})]}}]
    path.write_text(json.dumps(spec))
    binary = tmp_path / 'bin'
    binary.mkdir()
    gh = binary / 'gh'
    gh.write_text('#!/bin/sh\nprintf forbidden > "$GH_FORBIDDEN"\nexit 99\n')
    gh.chmod(0o700)
    monkeypatch.setenv('PATH', f'{binary}:/usr/bin:/bin')
    monkeypatch.setenv('GH_FORBIDDEN', str(tmp_path / 'github-called'))
    conf = parse_config(b'models: {default: {provider: openai, model: offline-model, '
                        b'base_url: "https://offline.invalid/v1"}}\n'
                        b'budgets: {review_max_tokens: 500, per_persona_tokens: 100}\n'
                        b'context: {enabled: true, related_paths: [caller.py]}\n')
    result = asyncio.run(run_experiment(cases, conf, tmp_path / 'experiment',
                                       runtime=runtime, evidence_kind='offline'))
    assert all(r['status'] == 'complete' and r['accounting_complete'] for r in result['runs'])
    assert not (tmp_path / 'github-called').exists()
    observations = list((tmp_path / 'experiment').rglob('offline-observations.json'))
    assert observations
    assert any('caller_original()' in p.read_text() for p in observations)
    assert all(json.loads(p.read_bytes())['guard_violations'] == [] for p in observations)
    assert all(json.loads(p.read_bytes())['credential_names'] == [] for p in observations)
    for run in result['runs']:
        directory = Path(run['run_dir'])
        for path in directory.glob('sessions/*/attempt-*/session.json'):
            session = json.loads(path.read_text())
            assert session['policy']['root'] == str(directory / 'review-inputs')
            assert session['policy']['tools'] == ['read']


def test_context_source_cannot_overlap_another_cases_labels(tmp_path):
    declared(tmp_path)
    path = tmp_path / 'manifest.json'
    manifest = json.loads(path.read_text())
    other = dict(manifest['cases'][0], id='other', labels='private-other/labels.json')
    other.pop('context_sources')
    directory = tmp_path / 'private-other'
    directory.mkdir()
    (directory / 'labels.json').write_text('{"case_id":"other","defects":[]}')
    manifest['cases'].append(other)
    manifest['cases'][0]['context_sources']['base']['directory'] = 'private-other'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='labels'):
        load_corpus(tmp_path)


def test_same_snapshot_identity_cannot_bind_different_source_bytes(tmp_path, monkeypatch):
    root = tmp_path / 'corpus'
    declared(root, fork='pilot/local')
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['cases'][0]['context_sources']['head']['revision'] = 'b'*40
    manifest_path.write_text(json.dumps(manifest))
    path = root / 'captures/a/metadata.json'
    metadata = json.loads(path.read_bytes())
    metadata['head_sha'] = metadata['pull_request']['head']['sha'] = 'b'*40
    path.write_text(json.dumps(metadata))
    cases = load_corpus(root)
    with pytest.raises(ValueError, match='identity'):
        asyncio.run(run_experiment(cases, context_config(), tmp_path / 'out',
            runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))


def test_context_metadata_duplicate_identities_are_rejected(tmp_path, monkeypatch):
    root = tmp_path / 'corpus'
    cases = declared(root)
    path = root / 'captures/a/metadata.json'
    original = path.read_text()
    path.write_text(original.replace('"head":', '"head": {"sha": "ambiguous"}, "head":'))
    with pytest.raises(ValueError, match='duplicate'):
        asyncio.run(run_experiment(cases, context_config(), tmp_path / 'out',
            runtime=NareRuntime(Path('/unused')), evidence_kind='offline'))
