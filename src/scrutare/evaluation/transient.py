"""Classify one retained attempt as an upstream HTTP 5xx interruption, or not at all."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Literal

from scrutare.evaluation.corpus import parse_json

# Server-side unavailability only; 501 and 505 describe requests that would fail again.
# A retained 500 is assumed to be unavailability unless it is a known model failure below.
_UNAVAILABLE = (500, 502, 503, 504, 529)
# The request failed before any response: nare's HTTP status report.
_HTTP_STATUS = re.compile(r'RuntimeError: chat completion failed with HTTP ([0-9]{3}):')
# The response stream ended in an error frame (the streaming OpenAI rail, which Scrutare
# uses from nare 2026.10.4): nare reports the provider's error object as its Python repr.
# A frame without a numeric code (OpenAI's own server_error has none) has no retained status.
_MID_STREAM = re.compile(r'RuntimeError: stream provider error: (\{.*\})', re.DOTALL)
# LiteLLM aborts a stream whose model keeps emitting the same chunk, and reports it as an
# HTTP 500. That is the model looping on its own output, not the server being unavailable.
_REPETITION = 'The model is repeating the same chunk'
# Review-level stops that a session interruption explains. Changed inputs or evidence do not.
_INTERRUPTIBLE = ('initial', 'correction', 'discovery_incomplete', 'assessment_incomplete',
                  'perspectives', 'chair', 'perspectives_incomplete', 'reassessment_incomplete')

ProviderFailure = Literal['unavailable', 'model_repetition']


def _mid_stream_status(error: str) -> tuple[bool, int | None]:
    """Whether this is nare's mid-stream provider error object, and its numeric code."""
    match = _MID_STREAM.fullmatch(error)
    if match is None:
        return False, None
    try:
        frame = ast.literal_eval(match.group(1))
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None
    if not isinstance(frame, dict):
        return False, None
    code = frame.get('code')
    if isinstance(code, str) and code.isascii() and code.isdigit():
        code = int(code)
    return True, code if type(code) is int else None


def classify_provider_error(error: object) -> tuple[ProviderFailure, int | None] | None:
    """Classify nare's terminal provider error report, in either form, or return None.

    LiteLLM's repetition abort is ``model_repetition`` whatever status it carries: a model
    failure, never an interruption. Otherwise a retained 500, 502, 503, 504 or 529 is
    ``unavailable`` (the upstream could not serve the request); anything else is None.
    """
    if not isinstance(error, str):
        return None
    match = _HTTP_STATUS.match(error)
    reported, code = (True, int(match.group(1))) if match else _mid_stream_status(error)
    if not reported:
        return None
    if _REPETITION in error:
        return 'model_repetition', code
    return ('unavailable', code) if code in _UNAVAILABLE else None


def _provider_failure(stdout: Path) -> tuple[ProviderFailure, int | None] | None:
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
    return classify_provider_error(terminal.get('error'))


def _sessions(directory: Path) -> list[tuple[dict[str, Any], Path]]:
    results = []
    for result in sorted(directory.glob('sessions/*/attempt-*/result.json')):
        if result.is_symlink() or not result.is_file():
            raise ValueError('unreadable session result')
        results.append((parse_json(result.read_bytes()), result.parent))
    return results


def transient_provider_failures(directory: Path) -> list[dict[str, Any]] | None:
    """Return every HTTP 5xx session failure, or None when anything else went wrong.

    Complete and never-started sessions are compatible with an upstream
    interruption. Budget, protocol, citation, timeout, 4xx, a model repetition abort and
    unreadable evidence are not, and make the whole attempt ineligible for a rerun, as
    does a review-level stop for changed inputs or evidence.
    """
    failures = []
    try:
        for name in ('panel.json', 'debate.json'):
            summary = directory / name
            if summary.is_symlink():
                return None
            if summary.is_file() and parse_json(summary.read_bytes())[
                    'reason'] not in _INTERRUPTIBLE:
                return None
        for saved, attempt in _sessions(directory):
            status, key = saved['status'], saved['session_key']
            if status in ('complete', 'not_started'):
                continue
            if status != 'failed' or saved['reason'] != 'provider' or not isinstance(key, str):
                return None
            failure = _provider_failure(attempt / 'stdout.jsonl')
            if failure is None or failure[0] != 'unavailable':
                return None
            failures.append({'session_key': key, 'http_status': failure[1]})
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None
    return failures or None


def model_repetition_failures(directory: Path) -> list[dict[str, Any]]:
    """Return every provider failure that was LiteLLM's repetition abort, for the record."""
    failures = []
    try:
        for saved, attempt in _sessions(directory):
            key = saved['session_key']
            if (saved['status'] != 'failed' or saved['reason'] != 'provider'
                    or not isinstance(key, str)):
                continue
            failure = _provider_failure(attempt / 'stdout.jsonl')
            if failure is not None and failure[0] == 'model_repetition':
                failures.append({'session_key': key, 'http_status': failure[1]})
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return []
    return failures
