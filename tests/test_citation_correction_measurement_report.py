"""The #91 citation-correction development measurement reproduces from public files."""
import hashlib
import importlib.util
import json
import re
import shutil

import pytest
from test_glm_qualification_report import REPORTS

PACKAGE = REPORTS / '2026-10-25-citation-correction-measurement'
JOBS = ('base', 'lines', 'lines-correct')


def module():
    spec = importlib.util.spec_from_file_location('citation_measurement',
                                                  PACKAGE / 'reproduce.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(monkeypatch, package, output):
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(package),
                                     '--output', str(output)])
    module().main()
    return json.loads(output.read_text())


def copy(tmp_path):
    target = tmp_path / 'package'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    return target


def rehash(package, name):
    hashes = json.loads((package / 'published-sha256.json').read_text())
    hashes[name] = hashlib.sha256((package / name).read_bytes()).hexdigest()
    (package / 'published-sha256.json').write_text(json.dumps(hashes))


def test_public_package_reproduces_the_measurement(tmp_path, monkeypatch):
    summary = run(monkeypatch, PACKAGE, tmp_path / 'summary.json')
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['qualification'] is False and summary['defaults_changed'] is False
    assert summary['holdout_used'] is False and summary['model'] == 'spark/glm-5.3-flash'
    jobs = summary['jobs']
    assert [jobs[job]['discovery_sessions'] for job in JOBS] == [48, 48, 48]
    assert [jobs[job]['cells_complete_and_accounted'] for job in JOBS] == [10, 12, 12]
    assert all(jobs[job]['first_answers_refused_for_citations']['sessions'] == 0
               for job in JOBS)
    assert jobs['base']['not_complete_by_cause'] == {'output_cap': 2}
    assert jobs['lines-correct']['corrections'] == {'offered': 0, 'by_outcome': {},
                                                    'tokens': 0}
    assert sum(jobs[job]['tokens']['cells_total'] for job in JOBS) == 2145458
    published = json.loads((PACKAGE / 'published-sha256.json').read_text())
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert set(published) == on_disk - {'published-sha256.json'}


def test_the_three_jobs_differ_only_in_the_settings_under_test():
    evaluations = {job: json.loads((PACKAGE / job / 'evaluation.json').read_text())
                   for job in JOBS}
    assert 'line_counts' not in evaluations['base']['context']
    assert evaluations['lines']['context']['line_counts'] is True
    assert evaluations['lines-correct']['findings'] == {'evidence': 'v2',
                                                        'citation_correction': True}
    rails = {json.dumps(evaluation['models'], sort_keys=True)
             for evaluation in evaluations.values()}
    assert rails == {json.dumps({'default': {
        'provider': 'openai', 'base_url': 'https://litellm.vividynamics.com/v1',
        'model': 'spark/glm-5.3-flash', 'structured_output': 'json_schema',
        'max_output_tokens': 20000}}, sort_keys=True)}
    preparations = [json.loads((PACKAGE / job / 'preparation.json').read_text())
                    for job in JOBS]
    assert {p['source_commit'] for p in preparations} == {
        'ff63a52459310641e07b9107d0ffe1d846973037'}
    assert {p['release_tag'] for p in preparations} == {'2026.10.34'}
    assert all(p['corpus'] == 'evaluations/pilot' and p['split'] == 'development'
               for p in preparations)


@pytest.mark.parametrize('change', ['evaluation', 'record', 'unlisted', 'hash'])
def test_tampering_is_refused(tmp_path, monkeypatch, change):
    package = copy(tmp_path)
    if change == 'evaluation':
        path = package / 'lines' / 'evaluation.json'
        document = json.loads(path.read_text())
        document['rounds']['max'] = 1
        path.write_text(json.dumps(document))
        # Both the preparation's input hash and the job comparison now disagree.
        rehash(package, 'lines/evaluation.json')
    elif change == 'record':
        path = package / 'base' / 'session-diagnosis.json'
        document = json.loads(path.read_text())
        document['sessions'][0]['citation_correction'] = {
            'offered': False, 'defects': 0, 'outcome': None, 'exit_code': None}
        path.write_text(json.dumps(document))
        rehash(package, 'base/session-diagnosis.json')
    elif change == 'unlisted':
        (package / 'extra.json').write_text('{}')
    else:
        (package / 'summary.json').write_text('{}')
    with pytest.raises(ValueError):
        run(monkeypatch, package, tmp_path / 'summary.json')


def test_no_public_file_names_a_local_path_or_a_key():
    for path in PACKAGE.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        text = path.read_text()
        assert not re.search(r'\bsk-[A-Za-z0-9_-]{12,}', text) and 'Bearer ' not in text, path
        if path.name != 'preparation.json':
            assert '/home/' not in text and '/tmp/' not in text, path
