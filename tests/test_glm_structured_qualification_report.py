"""The constrained onprem/glm-5.3-flash re-qualification reproduces from public files."""
import hashlib
import importlib.util
import json
import shutil

import pytest
from test_glm_qualification_report import REPORTS

PACKAGE = REPORTS / '2026-10-21-glm-structured-qualification'
G2 = REPORTS / '2026-10-14-glm-qualification' / 'g2'


def module():
    spec = importlib.util.spec_from_file_location('glm_structured', PACKAGE / 'reproduce.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(monkeypatch, package, output):
    reproduce = module()
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(package),
                                     '--reports', str(REPORTS), '--output', str(output)])
    reproduce.main()
    return json.loads(output.read_text())


def copy(tmp_path, name):
    target = tmp_path / name
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    return target


def rehash(package, name):
    hashes = json.loads((package / 'published-sha256.json').read_text())
    hashes[name] = hashlib.sha256((package / name).read_bytes()).hexdigest()
    (package / 'published-sha256.json').write_text(json.dumps(hashes))


def test_public_package_reproduces_gate_causes_and_comparison(tmp_path, monkeypatch):
    summary = run(monkeypatch, PACKAGE, tmp_path / 'summary.json')
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['qualified'] is False and summary['defaults_promoted'] is False
    assert summary['actual_human_judgments'] == 0 and summary['holdout_used'] is False
    s1 = summary['jobs']['s1']
    assert s1['model_alias'] == 'onprem/glm-5.3-flash' and s1['timeout_seconds'] == 600
    assert s1['all_gate_tests_pass'] is False and s1['rerun_attempts'] == 0
    assert s1['gate_items'] == {
        'cells': 36, 'cells_complete_and_accounted': 13,
        'cells_complete_and_accounted_first_attempt': 13,
        'aggregate_defect_cells_nonempty_valid_v2': 4,
        'assessed_defect_cells_actual_complete_assessor': 0}
    causes = summary['session_causes']
    assert causes['s1']['not_complete_by_cause'] == {
        'budget_overrun_assessor': 8, 'budget_overrun_discovery': 10,
        'citation_past_file_end': 2, 'nare_error_reasoning_without_answer': 1,
        'timeout_600s': 7}
    assert causes['g2']['not_complete_by_cause'] == {
        'budget_overrun_assessor': 2, 'budget_overrun_discovery': 3,
        'citation_past_file_end': 1, 'schema_failure': 9, 'timeout_600s': 23}
    assert causes['s1']['sessions_requesting_constraint'] == causes['s1']['sessions'] == 152
    assert causes['s1']['sessions_with_a_constrained_turn'] == 31
    assert causes['s1']['constrained_sessions_failing_schema'] == 0
    assert causes['g2']['sessions_requesting_constraint'] == 0
    assert causes['s1']['unconstrained_sessions_by_outcome']['complete'] == 100
    assert causes['s1']['complete_discovery_median_tokens'] == 12371
    side = summary['side_by_side']
    assert side['g2']['cells_complete_and_accounted'] == 14
    for name in ('sol-r1', 'sol-r2', 'sol-r3'):
        assert side[name]['gate_pass_under_this_package_rule'] is True
    published = json.loads((PACKAGE / 'published-sha256.json').read_text())
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert set(published) == on_disk - {'published-sha256.json'}


def test_only_the_frozen_preparation_names_a_local_path():
    for path in PACKAGE.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        text = path.read_text()
        assert '/tmp/' not in text
        assert 'holdout-v1/' not in text.replace('`evaluations/holdout-v1`', '')
        if path.relative_to(PACKAGE).as_posix() == 's1/preparation.json':
            console = json.loads(text)['nare_runtime']['console']
            assert text.count('/home/') == 1 and console.endswith('nare-2026.10.12/.venv/bin/nare')
        else:
            assert '/home/' not in text, path


def test_s1_is_g2_with_the_constraint_and_identical_criteria():
    s1 = json.loads((PACKAGE / 's1/evaluation.json').read_text())
    g2 = json.loads((G2 / 'evaluation.json').read_text())
    assert s1['models']['default'].pop('structured_output') == 'json_schema'
    assert s1 == g2
    gates = [json.loads(path.read_text()) for path in (PACKAGE / 's1/gate.json', G2 / 'gate.json')]
    criteria = ('cases', 'clean_cases', 'arms', 'repeats', 'expected_runs',
                'all_runs_complete_and_accounted',
                'aggregate_defect_case_repeats_nonempty_valid_v2',
                'assessed_defect_case_repeats_actual_complete_assessor', 'max_tokens_per_run',
                'max_turns', 'timeout_seconds', 'concurrency', 'transient_retry', 'pass_rule',
                'external_spend_usd_limit', 'holdout_used')
    assert all(gates[0][key] == gates[1][key] for key in criteria)
    preparation = json.loads((PACKAGE / 's1/preparation.json').read_text())
    assert preparation['nare_runtime']['version'] == '2026.10.12'
    assert preparation['release_tag'] == '2026.10.26'


