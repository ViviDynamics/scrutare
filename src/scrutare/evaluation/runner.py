"""Explicit non-posting experiments through the production nare engine."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from scrutare import __version__
from scrutare.config import BudgetSettings, ReviewConfig
from scrutare.engine.nare_session import inspect_nare_runtime
from scrutare.engine.review_inputs import prepare_review_inputs
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.engine.strategy import run_review
from scrutare.evaluation.corpus import Case, read_json
from scrutare.personas import load_persona
from scrutare.provenance import write_artifact_manifest


def write_json(path: Path, data: Any) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def comparison_configs(config: ReviewConfig) -> dict[str, ReviewConfig]:
    """Freeze current roster, shared rail and total cap; reserve a fifth debate chair quota."""
    if config.models.overrides:
        raise ValueError('comparison requires a single fixed model rail without overrides')
    total = config.budgets.review_max_tokens
    if total < 5:
        raise ValueError('comparison total ceiling must support five participants')
    roster = ('senior-dev', 'junior-dev', 'security', 'devops')
    return {
        'senior': replace(config, strategy='panel', personas=('senior-dev',),
                          budgets=BudgetSettings(total, total)),
        'panel': replace(config, strategy='panel', personas=roster,
                         budgets=BudgetSettings(total // 4, total)),
        'debate': replace(config, strategy='debate', personas=roster,
                          budgets=BudgetSettings(total // 5, total)),
    }


async def run_experiment(cases: tuple[Case, ...], config: ReviewConfig, output: Path, *,
                         runtime: NareRuntime, evidence_kind: str = 'model'
                         ) -> dict[str, Any]:
    """Fresh 3-repeat job; ordinary CI calls this only with explicitly offline evidence."""
    if evidence_kind not in ('model', 'offline') or not cases:
        raise ValueError('nonempty cases and model/offline evidence kind required')
    variants = comparison_configs(config)
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    capability = await inspect_nare_runtime(runtime)
    snapshot = {
        'schema_version': 1, 'scrutare_version': __version__, 'evidence_kind': evidence_kind,
        'nare_version': capability.version, 'nare_contract': capability.contract,
        'runtime': {**asdict(runtime), 'executable': str(runtime.executable)},
        'repeats': 3, 'configs': {name: value.to_dict() for name, value in variants.items()},
        'personas': {name: load_persona(name).system_prompt for name in variants['panel'].personas
                     if isinstance(name, str)},
        'cases': [{'id': case.id, 'split': case.split, 'domain': case.domain,
                   'capture_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in case.capture.iterdir()},
                   'labels_sha256': hashlib.sha256(case.labels.read_bytes()).hexdigest()}
                  for case in cases],
    }
    write_json(output / 'snapshot.json', snapshot)
    runs = []
    for case in cases:
        for variant, conf in variants.items():
            for repeat in range(1, 4):
                run_id = f'{case.id}/{variant}/{repeat}'
                directory = output / run_id
                directory.mkdir(parents=True)
                for name in ('diff.patch', 'files.json', 'metadata.json'):
                    shutil.copyfile(case.capture / name, directory / name)
                write_json(directory / 'config.json', conf.to_dict())
                write_json(directory / 'config.yaml', conf.to_dict())
                prepare_review_inputs(directory, conf)
                started = time.monotonic()
                record: dict[str, Any] = {
                    'run_id': run_id, 'case_id': case.id, 'variant': variant, 'repeat': repeat,
                    'run_dir': str(directory), 'status': 'failed', 'accounting_complete': False,
                    'usage': TokenUsage().to_dict(), 'findings': [], 'error': None,
                }
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
                except Exception as error:
                    # Provider details remain in private engine captures.
                    record['error'] = type(error).__name__
                    panel_path = directory / 'panel.json'
                    if panel_path.is_file():
                        saved = read_json(panel_path)
                        record['usage'] = saved['ledger']['usage']
                        record['accounting_complete'] = saved['ledger']['accounting_complete']
                record['latency_seconds'] = time.monotonic() - started
                write_json(directory / 'evaluation.json', record)
                write_artifact_manifest(directory)
                runs.append(record)
                with (output / 'runs.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record, sort_keys=True) + '\n')
    result = {'schema_version': 1, 'evidence_kind': evidence_kind, 'runs': runs}
    write_json(output / 'experiment.json', result)
    return result
