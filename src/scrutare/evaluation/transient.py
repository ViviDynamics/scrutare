"""Classify one retained attempt as an upstream HTTP 5xx interruption, or not at all."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scrutare.evaluation.corpus import parse_json

_HTTP_5XX = re.compile(r'RuntimeError: chat completion failed with HTTP (5[0-9]{2}):')


def _provider_status(stdout: Path) -> int | None:
    """Read the native terminal result; only its own HTTP 5xx report qualifies."""
    if stdout.is_symlink() or not stdout.is_file():
        return None
    terminal: Any = None
    for line in stdout.read_bytes().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict) and event.get('type') == 'result':
            terminal = event
    if not isinstance(terminal, dict) or terminal.get('status') != 'error':
        return None
    error = terminal.get('error')
    match = _HTTP_5XX.match(error) if isinstance(error, str) else None
    return int(match.group(1)) if match else None


def transient_provider_failures(directory: Path) -> list[dict[str, Any]] | None:
    """Return every HTTP 5xx session failure, or None when anything else went wrong.

    Complete and never-started sessions are compatible with an upstream
    interruption. Budget, protocol, citation, timeout, 4xx and unreadable
    evidence are not, and make the whole attempt ineligible for a rerun.
    """
    failures = []
    try:
        for result in sorted(directory.glob('sessions/*/attempt-*/result.json')):
            if result.is_symlink() or not result.is_file():
                return None
            saved = parse_json(result.read_bytes())
            status, key = saved['status'], saved['session_key']
            if status in ('complete', 'not_started'):
                continue
            if status != 'failed' or saved['reason'] != 'provider' or not isinstance(key, str):
                return None
            code = _provider_status(result.parent / 'stdout.jsonl')
            if code is None:
                return None
            failures.append({'session_key': key, 'http_status': code})
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return failures or None
