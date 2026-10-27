"""Reproduce g3's frozen gate, corrections, causes, spend and load offline.

Uses only the standard library and the immutable c1/s2 sibling report packages.
Private capture and spend-read hashes bind the exported claims; public artifacts
cannot authenticate the original provider responses or proxy observations.
"""
import argparse
import collections
import hashlib
import importlib.util
import json
from pathlib import Path

C1 = '2026-10-22-glm-capped-c1-qualification'
S2 = '2026-10-21-glm-structured-s2-qualification'
POSTED = {
    'preparation.json': 'f4c77b615afb58672895cc72525ea1551f5bdf4140aa2bd861a85af28924090a',
    'gate.json': '7d4c2f51057bbeea2c138bea77b156228b3b140e0e19fb1e2f96eefe84d62b78',
    'evaluation.json': '0dfb9ed918fb8b140cbc2830a8f699168f2615d9af349356bf6075506a938ac0',
    'scrutare-2026.10.36-py3-none-any.whl':
        '08f4b13b109564cc30ad6728c528ba45a5c9f31878c071b6d3f824668ba390c3',
}
POSTED_FREEZE = '4b9867c5b6ec3e8f38283fa42e7204651c55918e6ce53c90704cd9839f8a5792'
ROSTER = {'senior-dev', 'junior-dev', 'security', 'devops'}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_c1(reports):
    spec = importlib.util.spec_from_file_location('immutable_c1_report',
                                                  reports / C1 / 'reproduce.py')
    if not spec or not spec.loader:
        raise ValueError('cannot load the c1 report verifier')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.verify_hashes(reports / C1)
    helper.verify_hashes(reports / S2)
    return helper


def correction_summary(rows, answers):
    """Check first-answer classifications and bounded correction outcomes, then count."""
    by_answer = {a['session']: a for a in answers}
    if len(by_answer) != len(answers):
        raise ValueError('duplicate correction first-answer identity')
    outcomes = collections.Counter()
    kinds = collections.Counter()
    tokens = refused = answered = corrected_complete = 0
    offered_rows = []
    discovery = [r for r in rows if r['persona'] in ROSTER]
    for row in discovery:
        prefix = row.get('cell_attempt_prefix', row['cell'])
        key = f"{prefix}/sessions/{row['persona']}/{row['attempt']}"
        first = by_answer.get(key)
        record = row.get('citation_correction')
        if (first is None or record is None
                or (first['status'], first['reason']) != (row['status'], row['reason'])):
            raise ValueError('correction record or first-answer classification missing/disagrees')
        answer = first.get('first_answer')
        is_refused = answer not in (None, 'valid')
        correctable = first.get('correctable') is not None
        if ((answer is None) != (row['nare_status'] != 'done')
                or correctable and not is_refused
                or record['offered'] != (is_refused and correctable)):
            raise ValueError('correction offer disagrees with first-answer refusal')
        citation_refused = (answer or '').startswith('citation:') or is_refused and correctable
        answered += answer is not None
        refused += citation_refused
        if citation_refused:
            kinds[answer] += 1
        if record['offered']:
            if len(row.get('correction_request_output_tokens', [])) > 1:
                raise ValueError('more than one bounded correction turn was recorded')
            outcome = record['outcome']
            request = row.get('citation_correction_request')
            if (outcome not in ('corrected', 'refused', 'no_answer', 'failed')
                    or not request or len(request['defects']) != record['defects']
                    or row['status'] == 'complete' and outcome != 'corrected'):
                raise ValueError('correction outcome disagrees with session status/defects')
            before = row['first_run_tokens']
            extra = row['tokens'] - before if before is not None else -1
            if extra < 0 or outcome == 'corrected' and extra == 0:
                raise ValueError('correction usage does not include a bounded additional turn')
            outcomes[outcome] += 1
            tokens += extra
            corrected_complete += outcome == 'corrected' and row['status'] == 'complete'
            offered_rows.append({'session': key, 'outcome': outcome, 'status': row['status'],
                                 'tokens': extra, 'defects': request['defects']})
        elif (record != {'offered': False, 'defects': 0, 'outcome': None, 'exit_code': None}
              or row['status'] == 'complete' and is_refused):
            raise ValueError('correction outcome missing for refused complete session')
    if len(answers) != len(discovery):
        raise ValueError('correction first-answer entries outside discovery sessions')
    return {'discovery_sessions': len(discovery), 'answered_sessions': answered,
            'first_answers_refused_for_citations': refused, 'refusals_by_kind': dict(kinds),
            'offered': sum(outcomes.values()), 'by_outcome': dict(outcomes),
            'corrected_sessions_complete': corrected_complete, 'tokens': tokens,
            'sessions': offered_rows}


