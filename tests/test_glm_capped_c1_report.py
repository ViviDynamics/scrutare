"""The capped onprem/glm-5.3-flash schedule c1 (#82) reproduces from public files."""
import hashlib
import importlib.util
import json
import shutil

import pytest
from test_glm_qualification_report import REPORTS

PACKAGE = REPORTS / '2026-10-22-glm-capped-c1-qualification'
S2 = REPORTS / '2026-10-21-glm-structured-s2-qualification' / 's2'


def module():
    spec = importlib.util.spec_from_file_location('glm_capped_c1', PACKAGE / 'reproduce.py')
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


def test_public_package_reproduces_gate_causes_cap_and_comparison(tmp_path, monkeypatch):
    summary = run(monkeypatch, PACKAGE, tmp_path / 'summary.json')
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['qualified'] is False and summary['defaults_promoted'] is False
    assert summary['actual_human_judgments'] == 0 and summary['holdout_used'] is False
    c1 = summary['jobs']['c1']
    assert c1['timeout_seconds'] == 1800 and c1['rerun_attempts'] == 0
    assert c1['tests']['aggregate_bug_cells_nonempty_valid_v2'] is False
    assert c1['tests']['assessed_bug_cells_actual_complete_assessor'] is True
    assert c1['gate_items'] == {
        'cells': 36, 'cells_complete_and_accounted': 31,
        'cells_complete_and_accounted_first_attempt': 31,
        'aggregate_defect_cells_nonempty_valid_v2': 4,
        'assessed_defect_cells_actual_complete_assessor': 6}
    causes = summary['session_causes']
    assert causes['c1']['not_complete_by_cause'] == {
        'answer_evidence_refused': 1, 'budget_overrun_assessor': 1,
        'citation_past_file_end': 2, 'provider_aborted_repetition': 1}
    assert causes['c1']['cap']['max_output_tokens'] == 20000
    assert causes['c1']['cap']['largest_request_output_tokens'] == 20000
    assert causes['c1']['cap']['sessions_recorded_reached'] == 0
    assert [row[:3] for row in causes['c1']['cap']['requests_at_the_cap']] == [
        ['tests-consumer-fixture/focused1/2', 'evidence-assessor', 'budget']]
    assert causes['s2']['not_complete_by_cause'] == {
        'citation_past_file_end': 1, 'session_timeout': 1}
    assert 'cap' not in causes['s2']
    assert summary['side_by_side']['s2']['cells_complete_and_accounted'] == 34
    for name in ('sol-r1', 'sol-r2', 'sol-r3'):
        assert summary['side_by_side'][name]['gate_pass_under_this_package_rule'] is True
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
        if path.relative_to(PACKAGE).as_posix() == 'c1/preparation.json':
            console = json.loads(text)['nare_runtime']['console']
            assert text.count('/home/') == 1 and console.endswith('nare-2026.10.12/.venv/bin/nare')
        else:
            assert '/home/' not in text, path


def test_c1_differs_from_s2_only_in_the_cap_source_and_stated_terms():
    c1 = json.loads((PACKAGE / 'c1/evaluation.json').read_text())
    s2 = json.loads((S2 / 'evaluation.json').read_text())
    assert c1['models']['default'].pop('max_output_tokens') == 20000
    assert c1 == s2
    gates = [json.loads(p.read_text()) for p in (PACKAGE / 'c1/gate.json', S2 / 'gate.json')]
    changed = {key for key in set(gates[0]) | set(gates[1])
               if gates[0].get(key) != gates[1].get(key)}
    assert changed == {'profile', 'max_output_tokens', 'source_commit', 'release_tag',
                       'changed_from_reference', 'changes_from_s2',
                       'unchanged_from_s2_with_reasons',
                       'citation_past_file_end', 'launch_condition', 'schedules_allowed',
                       'fallback', 'predecessor', 's2_gate_sha256'}
    assert gates[0]['pass_rule'] == gates[1]['pass_rule']
    assert gates[0]['s2_gate_sha256'] == hashlib.sha256((S2 / 'gate.json').read_bytes()).hexdigest()
    prepared = [json.loads(p.read_text())['input_sha256']
                for p in (PACKAGE / 'c1/preparation.json', S2 / 'preparation.json')]
    assert {k: v for k, v in prepared[0].items() if k.startswith('corpus/')} == {
        k: v for k, v in prepared[1].items() if k.startswith('corpus/')}
    amendment = json.loads((PACKAGE / 'c1/amendment.json').read_text())
    assert amendment['authority'].startswith('maintainer ruling dated 2026-10-22')


def test_frozen_files_are_the_ones_posted_on_issue_51():
    for job, posted in module().POSTED_ON_51.items():
        for name in ('preparation.json', 'gate.json', 'evaluation.json', 'deviations.json',
                     'cap-sizing.json', 'amendment.json'):
            digest = hashlib.sha256((PACKAGE / job / name).read_bytes()).hexdigest()
            assert digest == posted[name]
        freeze = json.loads((PACKAGE / 'freeze-record.json').read_text())['freezes'][job]
        assert freeze['wheel_sha256'] == posted['wheel']


