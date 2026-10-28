"""Bounded whole-pipeline recovery, without changing session validation or grants."""
from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from scrutare.config import parse_config
from scrutare.engine.session_models import TokenUsage
from scrutare.engine.session_output import decode_cost_event

RULE_A = {'rule': 'A', 'max_whole_cell_reruns': 1,
          'inputs': 'identical captured inputs', 'per_attempt_limits': 'unchanged',
          'selection': 'original success, else sole successful rerun',
          'accounting': 'every attempt retained; cumulative usage separate'}


def sum_usage(attempts: list[dict[str, Any]]) -> dict[str, int]:
    return TokenUsage(**{key: sum(a['usage'][key] for a in attempts)
                         for key in ('input', 'output', 'cache_read', 'cache_write')}).to_dict()


def retryable_sessions(directory: Path) -> list[dict[str, str]] | None:
    """Refuse completed invalid answers and hard input/artifact/accounting failures.

    An incomplete ledger caused by a timeout/provider stop remains incomplete until
    independently reconciled. Recovery never changes that flag or erases its spend.
    Unknown terminal records are not permission to spend again.
    """
    failures = []
    try:
        paths = sorted(directory.glob('**/sessions/*/attempt-*/result.json'))
        if not paths:
            return None
        summaries = [p for name in ('panel.json', 'debate.json', 'iterative-round/panel.json')
                     if (p := directory / name).exists()]
        if not summaries:
            return None
        allowed = ('initial', 'correction', 'discovery_incomplete', 'assessment_incomplete',
                   'perspectives', 'chair', 'perspectives_incomplete', 'reassessment_incomplete',
                   'perspective_budget', 'chair_budget')
        for path in summaries:
            summary = json.loads(path.read_bytes())
            if (summary.get('strategy') == 'iterative' and 'reason' not in summary
                    and (directory / 'iterative-round/panel.json').is_file()):
                continue
            if summary.get('reason') not in allowed:
                return None
        if not pipeline_records_valid(directory):
            return None
        config = parse_config((directory / 'config.yaml').read_bytes())
        keys = set()
        for path in paths:
            if path.is_symlink():
                return None
            saved = json.loads(path.read_bytes())
            key = str(path.relative_to(directory).parent)
            if key in keys:
                return None
            keys.add(key)
            TokenUsage(**{k: saved['usage'][k] for k in (
                'input', 'output', 'cache_read', 'cache_write')})
            status, reason = saved['status'], saved['reason']
            if status != 'not_started':
                invocation = json.loads((path.parent / 'invocation.json').read_bytes())
                if invocation['rail'] != config.models.for_persona(saved['persona']).record():
                    return None
                costs = []
                for stream in ('stdout.jsonl', 'correction.stdout.jsonl'):
                    events = path.parent / stream
                    if not events.exists() and stream.startswith('correction'):
                        continue
                    for line in events.read_bytes().splitlines():
                        if line.strip():
                            usage = decode_cost_event(line)
                            if usage is not None:
                                costs.append({'usage': usage.to_dict()})
                if sum_usage(costs) != saved['usage']:
                    return None
            if status == 'not_started':
                continue
            if status == 'complete':
                if saved.get('accounting_complete') is not True:
                    return None
                continue
            # Schema/citation/identity refusals of a returned answer cannot be rerolled.
            if status not in ('failed', 'partial') or reason not in (
                    'output_cap', 'budget', 'timeout', 'provider', 'blocked', 'max_turns'):
                return None
            if reason in ('output_cap', 'budget') and saved.get('accounting_complete') is not True:
                return None
            failures.append({'session': key, 'status': status, 'reason': reason})
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return failures or None



