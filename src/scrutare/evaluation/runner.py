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
from scrutare.config import (
    AnalysisSettings,
    AssessmentSettings,
    BudgetSettings,
    DiscussionSettings,
    FindingSettings,
    InspectionSettings,
    ReviewConfig,
)
from scrutare.engine import assessment_inputs, discussion_inputs
from scrutare.engine.assessment_inputs import assessor_persona
from scrutare.engine.budgets import ReviewBudgetLedger
from scrutare.engine.nare_session import inspect_nare_runtime
from scrutare.engine.repository_context import capture_repository_context, encoded
from scrutare.engine.repository_snapshot import RepositorySnapshot
from scrutare.engine.review_inputs import (
    PreparedReviewInputs,
    prepare_review_inputs,
    prepared_content_hashes,
)
from scrutare.engine.routing import plan_routing
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.engine.static_analysis import capture_static_analysis, read_capture
from scrutare.engine.strategy import run_review
from scrutare.evaluation.corpus import Case, parse_json, read_json, source_contents
from scrutare.evaluation.focused_trace import discussion_trace
from scrutare.evaluation.transient import transient_provider_failures
from scrutare.personas import PersonaDefinition, load_persona, resolve_personas
from scrutare.personas.registry import procedure_record
from scrutare.provenance import write_artifact_manifest


def write_json(path: Path, data: Any) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def comparison_configs(config: ReviewConfig, *, comparison_set: str = 'baseline',
                       variant_names: tuple[str, ...] | None = None,
                       assessment_tokens: int | None = None
                       ) -> dict[str, ReviewConfig]:
    """Select explicit arms in their comparison order; never invent or reorder one.

    `assessment_tokens` replaces the default one-fifth assessor reservation in every
    assessed arm. The total ceiling is unchanged, so the reviewers share the rest.
    """
    variants = _comparison_configs(config, comparison_set=comparison_set,
                                   assessment_tokens=assessment_tokens)
    if variant_names is None:
        return variants
    if (not isinstance(variant_names, tuple) or not variant_names
            or len(set(variant_names)) != len(variant_names)
            or any(name not in variants for name in variant_names)):
        raise ValueError('variant selection requires distinct arms of the comparison set')
    return {name: value for name, value in variants.items() if name in variant_names}


