"""The published tools/reliability package reproduces from its own public files."""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / 'evaluations/reports/2026-10-13-tools-reliability-separating'


def module():
    spec = importlib.util.spec_from_file_location('tools_reliability', PACKAGE / 'reproduce.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(monkeypatch, package, output):
    reproduce = module()
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(package),
                                     '--output', str(output)])
    reproduce.main()
    return json.loads(output.read_text())


def test_public_package_reproduces_hashes_gates_decisions_and_summary(tmp_path, monkeypatch):
    summary = run(monkeypatch, PACKAGE, tmp_path / 'summary.json')
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['actual_human_judgments'] == 0 and summary['holdout_used'] is False
    assert summary['defaults_promoted'] is False and summary['frozen_rules_amended'] is False
    reliability = summary['reliability']
    assert (reliability['schedules_passed'], reliability['schedules_run']) == (3, 4)
    assert (reliability['cells_complete_on_first_attempt'], reliability['cells']) == (138, 144)
    assert summary['discussion'] == {'attempted_rounds': 3, 'completed_rounds': 0,
                                     'focused_cells': 90}
    assert summary['spend']['measured_usd'] <= summary['spend']['limit_usd'] == 60.0
    assert summary['jobs']['tools-voided']['status_counts'] == {'complete': 11, 'invalid': 13}
    voided = summary['assessor_reservation']['tools-voided']
    assert sum(arm['cells_voided_by_assessor_reservation'] for arm in voided.values()) == 12
    # dev3-12: the over-broad frozen filter flags all assessed findings; on reading they
    # state two other claims (see README). The assessor supported every one.
    for arm in ('assessed', 'focused1', 'focused3'):
        row = summary['jobs']['focused-uncaptured']['arms'][arm]['per_case']['dev3-12']
        assert row['initial_dispositions'] == {'supported': 3}
        assert row['findings_stating_tempting_allegation'] == 3
    assert summary['citation_refusals']['paired']['on']['sessions_refused_for_citation'] == 0
    assert 'precision' not in json.dumps(summary)
    published = json.loads((PACKAGE / 'published-sha256.json').read_text())
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert set(published) == on_disk - {'published-sha256.json'}
    for path in on_disk:
        text = (PACKAGE / path).read_text()
        assert '/home/' not in text and '/tmp/' not in text
        assert 'holdout-v1/' not in text.replace('`evaluations/holdout-v1`', '')


def test_rules_are_the_frozen_ones():
    reproduce = module()
    frozen = json.loads((PACKAGE / 'frozen-rules.json').read_text())
    assert reproduce.rules_sha256() == frozen['rules_sha256'] == (
        'f722d27708cb20461ad6074f2c1ae4a267321c28dce3c69c35ac44d382b4e683')
    assert frozen['frozen_at_utc'] == '2026-10-14T00:22:02+00:00'


def test_reproduction_refuses_changed_evidence_or_rules(tmp_path, monkeypatch):
    copy = tmp_path / 'package'
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
    target = copy / 'tools/experiment.json'
    target.write_text(target.read_text().replace('"complete"', '"failed"', 1))
    with pytest.raises(ValueError, match='hash mismatch'):
        run(monkeypatch, copy, tmp_path / 'out.json')
    hashes = json.loads((copy / 'published-sha256.json').read_text())
    hashes['tools/experiment.json'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (copy / 'published-sha256.json').write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match='disagrees|differ'):
        run(monkeypatch, copy, tmp_path / 'out.json')
    shutil.rmtree(copy)
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
    script = copy / 'reproduce.py'
    script.write_text(script.read_text().replace("r'deadlock', ", "", 1))
    hashes = json.loads((copy / 'published-sha256.json').read_text())
    hashes['reproduce.py'] = hashlib.sha256(script.read_bytes()).hexdigest()
    (copy / 'published-sha256.json').write_text(json.dumps(hashes))
    spec = importlib.util.spec_from_file_location('changed', script)
    changed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(changed)
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(copy),
                                     '--output', str(tmp_path / 'out.json')])
    with pytest.raises(ValueError, match='frozen'):
        changed.main()


def test_unmatched_finding_stays_pending_never_false():
    reproduce = module()
    finding = {'file': 'src/concurrency/account_locks.py', 'side': 'RIGHT', 'line': 9,
               'problem': 'Opposite transfers can deadlock.', 'reason': ''}
    run_row = {'run_id': 'dev3-09/evidence/1', 'case_id': 'dev3-09', 'findings': [
        finding, {**finding, 'line': 2}, {**finding, 'problem': 'Naming is unclear.'}]}
    assert [reproduce.decide(run_row, i) for i in range(3)] == ['dev3-09-defect', None, None]
    assert reproduce.decide({**run_row, 'case_id': 'dev3-06'}, 0) is None
