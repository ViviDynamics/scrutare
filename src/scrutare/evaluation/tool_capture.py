"""Explicit Ruff evidence generation from declared original development sources.

The production review adapter only consumes these files. This opt-in utility uses
Ruff's built-in parser on copied bytes; it never runs repository commands, imports
source code, loads project configuration or discovers third-party plugins.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from scrutare.evaluation.corpus import load_corpus, read_json, source_contents
from scrutare.evaluation.runner import write_json


def capture_development_ruff(corpus: Path, output: Path, *, executable: Path) -> dict[str, Any]:
    """Capture exactly Ruff 0.16.10; no provider, GitHub or review invocation."""
    if any(path.is_symlink() for path in (corpus, *corpus.parents)):
        raise ValueError('tool capture corpus must be unlinked')
    manifest = read_json(corpus / 'manifest.json')
    for entry in manifest['cases']:
        if entry['split'] != 'development':
            continue
        provenance = entry.get('provenance', {})
        if (provenance.get('origin') != 'original'
                or provenance.get('license') != 'Elastic-2.0'
                or provenance.get('external_material', False) is not False):
            raise ValueError('tool capture requires original licensed development sources')
    cases = load_corpus(corpus, 'development')
    sources = {}
    revisions = {}
    for case in cases:
        head = next((source for source in case.context_sources if source.side == 'head'), None)
        if head is None:
            raise ValueError('tool capture requires declared head source')
        sources[case.id] = source_contents(head.directory)
        revisions[case.id] = head.revision
    if not cases:
        raise ValueError('tool capture requires development cases')
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('tool capture output must be unlinked')
    executable = executable.absolute()
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    version = subprocess.run([str(executable), '--version'], capture_output=True,
                             check=True, timeout=30, env=environment)
    if version.stdout.strip() != b'ruff 0.16.10':
        raise ValueError('tool capture requires Ruff 0.16.10')
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    index: dict[str, Any] = {'schema_version': 1, 'tool': {'name': 'ruff', 'version': '0.16.10'},
        'split': 'development', 'execution': 'isolated_configuration_builtin_parser',
        'network_isolation': 'not_enforced', 'cases': {}}
    for case in cases:
        contents = sources[case.id]
        selected = sorted(path for path in contents if path.endswith(('.py', '.pyi')))
        with tempfile.TemporaryDirectory(prefix='scrutare-ruff-capture-') as temporary:
            root = Path(temporary)
            for path, data in contents.items():
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            command = [str(executable), 'check', '--isolated', '--no-cache',
                       '--select', 'E4,E7,E9,F', '--output-format', 'json', '--',
                       *(str(root / path) for path in selected)]
            started = time.monotonic()
            result = subprocess.run(command, capture_output=True, timeout=60, env=environment,
                                    cwd=temporary) if selected else None
            duration = time.monotonic() - started
            diagnostics = (json.loads(result.stdout)
                           if result and result.returncode in (0, 1) else [])
            status = 'unavailable' if result is None else ('complete' if result.returncode in (0, 1)
                                                          else 'failed')
            capture = {'schema_version': 1, 'revision': revisions[case.id], 'tool': index['tool'],
                'status': status, 'duration_seconds': duration, 'source_root': str(root),
                'diagnostics': diagnostics}
            filename = case.id + '.json'
            write_json(output / filename, capture)
            index['cases'][case.id] = {
                'capture': filename, 'capture_sha256': hashlib.sha256(
                    (output / filename).read_bytes()).hexdigest(),
                'source_sha256': {path: hashlib.sha256(data).hexdigest()
                                  for path, data in sorted(contents.items())},
                'inspected_paths': selected, 'status': status,
                'exit_code': result.returncode if result else None,
                'stdout_sha256': hashlib.sha256(result.stdout).hexdigest() if result else None,
                'stderr_sha256': hashlib.sha256(result.stderr).hexdigest() if result else None}
    write_json(output / 'index.json', index)
    os.chmod(output / 'index.json', 0o600)
    return index
