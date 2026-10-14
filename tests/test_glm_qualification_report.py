"""The published spark/glm-5.3-flash qualification package reproduces from public files."""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPORTS = Path(__file__).parents[1] / 'evaluations/reports'
PACKAGE = REPORTS / '2026-10-14-glm-qualification'


def module():
    spec = importlib.util.spec_from_file_location('glm_qualification', PACKAGE / 'reproduce.py')
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


def test_public_package_reproduces_gate_decisions_and_comparison(tmp_path, monkeypatch):
    summary = run(monkeypatch, PACKAGE, tmp_path / 'summary.json')
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['qualified'] is False
    assert summary['actual_human_judgments'] == 0 and summary['holdout_used'] is False
    assert summary['defaults_promoted'] is False
    g2 = summary['jobs']['g2']
    assert g2['model_alias'] == 'spark/glm-5.3-flash' and g2['timeout_seconds'] == 600
    assert g2['all_gate_tests_pass'] is False
    assert g2['gate_items'] == {
        'cells': 36, 'cells_complete_and_accounted': 14,
        'cells_complete_and_accounted_first_attempt': 14,
        'aggregate_defect_cells_nonempty_valid_v2': 2,
        'assessed_defect_cells_actual_complete_assessor': 0}
    assert g2['rerun_attempts'] == 0
    assert g2['session_outcomes']['discovery_not_complete_by_reason'] == {
        'timeout': 22, 'provider': 9, 'budget': 3, 'citation_validation': 1}
    g1 = summary['interrupted']['g1']
    assert g1['gate'] == 'not judged' and g1['cells_recorded'] == 6
    assert g1['status_counts'] == {'complete': 5, 'invalid': 1}
    assert set(summary['withdrawn_before_launch']) == {'g2-withdrawn-c8059bb'}
    for name in ('sol-r1', 'sol-r2', 'sol-r3'):
        row = summary['side_by_side'][name]
        assert row['gate_pass_under_this_package_rule'] is True
        assert row['cells_complete_and_accounted_first_attempt'] == 36
    published = json.loads((PACKAGE / 'published-sha256.json').read_text())
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert set(published) == on_disk - {'published-sha256.json'}
    for path in on_disk:
        text = (PACKAGE / path).read_text()
        assert '/home/' not in text and '/tmp/' not in text
        assert 'holdout-v1/' not in text.replace('`evaluations/holdout-v1`', '')


def test_judged_and_interrupted_schedules_share_profile_and_criteria():
    g1 = json.loads((PACKAGE / 'g1/evaluation.json').read_bytes())
    g2 = json.loads((PACKAGE / 'g2/evaluation.json').read_bytes())
    assert (PACKAGE / 'g1/evaluation.json').read_bytes() == (
        PACKAGE / 'g2/evaluation.json').read_bytes()
    assert g1['models']['default']['model'] == g2['models']['default']['model'] == (
        'spark/glm-5.3-flash')
    gates = [json.loads((PACKAGE / f'{job}/gate.json').read_text()) for job in ('g1', 'g2')]
    criteria = ('cases', 'clean_cases', 'arms', 'repeats', 'expected_runs',
                'all_runs_complete_and_accounted',
                'aggregate_defect_case_repeats_nonempty_valid_v2',
                'assessed_defect_case_repeats_actual_complete_assessor', 'max_tokens_per_run',
                'max_turns', 'timeout_seconds', 'concurrency', 'transient_retry', 'pass_rule')
    assert all(gates[0][key] == gates[1][key] for key in criteria)


def test_frozen_files_are_the_ones_posted_on_issue_51():
    reproduce = module()
    for job, posted in reproduce.POSTED_ON_51.items():
        for name, digest in posted.items():
            assert hashlib.sha256((PACKAGE / job / name).read_bytes()).hexdigest() == digest
    assert reproduce.POSTED_ON_51['g2']['gate.json'] == (
        'b3121fa3c2ba56960528e91e53a05830d406e3d1491bb2b3028f71ab159c996c')


def rehash(copy, name):
    hashes = json.loads((copy / 'published-sha256.json').read_text())
    hashes[name] = hashlib.sha256((copy / name).read_bytes()).hexdigest()
    (copy / 'published-sha256.json').write_text(json.dumps(hashes))


def test_reproduction_refuses_regenerated_freeze_or_mechanical_files(tmp_path, monkeypatch):
    for name, old, new in (
            ('g2/preparation.json', '"concurrency": 3', '"concurrency": 4'),
            ('g2/mechanical-qualification.json', '"gate_sha256":"', '"gate_sha256":"0'),
            ('freeze-record.json', '"gate_sha256": "c3301ce7', '"gate_sha256": "d3301ce7')):
        copy = tmp_path / name.replace('/', '-')
        shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
        text = (copy / name).read_text()
        assert old in text, name
        (copy / name).write_text(text.replace(old, new, 1))
        rehash(copy, name)
        with pytest.raises(ValueError, match='posted on #51|not bound|freeze'):
            run(monkeypatch, copy, tmp_path / 'out.json')
    copy = tmp_path / 'extra'
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
    (copy / 'unlisted.json').write_text('{}')
    with pytest.raises(ValueError, match='outside the hash manifest'):
        run(monkeypatch, copy, tmp_path / 'out.json')


def test_reproduction_refuses_changed_evidence(tmp_path, monkeypatch):
    copy = tmp_path / 'package'
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
    target = copy / 'g2/experiment.json'
    target.write_text(target.read_text().replace('"failed"', '"complete"', 1))
    with pytest.raises(ValueError, match='hash mismatch'):
        run(monkeypatch, copy, tmp_path / 'out.json')
    hashes = json.loads((copy / 'published-sha256.json').read_text())
    hashes['g2/experiment.json'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (copy / 'published-sha256.json').write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match='disagrees|differ'):
        run(monkeypatch, copy, tmp_path / 'out.json')