def test_frozen_files_are_the_ones_posted_on_issue_78():
    for job, posted in module().POSTED_ON_78.items():
        for name, digest in posted.items():
            assert hashlib.sha256((PACKAGE / job / name).read_bytes()).hexdigest() == digest


def test_reproduction_refuses_regenerated_freeze_mechanical_or_extra_files(tmp_path, monkeypatch):
    for name, old, new in (
            ('s1/preparation.json', '"concurrency": 3', '"concurrency": 4'),
            ('s1/mechanical-qualification.json', '"gate_sha256":"', '"gate_sha256":"0'),
            ('freeze-record.json', '"gate_sha256": "1b55a7bd', '"gate_sha256": "2b55a7bd')):
        package = copy(tmp_path, name.replace('/', '-'))
        text = (package / name).read_text()
        assert old in text, name
        (package / name).write_text(text.replace(old, new, 1))
        rehash(package, name)
        with pytest.raises(ValueError, match='posted on #78|not bound|freeze'):
            run(monkeypatch, package, tmp_path / 'out.json')
    package = copy(tmp_path, 'extra')
    (package / 's1/__pycache__').mkdir()
    (package / 's1/__pycache__/hidden.json').write_text('{}')
    with pytest.raises(ValueError, match='outside the hash manifest'):
        run(monkeypatch, package, tmp_path / 'out.json')


@pytest.mark.parametrize('name', ['s1/session-diagnosis.json', 'g2-session-diagnosis.json'])
def test_reproduction_refuses_a_diagnosis_that_disagrees_with_the_cells(tmp_path, monkeypatch,
                                                                       name):
    package = copy(tmp_path, 'diagnosis')
    document = json.loads((package / name).read_text())
    row = next(r for r in document['sessions'] if r['reason'] == 'timeout')
    row['reason'] = 'budget'
    (package / name).write_text(json.dumps(document))
    rehash(package, name)
    with pytest.raises(ValueError, match='session diagnosis disagrees'):
        run(monkeypatch, package, tmp_path / 'out.json')


def test_reproduction_refuses_changed_evidence(tmp_path, monkeypatch):
    package = copy(tmp_path, 'package')
    target = package / 's1/experiment.json'
    target.write_text(target.read_text().replace('"failed"', '"complete"', 1))
    with pytest.raises(ValueError, match='hash mismatch'):
        run(monkeypatch, package, tmp_path / 'out.json')
    rehash(package, 's1/experiment.json')
    with pytest.raises(ValueError, match='disagrees|differ'):
        run(monkeypatch, package, tmp_path / 'out.json')


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('structured_output_mode', None, 'frozen rail'),
    ('attempt', 'attempt-0002', 'more than one attempt')])
def test_reproduction_refuses_a_diagnosis_it_cannot_pair(tmp_path, monkeypatch, field, value,
                                                        message):
    package = copy(tmp_path, 'unpaired')
    name = 's1/session-diagnosis.json'
    document = json.loads((package / name).read_text())
    document['sessions'][0][field] = value
    (package / name).write_text(json.dumps(document))
    rehash(package, name)
    with pytest.raises(ValueError, match=message):
        run(monkeypatch, package, tmp_path / 'out.json')


def test_diagnosis_refuses_persona_overrides_and_rerun_cells():
    reproduce = module()
    path = PACKAGE / 's1/session-diagnosis.json'
    cells = json.loads((PACKAGE / 's1/mechanical-qualification.json').read_text())['cells']
    evaluation = json.loads((PACKAGE / 's1/evaluation.json').read_text())
    assert reproduce.diagnosis(path, cells, evaluation)['sessions'] == 152
    overridden = json.loads(json.dumps(evaluation))
    overridden['models']['overrides'] = {'security': {'structured_output': None}}
    with pytest.raises(ValueError, match='frozen rail'):
        reproduce.diagnosis(path, cells, overridden)
    rerun = json.loads(json.dumps(cells))
    rerun[0]['attempts'].append(dict(rerun[0]['attempts'][0]))
    with pytest.raises(ValueError, match='more than one attempt'):
        reproduce.diagnosis(path, rerun, evaluation)