def verify_serving_rows(rows):
    for row in rows:
        if (row['model_group'] != 'spark/glm-5.3-flash'
                or row['model'] != 'openai/glm-5.3-flash'
                or row['api_base'].rstrip('/') != 'http://192.168.3.40:8045/v1'
                or row['attempted_fallbacks'] not in (None, 0)):
            raise ValueError('serving evidence includes a different deployment or fallback')


def provider_summary(evidence):
    result = {}
    for label in ('smoke', 'g3', 'prelaunch'):
        window = evidence['windows'][label]
        rows = window['rows']
        ours = [r for r in rows if r['agent'] == 'python-httpx2']
        if not ours:
            raise ValueError('serving evidence has no evaluator rows')
        verify_serving_rows(ours)
        by = {}
        for row in rows:
            group = f"{row['agent']} | {row['key']} | {row['api_base']}"
            part = by.setdefault(group, {'rows': 0, 'prompt_tokens': 0, 'completion_tokens': 0})
            part['rows'] += 1
            part['prompt_tokens'] += row['prompt_tokens']
            part['completion_tokens'] += row['completion_tokens']
        spark = [r for r in rows if r['api_base'].rstrip('/') == 'http://192.168.3.40:8045/v1']
        result[label] = {
            'window_utc': window['window_utc'], 'rows_all_keys': len(rows),
            'evaluator_rows': len(ours),
            'evaluator_prompt_tokens': sum(r['prompt_tokens'] for r in ours),
            'evaluator_completion_tokens': sum(r['completion_tokens'] for r in ours),
            'evaluator_nominal_attribution_usd': round(sum(r['spend'] for r in ours), 6),
            'external_spend_usd': 0, 'evaluator_paid_provider_rows': 0,
            'evaluator_attempted_fallbacks_nonzero': 0,
            'all_keys_attempted_fallbacks_nonzero': sum(
                bool(r['attempted_fallbacks']) for r in rows),
            'other_spark_rows': sum(r['agent'] != 'python-httpx2' for r in spark),
            'base_url_with_trailing_slash_rows': sum(r['api_base'].endswith('/') for r in spark),
            'by_agent_key_base': by,
            'evaluator_failed_rows': [r for r in ours if r['status'] != 'success']}
    for label in ('router_before_smoke', 'router_prelaunch', 'router_after'):
        router = evidence[label]
        if (not router['ok'] or any(router['fallback_fields'].values())
                or router['wildcard_groups'] or len(router['glm_deployments']) != 1):
            raise ValueError('serving router evidence does not exclude fallbacks')
        deployment, = router['glm_deployments']
        verify_serving_rows([{'model_group': 'spark/glm-5.3-flash', **deployment,
                              'attempted_fallbacks': 0}])
    return result


def frozen_checks(package, reports):
    directory = package / 'g3'
    if sha(package / 'freeze-record.json') != POSTED_FREEZE:
        raise ValueError('freeze record differs from the hash posted on #51')
    freeze = read(package / 'freeze-record.json')
    prep = read(directory / 'preparation.json')
    for name, digest in POSTED.items():
        actual = prep['input_sha256'][name] if name.endswith('.whl') else sha(directory / name)
        if actual != digest or freeze['files'][name] != digest:
            raise ValueError('frozen artifact differs from the hash posted on #51')
    for name in ('gate.json', 'evaluation.json'):
        if prep['input_sha256'][name] != sha(directory / name):
            raise ValueError('frozen preparation disagrees with its inputs')
    c1 = reports / C1 / 'c1'
    gate, old = read(directory / 'gate.json'), read(c1 / 'gate.json')
    if gate['c1_gate_sha256'] != sha(c1 / 'gate.json'):
        raise ValueError('frozen c1 predecessor hash disagrees')
    for key in gate['unchanged_criteria_keys']:
        if json.dumps(gate[key]).encode() != json.dumps(old[key]).encode():
            raise ValueError('gate criteria/pass rule differ from c1')
    expected = read(c1 / 'evaluation.json')
    expected['context']['line_counts'] = True
    expected['findings'] = {'evidence': 'v2', 'citation_correction': True}
    evaluation = read(directory / 'evaluation.json')
    if evaluation != expected:
        raise ValueError('configuration differs from c1 beyond the two requested settings')
    old_prep = read(c1 / 'preparation.json')
    for key, value in old_prep['input_sha256'].items():
        if key.startswith('corpus/') and prep['input_sha256'].get(key) != value:
            raise ValueError('frozen corpus input differs from c1')
    if (prep['release_tag'] != gate['release_tag'] or prep['source_commit'] != gate['source_commit']
            or prep['release_tag'] != '2026.10.36' or not prep['context_line_counts']
            or not prep['citation_correction'] or prep['holdout_used']):
        raise ValueError('preparation differs from the frozen release and settings')
    snap = read(directory / 'snapshot.json')
    smoke_rail = read(package / 'smoke-summary.json')['config']['models']['default']
    for arm in gate['arms']:
        config = snap['configs'][arm]
        if (not config['context']['line_counts'] or not config['findings']['citation_correction']
                or config['models']['default'] != smoke_rail):
            raise ValueError('runtime snapshot differs from frozen opt-ins/rail')
    original = read(directory / 'original-artifact-sha256.json')
    mechanical = read(directory / 'mechanical-qualification.json')
    if (mechanical['gate_sha256'] != sha(directory / 'gate.json')
            or mechanical['experiment_sha256'] != original['experiment/experiment.json']):
        raise ValueError('mechanical summary is not bound to the frozen gate/experiment')
    return gate


