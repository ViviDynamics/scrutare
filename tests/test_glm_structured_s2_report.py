"""The final constrained spark/glm-5.3-flash schedule s2 reproduces from public files."""
import hashlib
import importlib.util
import json
import shutil

import pytest
from test_glm_qualification_report import REPORTS

PACKAGE = REPORTS / '2026-10-21-glm-structured-s2-qualification'
S1 = REPORTS / '2026-10-21-glm-structured-qualification' / 's1'


def module():
    spec = importlib.util.spec_from_file_location('glm_structured_s2', PACKAGE / 'reproduce.py')
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
    s2 = summary['jobs']['s2']
    assert s2['timeout_seconds'] == 1800 and s2['rerun_attempts'] == 0
    assert s2['all_gate_tests_pass'] is False
    assert s2['tests']['aggregate_bug_cells_nonempty_valid_v2'] is True
    assert s2['tests']['assessed_bug_cells_actual_complete_assessor'] is False
    assert s2['gate_items'] == {
        'cells': 36, 'cells_complete_and_accounted': 34,
        'cells_complete_and_accounted_first_attempt': 34,
        'aggregate_defect_cells_nonempty_valid_v2': 6,
        'assessed_defect_cells_actual_complete_assessor': 5}
    causes = summary['session_causes']
    assert causes['s2']['not_complete_by_cause'] == {
        'citation_past_file_end': 1, 'session_timeout': 1}
    assert causes['s2']['sessions_with_a_constrained_turn'] == 42
    assert causes['s2']['constrained_sessions_failing_schema'] == 0
    assert causes['s1']['not_complete_by_cause']['budget_overrun_assessor'] == 8
    assert causes['g2']['not_complete_by_cause']['schema_failure'] == 9
    side = summary['side_by_side']
    assert side['s1']['cells_complete_and_accounted'] == 13
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
        if path.relative_to(PACKAGE).as_posix() == 's2/preparation.json':
            console = json.loads(text)['nare_runtime']['console']
            assert text.count('/home/') == 1 and console.endswith('nare-2026.10.12/.venv/bin/nare')
        else:
            assert '/home/' not in text, path


def test_s2_differs_from_s1_only_in_the_recorded_settings():
    s2 = json.loads((PACKAGE / 's2/evaluation.json').read_text())
    s1 = json.loads((S1 / 'evaluation.json').read_text())
    assert s2['budgets'] == {'per_persona_tokens': 200000, 'review_max_tokens': 200000}
    assert s1['budgets'] == {'per_persona_tokens': 100000, 'review_max_tokens': 100000}
    assert {**s2, 'budgets': s1['budgets']} == s1
    gates = [json.loads(p.read_text()) for p in (PACKAGE / 's2/gate.json', S1 / 'gate.json')]
    changed = {key for key in set(gates[0]) | set(gates[1])
               if gates[0].get(key) != gates[1].get(key)}
    assert changed == {'profile', 'max_tokens_per_run', 'timeout_seconds', 'assessment_tokens',
                       'pass_rule', 'fallback', 'predecessor', 'changed_from_reference',
                       'unchanged_from_reference'}
    assert gates[0]['pass_rule'] == gates[1]['pass_rule'].replace('100,000', '200,000')
    preparations = [json.loads(p.read_text()) for p in (PACKAGE / 's2/preparation.json',
                                                         S1 / 'preparation.json')]
    assert preparations[0]['assessment_tokens'] == 50000
    assert preparations[0]['input_sha256']['scrutare-2026.10.2-py3-none-any.whl'] == (
        preparations[1]['input_sha256']['scrutare-2026.10.2-py3-none-any.whl'])
    amendments = json.loads((PACKAGE / 's2/deviations.json').read_text())['amendments']
    assert [a['authority'].startswith('maintainer ruling') for a in amendments] == [True]


