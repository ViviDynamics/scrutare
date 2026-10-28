"""Rule A's common operational predicates, separate from semantic promotion.

Inspectors must derive the cell/attempt facts from retained engine artifacts and
independently reconciled, uniquely attributed provider records. Missing facts fail
closed; a retry cannot turn an incomplete ledger into a complete one.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

ARMS = ('aggregate', 'assessed', 'focused1', 'focused3')


def rule_a_predicates(cells: list[dict[str, Any]], *, cases: list[str],
                      clean_cases: list[str], experiment_terminal: bool,
                      holdout_used: bool, max_tokens_per_attempt: int = 200000
                      ) -> dict[str, bool]:
    expected = {f'{case}/{arm}/{repeat}' for case in cases for arm in ARMS
                for repeat in range(1, 4)}
    counts = Counter(c.get('run_id') for c in cells)
    matrix = (len(cases) == 3 and len(set(cases)) == 3 and len(clean_cases) == 1
              and set(clean_cases) < set(cases) and len(expected) == 36
              and set(counts) == expected and all(v == 1 for v in counts.values()))
    attempts = [a for c in cells for a in c.get('attempts', [])]
    defects = [c for c in cells if str(c.get('run_id', '')).split('/')[0]
               in set(cases) - set(clean_cases)]
    def valid(c: dict[str, Any]) -> bool:
        return c.get('findings', 0) > 0 and c.get('evidence_valid') is True
    def bounded(c: dict[str, Any]) -> bool:
        history = c.get('attempts', [])
        if not 1 <= len(history) <= 2:
            return False
        if [a.get('attempt') for a in history] != list(range(1, len(history) + 1)):
            return False
        if len(history) == 2 and not (history[0].get('status') != 'complete'
                and history[0].get('failed_sessions', 0) > 0
                and history[0].get('retryable') is True):
            return False
        selected = c.get('selected_attempt')
        return (selected == len(history) and history[-1].get('status') == 'complete'
                if c.get('status') == 'complete' else selected is None)
    aggregate = [c for c in defects if c['run_id'].split('/')[1] == 'aggregate']
    assessed = [c for c in defects if c['run_id'].split('/')[1] == 'assessed']
    return {
        'matrix_exact': matrix,
        'experiment_terminal': experiment_terminal is True,
        'all_36_complete_accounted': matrix and all(
            c.get('status') == 'complete' and c.get('accounting_complete') is True for c in cells),
        'all_12_clean_complete': matrix and sum(c.get('status') == 'complete'
            and c.get('accounting_complete') is True for c in cells
            if c['run_id'].split('/')[0] in clean_cases) == 12,
        'all_24_defect_cells_nonempty_valid': len(defects) == 24 and all(map(valid, defects)),
        'six_aggregate_nonempty_valid': len(aggregate) == 6 and all(map(valid, aggregate)),
        'six_assessed_actual_complete': len(assessed) == 6 and all(
            c.get('assessor_complete') is True for c in assessed),
        'accepted_replay_valid': matrix and all(c.get('replay_valid') is True for c in cells),
        'bounded_rule_a_selection': matrix and all(map(bounded, cells)),
        'every_attempt_terminal': bool(attempts) and all(
            a.get('terminal') is True for a in attempts),
        'all_attempts_reconciled': bool(attempts) and all(
            a.get('ledger_reconciled') is True for a in attempts),
        'all_attempts_provenance_routing': bool(attempts) and all(
            a.get('provenance_valid') is True and a.get('routing_valid') is True for a in attempts),
        'all_attempts_within_ceiling': bool(attempts) and all(
            type(a.get('tokens')) is int and 0 <= a['tokens'] <= max_tokens_per_attempt
            for a in attempts),
        'zero_paid_calls_and_fallbacks': bool(attempts) and all(
            type(a.get('paid_calls')) is int and a['paid_calls'] == 0
            and type(a.get('fallbacks')) is int and a['fallbacks'] == 0 for a in attempts),
        'holdout_untouched': holdout_used is False,
    }


def inspect_rule_a(root: Any, gate: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """Inspect retained runs and an explicit provider-request attribution ledger.

    Each provider request has a unique id, run_id, attempt, relative session,
    source_sha256, model/api_base, paid_calls/fallbacks and disjoint usage. Recorded
    cost events bind by stream and zero-based cost-event index. A missing failed
    request instead requires one supplement binding its id to the session-result
    SHA-256 and reason; native flags are never rewritten. Provider authenticity is
    outside this offline check: retain the original read receipts and raw exports.
    """
    import hashlib
    import json
    from pathlib import Path

    from scrutare.config import parse_config
    from scrutare.engine.recovery import RULE_A, retryable_sessions, sum_usage
    from scrutare.engine.review_inputs import prepare_review_inputs, prepared_content_hashes
    from scrutare.engine.session_output import decode_cost_event
    from scrutare.findings.evidence import validate_evidence
    from scrutare.findings.models import parse_finding
    from scrutare.replay import replay_run

    root = Path(root).resolve()
    def read(path: Path) -> Any:
        if path.is_symlink():
            raise ValueError('linked qualification evidence')
        return json.loads(path.read_bytes())
    experiment, snapshot = read(root / 'experiment.json'), read(root / 'snapshot.json')
    requests = ledger.get('requests', [])
    supplements = ledger.get('supplements', [])
    ids = [r.get('id') for r in requests]
    unique_ids = bool(requests) and all(isinstance(i, str) and i for i in ids) \
        and len(set(ids)) == len(ids)
    used_requests: set[str] = set()
    used_supplements: set[str] = set()
    cells = []
    for row in experiment['runs']:
        identifier = row['run_id']
        attempts = []
        for saved in row.get('attempts', []):
            number = saved['attempt']
            directory = (root / identifier if number == 1 else
                         root / '_retries' / identifier / 'attempt-0002')
            facts: dict[str, Any] = {**saved, 'tokens': saved['usage']['total'],
                'provenance_valid': False, 'routing_valid': False, 'ledger_reconciled': False,
                'terminal': False, 'failed_sessions': 0, 'retryable': False,
                'paid_calls': None, 'fallbacks': None}
            try:
                if (Path(saved['run_dir']).resolve() != directory
                        or not directory.is_relative_to(root)):
                    raise ValueError('attempt identity differs')
                manifest = read(directory / 'artifacts.json')['artifacts']
                actual_files = {p.relative_to(directory).as_posix() for p in directory.rglob('*')
                                if p.is_file() and p.name != 'artifacts.json'}
                recorded = {e['path'] for e in manifest}
                if actual_files != recorded or len(recorded) != len(manifest):
                    raise ValueError('incomplete provenance')
                for entry in manifest:
                    path = directory / entry['path']
                    if (not path.resolve().is_relative_to(directory) or path.is_symlink()
                            or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']):
                        raise ValueError('provenance differs')
                config = parse_config((directory / 'config.yaml').read_bytes())
                case, variant, _ = identifier.split('/')
                inputs = prepare_review_inputs(directory, config)
                if (config.to_dict() != snapshot['configs'][variant]
                        or read(directory / 'qualification-policy.json') != RULE_A
                        or prepared_content_hashes(inputs) != snapshot[
                            'case_variants'][case][variant]['inputs_sha256']):
                    raise ValueError('frozen inputs differ')
                facts['provenance_valid'] = True
                sessions = sorted(directory.glob('**/sessions/*/attempt-*/result.json'))
                started, outcomes, engine_costs = [], [], []
                matched = True
                rail_ok = True
                for path in sessions:
                    result = read(path)
                    if result['status'] == 'not_started':
                        if (path.parent / 'invocation.json').exists():
                            raise ValueError('started session marked unstarted')
                        continue
                    session = path.parent.relative_to(directory).as_posix()
                    started.append(session)
                    outcomes.append(result)
                    invocation = read(path.parent / 'invocation.json')
                    rail = invocation['rail']
                    rail_ok &= (rail == config.models.for_persona(result['persona']).record()
                        and rail['model'] == 'spark/glm-5.3-flash'
                        and rail['base_url'].rstrip('/') in (
                            'https://litellm.vividynamics.com/v1',
                            'https://litellm.vividynamics.com'))
                    session_costs = []
                    for stream in ('stdout.jsonl', 'correction.stdout.jsonl'):
                        if not (path.parent / stream).exists():
                            continue
                        costs = [u for line in (path.parent / stream).read_bytes().splitlines()
                                 if line.strip() and (u := decode_cost_event(line)) is not None]
                        for index, usage in enumerate(costs):
                            session_costs.append({'usage': usage.to_dict()})
                            matches = [r for r in requests if r.get('run_id') == identifier
                                and r.get('attempt') == number and r.get('session') == session
                                and r.get('stream') == stream and r.get('event') == index]
                            if len(matches) != 1 or matches[0]['usage'] != usage.to_dict():
                                matched = False
                            else:
                                used_requests.add(matches[0]['id'])
                    if sum_usage(session_costs) != result['usage']:
                        matched = False
                    engine_costs += session_costs
                    extras = [s for s in supplements if s.get('run_id') == identifier
                              and s.get('attempt') == number and s.get('session') == session]
                    for supplement in extras:
                        matches = [r for r in requests if r.get('id') == supplement['id']]
                        if (len(matches) != 1 or supplement['id'] in used_requests
                                or supplement.get('session_result_sha256') != hashlib.sha256(
                                    path.read_bytes()).hexdigest()
                                or supplement.get('reason') != 'missing_failed_request'
                                or result['status'] not in ('failed', 'partial')):
                            matched = False
                        else:
                            used_requests.add(supplement['id'])
                            used_supplements.add(supplement['id'])
                    # Incomplete execution accounting needs explicit reconciliation evidence.
                    if not result['accounting_complete'] and not extras:
                        matched = False
                attributed = [r for r in requests if r.get('run_id') == identifier
                              and r.get('attempt') == number]
                facts.update(
                    sessions_complete=bool(started) and all(o['status'] == 'complete'
                        and o['accounting_complete'] is True for o in outcomes),
                    terminal=bool(started) and all(o['status'] in ('complete', 'failed', 'partial')
                        and o['reason'] != 'cancelled' for o in outcomes),
                    failed_sessions=sum(o['status'] != 'complete' for o in outcomes),
                    retryable=retryable_sessions(directory) is not None,
                    routing_valid=rail_ok and bool(started),
                    ledger_reconciled=unique_ids and matched and bool(attributed)
                        and sum_usage(engine_costs) == saved['usage'],
                    tokens=sum_usage(attributed)['total'],
                    paid_calls=sum(r['paid_calls'] for r in attributed),
                    fallbacks=sum(r['fallbacks'] for r in attributed))
                facts['routing_valid'] &= all(
                    r['model'] == 'openai/glm-5.3-flash'
                    and r['api_base'].rstrip('/') == 'http://192.168.3.40:8045'
                    and isinstance(r.get('source_sha256'), str)
                    and len(r['source_sha256']) == 64 for r in attributed)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            attempts.append(facts)
        evidence_valid, replay_valid, assessor = False, False, False
        try:
            directory = Path(row['run_dir'])
            if not directory.resolve().is_relative_to(root):
                raise ValueError('accepted directory outside experiment')
            for finding in row['findings']:
                parsed = parse_finding({k: v for k, v in finding.items() if k != 'persona'},
                    persona=finding['persona'], evidence_version=2, artifact=True)
                validate_evidence(parsed, directory / 'review-inputs')
                if parsed.evidence is None or not parsed.evidence.citations:
                    raise ValueError('missing v2 citations')
            evidence_valid = True
            replay_valid = replay_run(directory).exit_code == 0
            if row['status'] == 'complete':
                selected = row.get('selected_attempt')
                if (not isinstance(selected, int) or not 1 <= selected <= len(attempts)
                        or not attempts[selected - 1].get('sessions_complete')):
                    replay_valid = False
                verdict = read(directory / 'verdict.json')
                sources = [source for finding in verdict['findings']
                           for source in finding['sources']]
                if sources != row['findings']:
                    evidence_valid = False
            if (directory / 'assessment.json').exists():
                assessment = read(directory / 'assessment.json')
                outcome = assessment.get('outcome') or {}
                assessor = (assessment['status'] == 'complete'
                    and outcome.get('status') == 'complete'
                    and outcome.get('accounting_complete') is True
                    and outcome.get('usage', {}).get('total', 0) > 0)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        cells.append({**row, 'attempts': attempts, 'evidence_valid': evidence_valid,
                      'findings': len(row['findings']), 'replay_valid': replay_valid,
                      'assessor_complete': assessor})
    tests = rule_a_predicates(cells, cases=gate['cases'], clean_cases=gate['clean_cases'],
        experiment_terminal=experiment['status'] == 'complete',
        holdout_used=gate['holdout_used'], max_tokens_per_attempt=gate['max_tokens_per_run'])
    tests['all_provider_requests_attributed_once'] = (
        unique_ids and used_requests == set(ids) and len(used_supplements) == len(supplements))
    tests['rule_a_frozen'] = snapshot.get('qualification') == RULE_A
    tests['cumulative_cost_complete'] = (
        experiment.get('cumulative_usage') == sum_usage(
            [a for r in experiment['runs'] for a in r.get('attempts', [])])
        and all(r.get('cumulative_usage') == sum_usage(r.get('attempts', []))
                for r in experiment['runs']))
    return {'schema_version': 1, 'rule': 'A', 'tests': tests,
            'qualification': 'pass' if all(tests.values()) else 'not_qualified',
            'cumulative_usage': experiment.get('cumulative_usage'),
            'reconciled_cumulative_usage': sum_usage(requests), 'cells': cells,
            'semantic_qualification': False, 'holdout_used': False}