def session_cause(row, cells, helper):
    if 'repeating the same chunk' in (row.get('nare_error') or ''):
        return 'model_repetition'
    if row['reason'] == 'protocol':
        anchors = row.get('finding_answer_anchors') or []
        if any(type(a.get('line')) is int and a['line'] <= 0 for a in anchors):
            return 'invalid_finding_anchor'
        identifiers = row.get('assessment_answer_candidate_ids')
        if row['persona'] == 'evidence-assessor' and identifiers is not None:
            expected = {c['candidate_id'] for c in cells[row['cell']]['candidates']}
            if set(identifiers) != expected or len(identifiers) != len(set(identifiers)):
                return 'assessment_candidate_identity'
    return helper.cause(row)


def sessions_summary(directory, helper):
    cells = read(directory / 'mechanical-qualification.json')['cells']
    rows = read(directory / 'session-diagnosis.json')['sessions']
    unique = {(r['cell'], r.get('cell_attempt', 1), r['persona'], r['attempt']) for r in rows}
    if len(unique) != len(rows):
        raise ValueError('duplicated session diagnosis identity')
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[row['cell'], row.get('cell_attempt', 1)].append(row)
        cap = row['max_output_tokens']
        if (cap['limit'] != 20000 or cap['reached'] != (row['reason'] == 'output_cap')
                or row['structured_output_mode'] != 'json_schema'
                or sum(row['request_output_tokens']) != row['output_tokens']
                or max(row['request_output_tokens'], default=0) > 20000):
            raise ValueError('session diagnosis disagrees with the frozen cap or request usage')
    expected_groups = {(c['run_id'], a['attempt']) for c in cells for a in c['attempts']}
    if set(grouped) != expected_groups:
        raise ValueError('session diagnosis differs from retained cell attempts')
    for cell in cells:
        for attempt in cell['attempts']:
            members = grouped[cell['run_id'], attempt['attempt']]
            if sum(r['tokens'] for r in members) != attempt['tokens']:
                raise ValueError('session diagnosis usage differs from the cell attempt')
        final = grouped[cell['run_id'], len(cell['attempts'])]
        discovery = sorted((r['persona'], r['status'], r['reason'], r['tokens'])
                           for r in final if r['persona'] in ROSTER)
        retained = sorted((r['persona'], r['status'], r['reason'], r['tokens'])
                          for r in cell['session_statuses'])
        assessors = [r for r in final if r['persona'] == 'evidence-assessor']
        if (discovery != retained or len(assessors) != int(cell['assessor_actual'])
                or any((r['reason'], r['tokens'], r['status'] == 'complete') !=
                       (cell['assessor_reason'], cell['assessor_tokens'], cell['assessor_complete'])
                       for r in assessors)):
            raise ValueError('session diagnosis disagrees with the mechanical cell')
    by_cell = {c['run_id']: c for c in cells}

    def cause(row):
        return session_cause(row, by_cell, helper)
    failed = [r for r in rows if r['status'] != 'complete']
    corrections = correction_summary(rows, read(directory / 'first-answers.json')['sessions'])
    return {'sessions': len(rows), 'complete': len(rows) - len(failed),
            'not_complete_by_cause': dict(collections.Counter(cause(r) for r in failed)),
            'failed_sessions': [{'cell': r['cell'], 'cell_attempt': r.get('cell_attempt', 1),
                                 'persona': r['persona'], 'cause': cause(r),
                                 'reason': r['reason']} for r in failed],
            'requests': sum(len(r['request_output_tokens']) for r in rows),
            'constrained_turns_initial': sum(r['constrained_turns'] or 0 for r in rows),
            'requests_at_cap': sum(t == 20000 for r in rows for t in r['request_output_tokens']),
            'corrections': corrections}


