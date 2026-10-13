"""The published qualification package reproduces from its own public files."""
import importlib.util
import json
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / 'evaluations/reports/2026-10-13-qualification-rerun'


def module():
    spec = importlib.util.spec_from_file_location('qualification_rerun', PACKAGE / 'reproduce.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_public_package_reproduces_hashes_gates_decisions_and_summary(tmp_path, monkeypatch):
    reproduce = module()
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(PACKAGE),
                                     '--output', str(tmp_path / 'summary.json')])
    reproduce.main()
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['actual_human_judgments'] == 0 and summary['holdout_used'] is False
    assert summary['defaults_promoted'] is False
    assert summary['jobs']['qualification']['all_gate_tests_pass'] is True
    assert summary['jobs']['qualification']['status_counts'] == {'complete': 36}
    assert summary['jobs']['qualification']['rerun_attempts'] == 0
    assert summary['jobs']['routing']['status_counts'] == {'complete': 22, 'failed': 2}
    assert summary['jobs']['routing']['arms']['routing']['cells_with_specialist'] == 6
    assert sum(arm['discussion_completed_rounds'] for job in summary['jobs'].values()
               for arm in job['arms'].values()) == 0
    assert summary['spend']['total_nominal_usd'] <= summary['spend']['limit_usd'] == 75.0
    published = json.loads((PACKAGE / 'published-sha256.json').read_text())
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert set(published) == on_disk - {'published-sha256.json'}
    for path in on_disk:
        text = (PACKAGE / path).read_text()
        assert '/home/' not in text and 'holdout-v1/' not in text.replace(
            '`evaluations/holdout-v1`', '')


def test_reproduction_refuses_changed_evidence(tmp_path, monkeypatch):
    import shutil
    copy = tmp_path / 'package'
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
    reproduce = module()
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(copy),
                                     '--output', str(tmp_path / 'out.json')])
    target = copy / 'routing/experiment.json'
    target.write_text(target.read_text().replace('"failed"', '"complete"', 1))
    with pytest.raises(ValueError, match='hash mismatch'):
        reproduce.main()
    import hashlib
    hashes = json.loads((copy / 'published-sha256.json').read_text())
    hashes['routing/experiment.json'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (copy / 'published-sha256.json').write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match='disagrees|differ'):
        reproduce.main()


def test_unmatched_finding_stays_pending_never_false(tmp_path):
    reproduce = module()
    run = {'run_id': 'dev2-01/evidence/1', 'case_id': 'dev2-01', 'findings': [
        {'file': 'src/concurrency/counter.py', 'side': 'RIGHT', 'line': 11,
         'problem': 'Unclear naming.'},
        {'file': 'src/other.py', 'side': 'RIGHT', 'line': 11,
         'problem': 'Computing outside the lock loses increments.'},
        {'file': 'src/concurrency/counter.py', 'side': 'RIGHT', 'line': 3,
         'problem': 'Computing the count outside the lock loses concurrent increments.'},
        {'file': 'src/concurrency/counter.py', 'side': 'RIGHT', 'line': 11,
         'problem': 'Computing the count outside the lock loses concurrent increments.'}]}
    assert [reproduce.decide(run, i) for i in range(4)] == [
        None, None, None, 'dev2-01-defect']
    assert reproduce.decide({**run, 'case_id': 'dev2-07'}, 3) is None
    summary = json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['pending_human_findings'] == 0 and summary['automated_decisions'] == 190
    assert 'precision' not in json.dumps(summary)
