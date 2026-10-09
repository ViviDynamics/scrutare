"""Explicit non-posting experiments through the production nare engine."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Literal, cast

from scrutare import __version__
from scrutare.config import BudgetSettings, InspectionSettings, ReviewConfig
from scrutare.engine.nare_session import inspect_nare_runtime
from scrutare.engine.repository_context import capture_repository_context, encoded
from scrutare.engine.repository_snapshot import RepositorySnapshot
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.engine.static_analysis import capture_static_analysis, read_capture
from scrutare.engine.strategy import run_review
from scrutare.evaluation.corpus import Case, parse_json, read_json, source_contents
from scrutare.personas import load_persona
from scrutare.provenance import write_artifact_manifest


def write_json(path: Path, data: Any) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def comparison_configs(config: ReviewConfig, *, comparison_set: str = 'baseline'
                       ) -> dict[str, ReviewConfig]:
    """Freeze current roster, shared rail and total cap; reserve a fifth debate chair quota."""
    if comparison_set not in ('baseline', 'procedures'):
        raise ValueError('unknown comparison set')
    if config.models.overrides:
        raise ValueError('comparison requires a single fixed model rail without overrides')
    total = config.budgets.review_max_tokens
    if total < 5:
        raise ValueError('comparison total ceiling must support five participants')
    roster = ('senior-dev', 'junior-dev', 'security', 'devops')
    if comparison_set == 'procedures':
        return {name: replace(config, strategy='panel', personas=names,
                    inspection=InspectionSettings(cast(Literal['baseline', 'v1'], profile)),
                    budgets=BudgetSettings(total // len(names), total))
                for name, names, profile in (
                    ('current4', roster, 'baseline'), ('revised4', roster, 'v1'),
                    ('revised5', (*roster, 'testing-verification'), 'v1'))}
    return {
        'senior': replace(config, strategy='panel', personas=('senior-dev',),
                          budgets=BudgetSettings(total, total)),
        'panel': replace(config, strategy='panel', personas=roster,
                         budgets=BudgetSettings(total // 4, total)),
        'debate': replace(config, strategy='debate', personas=roster,
                          budgets=BudgetSettings(total // 5, total)),
    }


def _observed_usage(directory: Path) -> TokenUsage:
    """Recover disjoint session counters or a later aggregate, never sum both.

    Interrupted sessions write their own outcomes before propagating cancellation;
    a panel aggregate may therefore be absent or lag those durable outcomes.
    Missing or invalid evidence remains unknown, so this is always a lower bound.
    """
    fields = ('input', 'output', 'cache_read', 'cache_write')
    session_totals = dict.fromkeys(fields, 0)
    seen = set()
    for path in sorted(directory.glob('sessions/*/attempt-*/result.json')):
        try:
            saved = read_json(path)
            usage = TokenUsage(**{key: saved['usage'][key] for key in fields})
            session_key = saved['session_key']
            if session_key in seen:
                continue
            seen.add(session_key)
            for key in fields:
                session_totals[key] += getattr(usage, key)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    recovered = session_totals.copy()
    for name in ('panel.json', 'debate.json'):
        try:
            saved = read_json(directory / name)
            usage = TokenUsage(**{key: saved['ledger']['usage'][key] for key in fields})
            for key in fields:
                recovered[key] = max(recovered[key], getattr(usage, key))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return TokenUsage(**recovered)


async def run_experiment(cases: tuple[Case, ...], config: ReviewConfig, output: Path, *,
                         runtime: NareRuntime, evidence_kind: str = 'model', concurrency: int = 1,
                         comparison_set: str = 'baseline'
                         ) -> dict[str, Any]:
    """Fresh 3-repeat job; ordinary CI calls this only with explicitly offline evidence."""
    if (evidence_kind not in ('model', 'offline') or not cases
            or type(concurrency) is not int or not 1 <= concurrency <= 32):
        raise ValueError('nonempty cases and model/offline evidence kind required')
    variants = comparison_configs(config, comparison_set=comparison_set)
    # Freeze before the first await; snapshot hashes and every repeat use these bytes.
    captures = {case.id: {name: (case.capture / name).read_bytes()
                         for name in ('diff.patch', 'files.json', 'metadata.json')}
                for case in cases}
    analysis_captures = {case.id: read_capture(case.analysis_capture)
                         if case.analysis_capture is not None else None
                         for case in cases} if config.analysis.enabled else {}
    context_clients: dict[str, RepositorySnapshot] = {}
    context_sources: dict[str, dict[str, Any]] = {}
    derived_metadata: dict[str, bytes] = {}
    if config.context.enabled:
        for case in cases:
            if not case.context_sources:
                raise ValueError('context evaluation requires explicit corpus source declarations')
            metadata = parse_json(captures[case.id]['metadata.json'])
            snapshots: dict[tuple[str, str], dict[str, bytes]] = {}
            transformations = []
            for side in case.context_sources:
                key = side.repository, side.revision
                contents = source_contents(side.directory)
                if key in snapshots and snapshots[key] != contents:
                    raise ValueError('corpus snapshot identity binds inconsistent source bytes')
                snapshots[key] = contents
                captured = metadata['pull_request'][side.side]
                if captured['sha'] != side.revision:
                    raise ValueError('corpus source revision changed after loading')
                identity = captured.get('repo')
                if (side.side == 'base' and not isinstance(identity, dict)
                        or identity is not None and not isinstance(identity, dict)):
                    raise ValueError('corpus source captured identity is missing or malformed')
                if identity is not None and identity.get('full_name') != side.repository:
                    raise ValueError('corpus source identity changed after loading')
                if identity is None:
                    captured['repo'] = {'full_name': side.repository}
                    transformations.append({'side': side.side, 'field': 'repo',
                                            'original': None, 'declared': captured['repo']})
            client = RepositorySnapshot(snapshots)
            context_clients[case.id] = client
            context_sources[case.id] = {**client.provenance(),
                'metadata_transformations': transformations,
                'original_metadata_sha256': hashlib.sha256(
                    captures[case.id]['metadata.json']).hexdigest()}
            derived_metadata[case.id] = encoded(metadata)
    label_hashes = {case.id: hashlib.sha256(case.labels.read_bytes()).hexdigest()
                    for case in cases}
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    snapshot: dict[str, Any] = {
        'schema_version': 1, 'scrutare_version': __version__, 'evidence_kind': evidence_kind,
        'nare_version': None, 'nare_contract': None,
        'runtime': {**asdict(runtime), 'executable': str(runtime.executable)},
        'repeats': 3, 'concurrency': concurrency,
        'expected_runs': [{'run_id': f'{case.id}/{variant}/{repeat}', 'case_id': case.id,
                           'variant': variant, 'repeat': repeat}
                          for case in cases for variant in variants for repeat in range(1, 4)],
        'configs': {name: value.to_dict() for name, value in variants.items()},
        'comparison_set': comparison_set,
        'variant_personas': {variant: {name: {
                'profile': value.inspection.procedures,
                'system_prompt': load_persona(
                    name, procedures=value.inspection.procedures).system_prompt,
                'system_prompt_sha256': hashlib.sha256(load_persona(
                    name, procedures=value.inspection.procedures
                    ).system_prompt.encode()).hexdigest()}
                for name in value.personas if isinstance(name, str)}
                for variant, value in variants.items()},
        'personas': {name: load_persona(name).system_prompt for name in
                    variants['current4' if comparison_set == 'procedures' else 'panel'].personas
                     if isinstance(name, str)},
        'cases': [{'id': case.id, 'split': case.split, 'domain': case.domain,
                   'capture_sha256': {name: hashlib.sha256(data).hexdigest()
                                      for name, data in captures[case.id].items()},
                   'labels_sha256': label_hashes[case.id],
                   **({'analysis_source_sha256': hashlib.sha256(
                       cast(bytes, analysis_captures[case.id]))
                       .hexdigest() if analysis_captures[case.id] is not None else None}
                      if config.analysis.enabled else {}),
                   **({'context_source': context_sources[case.id]} if config.context.enabled
                      else {})}
                  for case in cases],
    }
    write_json(output / 'snapshot.json', snapshot)
    capability = await inspect_nare_runtime(runtime)
    snapshot.update(nare_version=capability.version, nare_contract=capability.contract)
    write_json(output / 'snapshot.next.json', snapshot)
    (output / 'snapshot.next.json').replace(output / 'snapshot.json')
    completed: dict[str, dict[str, Any]] = {}
    def persist() -> dict[str, Any]:
        records = [completed.get(cell['run_id'], {**cell, 'status': 'missing',
                   'accounting_complete': False, 'usage': TokenUsage().to_dict(),
                   'findings': [], 'latency_seconds': 0}) for cell in snapshot['expected_runs']]
        result = {'schema_version': 1, 'evidence_kind': evidence_kind,
                  'status': 'complete' if len(completed) == len(records) else 'incomplete',
                  'runs': records}
        temporary = output / 'experiment.next.json'
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
        temporary.replace(output / 'experiment.json')
        return result
    persist()
    async def execute(case: Case, variant: str, conf: ReviewConfig, repeat: int) -> None:
        run_id = f'{case.id}/{variant}/{repeat}'
        directory = output / run_id
        directory.mkdir(parents=True)
        for name, data in captures[case.id].items():
            with (directory / name).open('xb') as stream:
                stream.write(data)
        write_json(directory / 'config.json', conf.to_dict())
        write_json(directory / 'config.yaml', conf.to_dict())
        if conf.context.enabled:
            with (directory / 'metadata.original.json').open('xb') as stream:
                stream.write(captures[case.id]['metadata.json'])
            (directory / 'metadata.json').write_bytes(derived_metadata[case.id])
            capture_repository_context(context_clients[case.id], directory, conf,
                                       source=context_clients[case.id].provenance())
        capture_static_analysis(directory, conf,
                                capture_bytes=analysis_captures.get(case.id))
        prepare_review_inputs(directory, conf)
        started = time.monotonic()
        record: dict[str, Any] = {
            'run_id': run_id, 'case_id': case.id, 'variant': variant, 'repeat': repeat,
            'run_dir': str(directory), 'status': 'failed', 'accounting_complete': False,
            'usage': TokenUsage().to_dict(), 'findings': [], 'error': None,
        }
        interrupted = False
        try:
            panel = await run_review(directory, conf, runtime=runtime)
            record.update(status=panel.status,
                          accounting_complete=panel.accounting_complete,
                          usage=panel.usage.to_dict())
            if panel.verdict is not None:
                record['findings'] = [source for group in panel.verdict.findings
                                      for source in cast(list[dict[str, Any]],
                                                         group.to_dict()['sources'])]
            elif panel.status != 'failed':
                record['status'] = 'invalid'
        except asyncio.CancelledError:
            interrupted = True
            record['error'] = 'Interrupted'
            record['usage'] = _observed_usage(directory).to_dict()
        except Exception as error:
            # Provider details remain in private engine captures.
            record['error'] = type(error).__name__
            record['usage'] = _observed_usage(directory).to_dict()
        record['latency_seconds'] = time.monotonic() - started
        write_json(directory / 'evaluation.json', record)
        write_artifact_manifest(directory)
        completed[run_id] = record
        with (output / 'runs.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, sort_keys=True) + '\n')
        persist()
        if interrupted:
            raise asyncio.CancelledError

    semaphore = asyncio.Semaphore(concurrency)
    async def bounded(case: Case, variant: str, conf: ReviewConfig, repeat: int) -> None:
        async with semaphore:
            await execute(case, variant, conf, repeat)
    tasks = [asyncio.create_task(bounded(case, variant, conf, repeat))
             for case in cases for variant, conf in variants.items() for repeat in range(1, 4)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        persist()
    return persist()