def gate_losses(cells, gate, failed_sessions):
    bugs = set(gate['cases']) - set(gate['clean_cases'])
    result = []
    for cell in cells:
        case, arm, _ = cell['run_id'].split('/')
        reasons = sorted({r['cause'] for r in failed_sessions if r['cell'] == cell['run_id']})
        if cell['status'] != 'complete' and not reasons:
            reasons.append('cell_' + cell['status'])
        if not cell['accounting_complete']:
            reasons.append('accounting_incomplete')
        if len(cell['attempts']) > 1:
            reasons.append('needed_transient_rerun')
        if any(a['tokens'] > gate['max_tokens_per_run'] for a in cell['attempts']):
            reasons.append('attempt_over_ceiling')
        if case in bugs and arm == 'aggregate' and (
                cell['findings'] == 0 or cell['finding_validation_errors']):
            reasons.append('aggregate_empty_or_invalid_evidence')
        if case in bugs and arm == 'assessed' and (
                cell['assessment_status'] != 'complete' or not cell['assessor_complete']):
            reasons.append('assessor_not_run_or_incomplete')
        if reasons:
            result.append({'cell': cell['run_id'], 'causes': reasons})
    return result


def reconcile_usage(usage, sessions, provider):
    differences = {
        'request_difference': provider['evaluator_rows'] - sessions['requests'],
        'input_difference': provider['evaluator_prompt_tokens'] - usage['input'],
        'output_difference': provider['evaluator_completion_tokens'] - usage['output']}
    exact = all(value == 0 for value in differences.values())
    if not provider['evaluator_failed_rows'] and not exact:
        raise ValueError('serving usage differs from native requests/tokens '
                         'without provider failures')
    return {**differences, 'exact_match': exact,
            'provider_failed_rows': len(provider['evaluator_failed_rows'])}


def build(package, reports):
    helper = load_c1(reports)
    frozen_checks(package, reports)
    jobs, causes = {}, {}
    for label, directory in (('g3', package / 'g3'), ('c1', reports / C1 / 'c1'),
                             ('s2', reports / S2 / 's2')):
        _, jobs[label] = helper.summarize(directory, first_attempt_rule=True)
        if label != 'g3':
            causes[label] = helper.diagnosis(directory / 'session-diagnosis.json',
                read(directory / 'mechanical-qualification.json')['cells'],
                read(directory / 'evaluation.json'))
    causes['g3'] = sessions_summary(package / 'g3', helper)
    provider = provider_summary(read(package / 'provider-evidence.json'))
    smoke = read(package / 'smoke-summary.json')
    if (smoke['status'] != 'complete' or smoke['session_status'] != 'complete'
            or not read(package / 'smoke-verification.json')['verified']
            or provider['smoke']['evaluator_prompt_tokens'] != smoke['usage']['input']
            or provider['smoke']['evaluator_completion_tokens'] != smoke['usage']['output']):
        raise ValueError('smoke status or serving usage disagrees with retained provenance')
    return {'schema_version': 1, 'qualified': jobs['g3']['all_gate_tests_pass'],
            'defaults_promoted': False, 'actual_human_judgments': 0, 'holdout_used': False,
            'jobs': jobs, 'session_causes': causes, 'provider': provider,
            'spend_reconciliation': reconcile_usage(
                jobs['g3']['usage_all_attempts'], causes['g3'], provider['g3']),
            'gate_losses': gate_losses(
                read(package / 'g3/mechanical-qualification.json')['cells'],
                read(package / 'g3/gate.json'), causes['g3']['failed_sessions']),
            'side_by_side': {k: v['gate_items'] for k, v in jobs.items()},
            'comparison_limit': 'Different days/releases and concurrent load; '
                                'not a paired effect estimate.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--reports', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    package = args.package.resolve()
    reports = args.reports.resolve() if args.reports else package.parent
    helper = load_c1(reports)
    if not args.write:
        helper.verify_hashes(package)
    summary = build(package, reports)
    if args.write:
        (package / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    elif summary != read(package / 'summary.json'):
        raise ValueError('recomputed summary differs from the published one')
    args.output.write_text(json.dumps(summary, sort_keys=True) + '\n')
    print(json.dumps({'qualified': summary['qualified'], 'corrections':
                     summary['session_causes']['g3']['corrections']['offered']}))


if __name__ == '__main__':
    main()