def _comparison_configs(config: ReviewConfig, *, comparison_set: str = 'baseline',
                        assessment_tokens: int | None = None) -> dict[str, ReviewConfig]:
    """Freeze current roster, shared rail and total cap; reserve a fifth debate chair quota."""
    if comparison_set not in ('baseline', 'procedures', 'architectures', 'focused'):
        raise ValueError('unknown comparison set')
    if config.models.overrides:
        raise ValueError('comparison requires a single fixed model rail without overrides')
    total = config.budgets.review_max_tokens
    if total < 5:
        raise ValueError('comparison total ceiling must support five participants')
    roster = ('senior-dev', 'junior-dev', 'security', 'devops')
    if assessment_tokens is not None and (
            comparison_set not in ('architectures', 'focused')
            or type(assessment_tokens) is not int
            or not 0 < assessment_tokens <= total - len(roster)):
        raise ValueError('assessment reservation requires an assessed comparison set and a '
                         'positive integer that leaves every reviewer a grant')
    if comparison_set in ('architectures', 'focused'):
        base = replace(config, strategy='panel', personas=roster,
            budgets=BudgetSettings((total + 3) // 4, total),
            context=replace(config.context, enabled=False),
            findings=FindingSettings(), inspection=InspectionSettings(),
            analysis=AnalysisSettings(), routing=replace(config.routing, enabled=False))
        context = replace(base, context=replace(base.context, enabled=True))
        procedures = replace(context, inspection=InspectionSettings('v1'))
        evidence = replace(procedures, findings=FindingSettings('v2'))
        assessed = replace(evidence, findings=FindingSettings('v2',
            AssessmentSettings(True, total // 5 if assessment_tokens is None
                               else assessment_tokens)))
        if comparison_set == 'focused':
            discussion = replace(assessed, strategy='debate',
                findings=replace(assessed.findings, discussion=DiscussionSettings(True)))
            return {'aggregate': evidence, 'assessed': assessed,
                'focused1': replace(discussion, rounds=replace(config.rounds, max=1)),
                'focused3': replace(discussion, rounds=replace(config.rounds, max=3))}
        return {'baseline': base, 'context': context, 'procedures': procedures,
            'evidence': evidence,
            'assessment': assessed,
            'tools': replace(assessed, analysis=AnalysisSettings(True)),
            'routing': replace(evidence, routing=replace(config.routing, enabled=True))}
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
                         comparison_set: str = 'baseline',
                         variant_names: tuple[str, ...] | None = None,
                         transient_retries: int = 0,
                         transient_retry_delay_seconds: float = 0.0,
                         transient_retry_budget: int | None = None,
                         assessment_tokens: int | None = None,
                         ) -> dict[str, Any]:
    """Fresh 3-repeat job; ordinary CI calls this only with explicitly offline evidence.

    A cell interrupted only by upstream HTTP 5xx may rerun in a fresh directory
    up to `transient_retries` times, within `transient_retry_budget` reruns for the
    whole job when one is given. Every attempt stays on disk and in its record.
    """
    if (evidence_kind not in ('model', 'offline') or not cases
            or type(concurrency) is not int or not 1 <= concurrency <= 32):
        raise ValueError('nonempty cases and model/offline evidence kind required')
    if (type(transient_retries) is not int or not 0 <= transient_retries <= 5
            or type(transient_retry_delay_seconds) not in (int, float)
            or not 0 <= transient_retry_delay_seconds <= 600
            or transient_retry_budget is not None and (
                type(transient_retry_budget) is not int or transient_retry_budget < 0)):
        raise ValueError('transient retry policy requires 0-5 retries, 0-600 seconds and a '
                         'nonnegative job budget')
    variants = comparison_configs(config, comparison_set=comparison_set,
                                  variant_names=variant_names,
                                  assessment_tokens=assessment_tokens)
    needs_context = any(v.context.enabled for v in variants.values())
    needs_analysis = any(v.analysis.enabled for v in variants.values())
    # Freeze before the first await; snapshot hashes and every repeat use these bytes.
    captures = {case.id: {name: (case.capture / name).read_bytes()
                         for name in ('diff.patch', 'files.json', 'metadata.json')}
                for case in cases}
    analysis_captures = {case.id: read_capture(case.analysis_capture)
                         if case.analysis_capture is not None else None
                         for case in cases} if needs_analysis else {}
    context_clients: dict[str, RepositorySnapshot] = {}
    context_sources: dict[str, dict[str, Any]] = {}
    derived_metadata: dict[str, bytes] = {}
    if needs_context:
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
        'variant_selection': list(variants),
        'assessment_tokens': assessment_tokens,
        'transient_retry': {
            'max_retries': transient_retries, 'delay_seconds': transient_retry_delay_seconds,
            'max_job_retries': transient_retry_budget,
            'eligibility': 'attempt incomplete without evaluator error; every native session '
                           'complete, not started, or failed with a retained provider HTTP 5xx',
            'accounting': 'usage is the final attempt; retry_usage sums earlier attempts as '
                          'a lower bound; latency_seconds spans all attempts and waits; '
                          'each attempt has its own review ceiling'},
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
                    variants.get('panel', next(iter(variants.values()))).personas
                     if isinstance(name, str)},
        'cases': [{'id': case.id, 'split': case.split, 'domain': case.domain,
                   'capture_sha256': {name: hashlib.sha256(data).hexdigest()
                                      for name, data in captures[case.id].items()},
                   'labels_sha256': label_hashes[case.id],
                   **({'analysis_source_sha256': hashlib.sha256(
                       cast(bytes, analysis_captures[case.id]))
                       .hexdigest() if analysis_captures[case.id] is not None else None}
                      if needs_analysis else {}),
                   **({'context_source': context_sources[case.id]} if needs_context
                      else {})}
                  for case in cases],
    }
    def prepare(directory: Path, case: Case, conf: ReviewConfig) -> PreparedReviewInputs:
        """Materialize one attempt from the bytes frozen above, never from the corpus."""
        directory.mkdir(parents=True)
        for name, data in captures[case.id].items():
            (directory / name).write_bytes(data)
        write_json(directory / 'config.json', conf.to_dict())
        write_json(directory / 'config.yaml', conf.to_dict())
        if conf.context.enabled:
            (directory / 'metadata.original.json').write_bytes(
                captures[case.id]['metadata.json'])
            (directory / 'metadata.json').write_bytes(derived_metadata[case.id])
            capture_repository_context(context_clients[case.id], directory, conf,
                                       source=context_clients[case.id].provenance())
        capture_static_analysis(directory, conf,
                                capture_bytes=analysis_captures.get(case.id)
                                if conf.analysis.enabled else None)
        return prepare_review_inputs(directory, conf)

    # Prepare every case synchronously before provider inspection. This records
    # actual routed participants and fixed ledger reservations, not guessed rosters.
    case_variants: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_variants[case.id] = {}
        for variant, conf in variants.items():
            for repeat in range(1, 4):
                inputs = prepare(output / case.id / variant / str(repeat), case, conf)
                if repeat != 1:
                    continue
                personas = resolve_personas(conf.personas,
                                            procedures=conf.inspection.procedures)
                routing = None
                if conf.routing.enabled:
                    focus = read_json(inputs.root / 'routing-focus.json')
                    plan = plan_routing(read_json(inputs.root / 'files.json'),
                                        tuple(focus['global_supporting_paths']), conf)
                    personas, routing = plan.personas, plan.record
                names = tuple(p.name for p in personas)
                phases = {}
                if conf.strategy == 'debate' and not conf.findings.discussion.enabled:
                    name = 'debate-chair'
                    while name in names:
                        name += '-chair'
                    phases[name] = replace(load_persona('senior-dev',
                        procedures=conf.inspection.procedures), name=name)
                names += tuple(phases)
                reserved = None
                if conf.findings.assessment.enabled:
                    name = 'evidence-assessor'
                    while name in names:
                        name += '-assessor'
                    phases[name] = assessor_persona(name)
                    names += (name,)
                    reserved = {name: conf.findings.assessment.tokens}
                ledger = ReviewBudgetLedger(names, conf.budgets,
                                            reserved_allocations=reserved)
                inline_names = {p.name for p in conf.personas
                                if isinstance(p, PersonaDefinition)} | {
                    rule.persona.name for rule in conf.routing.rules
                    if isinstance(rule.persona, PersonaDefinition)}
                def describe(persona: PersonaDefinition) -> dict[str, Any]:
                    return {**procedure_record(persona,
                        procedures=conf.inspection.procedures, inline=persona.name in inline_names),
                        'system_prompt': persona.system_prompt}
                phase_descriptions = {n: {**describe(p), 'origin': 'engine',
                    'version': 'assessment-v1' if n.startswith('evidence-assessor')
                               else 'debate-chair-v1'} for n, p in phases.items()}
                cell = {'effective_personas': [p.name for p in personas],
                        'personas': {p.name: describe(p) for p in personas},
                        'phase_personas': phase_descriptions,
                        'allocations': dict(ledger.allocations),
                        'review_ceiling': conf.budgets.review_max_tokens,
                        'inputs_sha256': prepared_content_hashes(inputs),
                        'routing': routing}
                if conf.findings.discussion.enabled:
                    cell['phase_budget_policy'] = 'reuse_discovery_and_assessor_grants'
                    cell['protocol_sources'] = {
                        'assessment_inputs_sha256': hashlib.sha256(Path(
                            assessment_inputs.__file__).read_bytes()).hexdigest(),
                        'discussion_inputs_sha256': hashlib.sha256(Path(
                            discussion_inputs.__file__).read_bytes()).hexdigest()}
                if conf.analysis.enabled:
                    cell['analysis_status'] = read_json(inputs.root / 'static-analysis.json')[
                        'status']
                case_variants[case.id][variant] = cell
    snapshot['case_variants'] = case_variants
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
    async def attempt_once(run_id: str, case: Case, variant: str, conf: ReviewConfig,
                           repeat: int, directory: Path) -> tuple[dict[str, Any], bool]:
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
        if conf.findings.discussion.enabled:
            record['discussion_trace'] = discussion_trace(directory)
            if record['discussion_trace']['availability'] != 'recorded':
                if record['status'] != 'failed':
                    record['status'] = 'invalid'
                    record['error'] = 'UnavailableFocusedTrace'
        record['latency_seconds'] = time.monotonic() - started
        return record, interrupted

    reruns = {'used': 0}

    async def execute(case: Case, variant: str, conf: ReviewConfig, repeat: int) -> None:
        run_id = f'{case.id}/{variant}/{repeat}'
        directory = output / run_id
        started = time.monotonic()
        attempts: list[dict[str, Any]] = []
        while True:
            record, interrupted = await attempt_once(run_id, case, variant, conf, repeat,
                                                     directory)
            if transient_retries:
                # A failed engine status with no evaluator error is the only rerun candidate.
                transient = (transient_provider_failures(directory)
                             if record['status'] != 'complete' and record['error'] is None
                             else None)
                attempts.append({'attempt': len(attempts) + 1, 'run_dir': record['run_dir'],
                    'status': record['status'], 'error': record['error'],
                    'accounting_complete': record['accounting_complete'],
                    'usage': record['usage'], 'latency_seconds': record['latency_seconds'],
                    'transient_provider_failures': transient})
                earlier = [TokenUsage(**{key: a['usage'][key] for key in (
                    'input', 'output', 'cache_read', 'cache_write')}) for a in attempts[:-1]]
                record.update(attempt=len(attempts), attempts=list(attempts),
                    retry_usage=TokenUsage(*(sum(getattr(u, key) for u in earlier) for key in (
                        'input', 'output', 'cache_read', 'cache_write'))).to_dict())
            write_json(directory / 'evaluation.json', record)
            write_artifact_manifest(directory)
            if (interrupted or not transient_retries or attempts[-1][
                    'transient_provider_failures'] is None or len(attempts) > transient_retries):
                break
            if transient_retry_budget is not None and reruns['used'] >= transient_retry_budget:
                # The declared spend bound wins; the eligible failure stays recorded.
                record['retry_denied'] = 'job_retry_budget'
                break
            reruns['used'] += 1
            refused = None
            planned = output / '_retries' / run_id / f'attempt-{len(attempts) + 1:04d}'
            try:
                await asyncio.sleep(transient_retry_delay_seconds)
                if prepared_content_hashes(prepare(planned, case, conf)) != case_variants[
                        case.id][variant]['inputs_sha256']:
                    # Never spend on inputs that differ from the frozen first attempt.
                    refused = 'RetryInputsDiffer'
            except asyncio.CancelledError:
                # The spent attempt must still reach the job record before cancellation.
                interrupted, refused = True, 'Interrupted'
            except Exception as error:
                refused = type(error).__name__
            if refused is not None:
                record['error'] = refused
                attempts.append({'attempt': len(attempts) + 1, 'run_dir': str(planned),
                    'status': 'refused', 'error': refused, 'accounting_complete': True,
                    'usage': TokenUsage().to_dict(), 'latency_seconds': 0.0,
                    'transient_provider_failures': None})
                record['attempts'] = list(attempts)
                break
            directory = planned
        if transient_retries:
            record['latency_seconds'] = time.monotonic() - started
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