def test_reproduction_refuses_regenerated_frozen_files_or_extra_files(tmp_path, monkeypatch):
    for name, old, new in (
            ('c1/preparation.json', '"concurrency": 3', '"concurrency": 4'),
            ('c1/deviations.json', '"label": "c1"', '"label": "c2"'),
            ('c1/amendment.json', '"label": "c1"', '"label": "c2"'),
            ('c1/cap-sizing.json', '"cap": 20000', '"cap": 30000'),
            ('c1/mechanical-qualification.json', '"gate_sha256":"', '"gate_sha256":"0')):
        package = copy(tmp_path, name.replace('/', '-'))
        text = (package / name).read_text()
        assert old in text, name
        (package / name).write_text(text.replace(old, new, 1))
        rehash(package, name)
        with pytest.raises(ValueError, match='posted on #51|not bound'):
            run(monkeypatch, package, tmp_path / 'out.json')
    package = copy(tmp_path, 'extra')
    (package / 'c1/__pycache__').mkdir()
    (package / 'c1/__pycache__/hidden.json').write_text('{}')
    with pytest.raises(ValueError, match='outside the hash manifest'):
        run(monkeypatch, package, tmp_path / 'out.json')


@pytest.mark.parametrize('change', ['limit', 'reached', 'requests'])
def test_reproduction_refuses_a_diagnosis_that_disagrees_with_the_cap(tmp_path, monkeypatch,
                                                                       change):
    package = copy(tmp_path, change)
    name = 'c1/session-diagnosis.json'
    document = json.loads((package / name).read_text())
    row = document['sessions'][0]
    if change == 'limit':
        row['max_output_tokens']['limit'] = 19999
    elif change == 'reached':
        row['max_output_tokens']['reached'] = True
    else:
        row['request_output_tokens'].append(1)
    (package / name).write_text(json.dumps(document))
    rehash(package, name)
    with pytest.raises(ValueError, match='session diagnosis disagrees'):
        run(monkeypatch, package, tmp_path / 'out.json')


def test_reproduction_refuses_changed_evidence(tmp_path, monkeypatch):
    package = copy(tmp_path, 'package')
    target = package / 'c1/experiment.json'
    target.write_text(target.read_text().replace('"failed"', '"complete"', 1))
    with pytest.raises(ValueError, match='hash mismatch'):
        run(monkeypatch, package, tmp_path / 'out.json')
    rehash(package, 'c1/experiment.json')
    with pytest.raises(ValueError, match='disagrees|differ'):
        run(monkeypatch, package, tmp_path / 'out.json')


@pytest.mark.parametrize(('name', 'path', 'value'), [
    ('gate.json', ('max_output_tokens',), 16000),
    ('preparation.json', ('max_output_tokens',), None),
    ('evaluation.json', ('models', 'default', 'max_output_tokens'), 30000)])
def test_frozen_files_must_agree_on_the_cap(tmp_path, monkeypatch, name, path, value):
    package = copy(tmp_path, 'cap')
    document = json.loads((package / 'c1' / name).read_text())
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    (package / 'c1' / name).write_text(json.dumps(document))
    rehash(package, f'c1/{name}')
    with pytest.raises(ValueError, match='posted on #51|freeze the cap|frozen preparation'):
        run(monkeypatch, package, tmp_path / 'out.json')


def test_the_aborted_request_accounts_for_the_proxy_evaluator_rows():
    summary = json.loads((PACKAGE / 'summary.json').read_text())
    proxy = summary['proxy_logged_evaluator_tokens_c1_window']
    usage = summary['jobs']['c1']['usage_all_attempts']
    failed, = json.loads((PACKAGE / 'provider-evidence.json').read_text())[
        'spend_rows_c1_window_reread']['failed_rows']
    assert proxy['prompt_tokens'] == usage['input'] + failed['prompt_tokens']
    assert proxy['completion_tokens'] == usage['output'] + failed['completion_tokens']
    assert proxy['request_at_the_cap_completion_tokens'] == 20000


def test_every_spend_row_was_served_by_the_onprem_deployment_without_fallback():
    reread = json.loads((PACKAGE / 'provider-evidence.json').read_text())[
        'spend_rows_c1_window_reread']
    assert reread['all_rows_served_by'] == ['openai/glm-5.3-flash at http://192.0.2.40:8045/v1']
    assert reread['model_groups'] == {'onprem/glm-5.3-flash': reread['rows']}
    assert reread['attempted_fallbacks_nonzero'] == 0 and reread['attempted_retries_nonzero'] == 0
    # The spend script's "paid provider" count is the trailing-slash string mismatch, nothing else.
    assert reread['spend_script_summary']['this_key_paid_provider_rows'] == (
        reread['base_url_with_trailing_slash_rows'])
    assert reread['spend_script_summary']['this_key_attempted_fallbacks'] == {'0': reread['rows']}
