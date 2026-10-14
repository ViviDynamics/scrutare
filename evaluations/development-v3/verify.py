"""Independently check the checked-in development-v3 fixtures.

This verifier executes only this repository's original trusted fixture and control
code. It must never be pointed at an untrusted pull request or external corpus.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SEALED = ('README.md', 'build.py', 'verify.py', 'manifest.json', 'related-paths.json',
          'captures', 'controls', 'labels', 'sources')
GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Scrutare Fixtures', 'GIT_AUTHOR_EMAIL': 'fixtures@scrutare.invalid',
    'GIT_COMMITTER_NAME': 'Scrutare Fixtures', 'GIT_COMMITTER_EMAIL': 'fixtures@scrutare.invalid',
    'GIT_AUTHOR_DATE': '2026-10-13T00:00:00+00:00',
    'GIT_COMMITTER_DATE': '2026-10-13T00:00:00+00:00',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
}


def inventory(root: Path) -> dict[str, Any]:
    """Hash every sealed byte; generated caches and the two derived reports are excluded."""
    files = {}
    for name in SEALED:
        path = root / name
        members = [path] if path.is_file() else sorted(path.rglob('*'))
        for member in members:
            if member.is_symlink():
                raise ValueError('Sealed fixtures cannot contain symlinks.')
            if member.is_file() and '__pycache__' not in member.parts:
                files[member.relative_to(root).as_posix()] = hashlib.sha256(
                    member.read_bytes()).hexdigest()
    canonical = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
    return {'schema_version': 1, 'files': files,
            'inventory_sha256': hashlib.sha256(canonical).hexdigest()}


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(['git', *arguments], cwd=directory, check=True, capture_output=True,
                          text=True, env={'PATH': os.environ['PATH'], **GIT_ENV}).stdout


def _tree(directory: Path) -> dict[str, bytes]:
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob('*'))
            if path.is_file() and '.git' not in path.relative_to(directory).parts}


def _control(script: Path, tree: Path, work: Path) -> bool:
    """Run one trusted control against a private copy; exit status is the only signal."""
    target = work / 'tree'
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(tree, target)
    result = subprocess.run(
        [sys.executable, '-I', '-B', str(script)], cwd=target, capture_output=True, timeout=60,
        env={'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
    return result.returncode == 0


def verify(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != ROOT:
        raise ValueError('Only checked-in original development-v3 fixtures may be executed.')
    failures: list[str] = []
    frozen = root / 'freeze-sha256.json'
    if not frozen.is_file() or json.loads(frozen.read_text()) != inventory(root):
        raise ValueError('Fixture bytes differ from the sealed inventory; refusing to execute.')
    manifest = json.loads((root / 'manifest.json').read_text())
    related = set(json.loads((root / 'related-paths.json').read_text()))
    report: dict[str, Any] = {
        'schema_version': 1, 'cases': len(manifest['cases']), 'patches_verified': 0,
        'commit_pairs_reconstructed': 0, 'defects_verified': 0, 'clean_controls_verified': 0,
        'control_executions': 0, 'tempting_allegation_cases': 0,
        'clean_labels_requiring_context': 0, 'evidence_outside_capture_cases': 0,
        'routing_classes': {'covered': 0, 'missed': 0, 'none': 0}}
    with tempfile.TemporaryDirectory(prefix='scrutare-dev3-verify-') as temporary:
        work = Path(temporary)
        for entry in manifest['cases']:
            identifier = entry['id']
            base, head = (root / entry['context_sources'][side]['directory']
                          for side in ('base', 'head'))
            capture = root / entry['capture']
            gold = json.loads((root / entry['labels']).read_text())
            metadata = json.loads((capture / 'metadata.json').read_text())
            files = json.loads((capture / 'files.json').read_text())
            for tree in (base, head):
                for path, data in _tree(tree).items():
                    if path.endswith('.py'):
                        compile(data, path, 'exec')
            repo = work / f'{identifier}-repo'
            repo.mkdir()
            _git(repo, 'init', '--quiet', '--initial-branch=main')
            revisions = []
            for message, tree in (('base', base), ('head', head)):
                for child in repo.iterdir():
                    if child.name != '.git':
                        shutil.rmtree(child) if child.is_dir() else child.unlink()
                shutil.copytree(tree, repo, dirs_exist_ok=True)
                # Copied files keep their timestamps; drop the stat cache so Git rehashes.
                (repo / '.git' / 'index').unlink(missing_ok=True)
                _git(repo, 'add', '--all')
                _git(repo, '-c', 'commit.gpgsign=false', 'commit', '--quiet', '--message',
                     message)
                revisions.append(_git(repo, 'rev-parse', 'HEAD').strip())
            patch = _git(repo, '-c', 'core.quotepath=false', 'diff', '--no-color',
                         '--no-ext-diff', '--full-index', '--no-renames', *revisions)
            declared = [entry['context_sources'][side]['revision'] for side in ('base', 'head')]
            captured = [metadata['pull_request'][side]['sha'] for side in ('base', 'head')]
            if revisions == declared == captured and metadata['head_sha'] == revisions[1]:
                report['commit_pairs_reconstructed'] += 1
            else:
                failures.append(f'{identifier}: commit identity')
            applied = work / f'{identifier}-applied'
            shutil.copytree(base, applied)
            subprocess.run(['git', 'apply', '--no-index', str(capture / 'diff.patch')],
                           cwd=applied, check=True, capture_output=True,
                           env={'PATH': os.environ['PATH'], **GIT_ENV,
                                'GIT_CEILING_DIRECTORIES': str(work)})
            changed = sorted(path for path, data in _tree(head).items()
                             if _tree(base).get(path) != data)
            if (patch == (capture / 'diff.patch').read_text() and _tree(applied) == _tree(head)
                    and [f['filename'] for f in files] == changed
                    and metadata['pull_request']['changed_files'] == len(changed)
                    and set(_tree(base)) == set(_tree(head))
                    and all(path not in changed and (base / path).is_file()
                            for path in (*gold['unchanged_supporting_paths'],
                                         *gold['evidence_outside_capture']))
                    and not set(gold['evidence_outside_capture']) & related):
                report['patches_verified'] += 1
            else:
                failures.append(f'{identifier}: patch')
            script = root / 'controls' / f'{identifier}.py'
            base_ok, head_ok = _control(script, base, work), _control(script, head, work)
            report['control_executions'] += 2
            if gold['defects']:
                if base_ok and not head_ok and len(gold['defects']) == 1:
                    report['defects_verified'] += 1
                else:
                    failures.append(f'{identifier}: defect control')
            elif base_ok and head_ok:
                report['clean_controls_verified'] += 1
            else:
                failures.append(f'{identifier}: clean control')
            report['tempting_allegation_cases'] += bool(gold['tempting_allegations'])
            report['clean_labels_requiring_context'] += gold['clean_label_requires_context']
            report['evidence_outside_capture_cases'] += bool(gold['evidence_outside_capture'])
            report['routing_classes'][gold['routing_expectation']['class']] += 1
    report['failures'] = failures
    return report


if __name__ == '__main__':
    result = verify(ROOT)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(1 if result['failures'] else 0)
