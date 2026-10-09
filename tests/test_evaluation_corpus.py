"""Pilot fixtures are coherent captures with labels outside reviewer evidence."""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'evaluations' / 'pilot'


def test_original_pilot_has_balanced_separate_ground_truth():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    cases = manifest['cases']
    assert len(cases) == 30
    assert len({case['id'] for case in cases}) == 30
    defects = []
    for case in cases:
        capture = ROOT / case['capture']
        label = ROOT / case['labels']
        assert set(p.name for p in capture.iterdir()) == {
            'diff.patch', 'files.json', 'metadata.json'
        }
        assert capture not in label.parents
        data = json.loads(label.read_text())
        assert data['case_id'] == case['id']
        assert data['counterexamples']
        for defect in data['defects']:
            assert all(defect.get(k) for k in ('id', 'trigger', 'impact', 'evidence',
                                              'match_criteria', 'fix_or_counterexample'))
        defects.append((case['split'], bool(data['defects'])))
        assert case['provenance']['origin'] == 'original'
        assert case['provenance']['license'] == 'Elastic-2.0'
    assert sum(present for _, present in defects) == 20
    holdout = [present for split, present in defects if split == 'holdout']
    assert len(holdout) == 10 and sum(holdout) == 7
    assert len({case['domain'] for case in cases}) == 5


def test_capture_patches_reproduce_real_head_sources(tmp_path):
    cases = json.loads((ROOT / 'manifest.json').read_text())['cases']
    cross_file = 0
    for case in cases:
        source = ROOT / 'sources' / case['id']
        destination = tmp_path / case['id']
        shutil.copytree(source / 'base', destination)
        patch = ROOT / case['capture'] / 'diff.patch'
        subprocess.run(['git', 'apply', '--no-index', str(patch)], cwd=destination, check=True,
                       capture_output=True)
        expected = {p.relative_to(source / 'head'): p.read_bytes()
                    for p in (source / 'head').rglob('*') if p.is_file()}
        actual = {p.relative_to(destination): p.read_bytes()
                  for p in destination.rglob('*') if p.is_file()}
        assert actual == expected
        files = json.loads((patch.parent / 'files.json').read_text())
        metadata = json.loads((patch.parent / 'metadata.json').read_text())
        assert metadata['pull_request']['changed_files'] == len(files)
        cross_file += len(expected) > 1
        for path, content in expected.items():
            if path.suffix == '.py':
                compile(content, str(path), 'exec')
    assert cross_file >= 5