def test_frozen_files_are_the_ones_posted_on_issue_51():
    for job, posted in module().POSTED_ON_51.items():
        for name in ('preparation.json', 'gate.json', 'evaluation.json', 'deviations.json'):
            digest = hashlib.sha256((PACKAGE / job / name).read_bytes()).hexdigest()
            assert digest == posted[name]
        freeze = json.loads((PACKAGE / 'freeze-record.json').read_text())['freezes'][job]
        assert freeze['wheel_sha256'] == posted['wheel']
        assert freeze['deviations_sha256'] == posted['deviations_before_amendment']


def test_reproduction_refuses_regenerated_freeze_deviation_or_extra_files(tmp_path, monkeypatch):
    for name, old, new in (
            ('s2/preparation.json', '"concurrency": 3', '"concurrency": 4'),
            ('s2/deviations.json', '"label": "s2"', '"label": "s3"'),
            ('s2/mechanical-qualification.json', '"gate_sha256":"', '"gate_sha256":"0')):
        package = copy(tmp_path, name.replace('/', '-'))
        text = (package / name).read_text()
        assert old in text, name
        (package / name).write_text(text.replace(old, new, 1))
        rehash(package, name)
        with pytest.raises(ValueError, match='posted on #51|not bound'):
            run(monkeypatch, package, tmp_path / 'out.json')
    package = copy(tmp_path, 'extra')
    (package / 's2/__pycache__').mkdir()
    (package / 's2/__pycache__/hidden.json').write_text('{}')
    with pytest.raises(ValueError, match='outside the hash manifest'):
        run(monkeypatch, package, tmp_path / 'out.json')


def test_reproduction_refuses_a_diagnosis_that_disagrees_with_the_cells(tmp_path, monkeypatch):
    package = copy(tmp_path, 'diagnosis')
    name = 's2/session-diagnosis.json'
    document = json.loads((package / name).read_text())
    row = next(r for r in document['sessions'] if r['reason'] == 'timeout')
    row['reason'] = 'budget'
    (package / name).write_text(json.dumps(document))
    rehash(package, name)
    with pytest.raises(ValueError, match='session diagnosis disagrees'):
        run(monkeypatch, package, tmp_path / 'out.json')


def test_reproduction_refuses_changed_evidence(tmp_path, monkeypatch):
    package = copy(tmp_path, 'package')
    target = package / 's2/experiment.json'
    target.write_text(target.read_text().replace('"failed"', '"complete"', 1))
    with pytest.raises(ValueError, match='hash mismatch'):
        run(monkeypatch, package, tmp_path / 'out.json')
    rehash(package, 's2/experiment.json')
    with pytest.raises(ValueError, match='disagrees|differ'):
        run(monkeypatch, package, tmp_path / 'out.json')


@pytest.mark.parametrize(('name', 'key', 'value'), [
    ('gate.json', 'assessment_tokens', 40000), ('gate.json', 'timeout_seconds', 600),
    ('gate.json', 'max_tokens_per_run', 100000)])
def test_frozen_files_must_agree_on_the_changed_settings(tmp_path, name, key, value):
    reproduce = module()
    package = copy(tmp_path, 'settings')
    reproduce.frozen_settings_agree(package / 's2')
    document = json.loads((package / 's2' / name).read_text())
    document[key] = value
    (package / 's2' / name).write_text(json.dumps(document))
    with pytest.raises(ValueError, match='disagree on the changed settings'):
        reproduce.frozen_settings_agree(package / 's2')


def test_the_cut_off_request_accounts_for_the_proxy_evaluator_rows():
    summary = json.loads((PACKAGE / 'summary.json').read_text())
    proxy = summary['proxy_logged_evaluator_tokens_s2_window']
    usage = summary['jobs']['s2']['usage_all_attempts']
    stalled, = json.loads((PACKAGE / 'provider-evidence.json').read_text())[
        'spend_rows_s2_window_reread']['stalled_assessor_request']
    assert proxy['prompt_tokens'] == usage['input'] + stalled['prompt_tokens']
    assert proxy['completion_tokens'] == usage['output'] + stalled['completion_tokens']