def pipeline_records_valid(directory: Path) -> bool:
    """Every declared participant and outcome has its own agreeing durable terminal."""
    try:
        referenced = set()
        def bound(value: Any) -> bool:
            if isinstance(value, list):
                return all(bound(v) for v in value)
            if not isinstance(value, dict):
                return True
            if 'artifact_directory' in value and 'status' in value:
                path = Path(value['artifact_directory']) / 'result.json'
                if (not path.resolve().is_relative_to(directory.resolve())
                        or not path.is_file() or path.is_symlink()):
                    return False
                saved = json.loads(path.read_bytes())
                if any(saved.get(k) != value.get(k) for k in ('status', 'reason', 'usage')):
                    return False
                referenced.add(path.resolve())
            return all(bound(v) for v in value.values())
        fanout_path = directory / 'fanout.json'
        if not fanout_path.is_file():
            fanout_path = directory / 'iterative-round/fanout.json'
        fanout = json.loads(fanout_path.read_bytes())
        names = [p['name'] for p in fanout['personas']]
        outcomes = fanout['result']['outcomes']
        config = parse_config((directory / 'config.yaml').read_bytes())
        if not config.routing.enabled:
            from scrutare.personas import resolve_personas
            expected = {p.name for p in resolve_personas(config.personas,
                        procedures=config.inspection.procedures)}
            if set(names) != expected:
                return False
        if (not names or len(set(names)) != len(names)
                or {o['persona'] for o in outcomes} != set(names)
                or len(outcomes) != len(names)):
            return False
        summaries = [*directory.glob('*.json'), *directory.glob('iterative-round/*.json')]
        for path in summaries:
            if path.name not in ('artifacts.json', 'evaluation.json', 'review-recovery.json'):
                if path.is_symlink() or not bound(json.loads(path.read_bytes())):
                    return False
        terminals = {p.resolve() for p in directory.glob('**/sessions/*/attempt-*/result.json')}
        return bool(referenced) and referenced == terminals
    except (OSError, ValueError, KeyError, TypeError):
        return False

def attempt_record(record: dict[str, Any], number: int) -> dict[str, Any]:
    return {key: record[key] for key in ('run_dir', 'status', 'error', 'accounting_complete',
                                       'usage', 'latency_seconds')} | {'attempt': number}


def select_attempt(record: dict[str, Any], attempts: list[dict[str, Any]]) -> None:
    """Keep accepted usage distinct from total operation usage, including failures."""
    record.update(attempts=attempts, attempt=len(attempts),
                  selected_attempt=len(attempts) if record['status'] == 'complete' else None,
                  cumulative_usage=sum_usage(attempts),
                  retry_usage=sum_usage(attempts[:-1]))
    if record['status'] != 'complete':
        record['findings'] = []


def capture_bytes(directory: Path) -> dict[str, bytes]:
    """Freeze the fresh ingestion tree, before any model or posting artifacts exist."""
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('unsafe recovery snapshot')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = path.read_bytes()
    return result


def verify_capture(directory: Path, frozen: dict[str, bytes]) -> None:
    for name, data in frozen.items():
        path = directory / name
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError('recovery inputs changed')


def restore_capture(directory: Path, frozen: dict[str, bytes]) -> None:
    directory.mkdir(mode=0o700)
    for name, data in frozen.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)


# Iterative history is another input. A scoped replay restores the pre-attempt
# state in memory, while refusing any intervening writer to its durable journal.
# The ordinary path has no snapshot and keeps its existing history semantics.

_HISTORY: ContextVar[dict[Path, dict[str, Any]] | None] = ContextVar('review_recovery_history',
                                                               default=None)


@contextmanager
def whole_review_history() -> Iterator[None]:
    token = _HISTORY.set({})
    try:
        yield
    finally:
        _HISTORY.reset(token)


def frozen_history(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    snapshot = _HISTORY.get()
    if snapshot is None:
        return state
    raw = path.read_bytes() if path.exists() else None
    if path not in snapshot:
        snapshot[path] = {'initial': copy.deepcopy(state), 'latest': raw}
    elif snapshot[path]['latest'] != raw:
        from scrutare.engine.review_inputs import ReviewInputError
        raise ReviewInputError('Iterative history changed between whole-review attempts.')
    return copy.deepcopy(snapshot[path]['initial'])


def remember_history(path: Path) -> None:
    snapshot = _HISTORY.get()
    if snapshot is not None and path in snapshot:
        snapshot[path]['latest'] = path.read_bytes()


def record_history_input(run: Path, state: dict[str, Any]) -> None:
    if _HISTORY.get() is None:
        return
    from scrutare.engine.session_artifacts import write_owned_json
    path = run / 'review-history-input.json'
    document = {'schema_version': 1, 'state': state}
    if path.exists():
        if path.is_symlink() or json.loads(path.read_bytes()) != document:
            raise ValueError('frozen iterative history input changed')
    else:
        write_owned_json(path, document, prepared_root=run / 'review-inputs')
