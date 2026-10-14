"""Recompute this package's gates, automated decisions and tables from public JSON.

No network, provider, credential or private capture is used. Authenticating the
normalized evidence against native sessions needs the retained private captures.
"""
import argparse
import collections
import hashlib
import json
import math
import re
from pathlib import Path

JOBS = ('reliability-r2', 'reliability-r3', 'discussion-untried', 'tools-silent', 'tools',
        'focused-wrong-candidates', 'focused-uncaptured', 'routing-a', 'routing-b',
        'line-counts')
# Proxy-advertised gpt-6.1-sol rates retained on 2026-10-13 (per token, US dollars).
INPUT_RATE, OUTPUT_RATE = 2e-06, 1e-05
SPEND_LIMIT_USD = 60.0
# Earlier schedules of the same model, ceiling, prompt and inputs, from the sibling
# packages named here: (cells, complete and accounted on the first attempt, gate passed).
EARLIER_SCHEDULES = {
    'sol100k': {'package': '2026-10-13-protocol/sol100', 'cells': 36, 'first_attempt': 30,
                'passed': False},
    'r1': {'package': '2026-10-13-qualification-rerun/qualification', 'cells': 36,
           'first_attempt': 36, 'passed': True},
}
# One labeled defect per case: (defect id, changed file, admissible new-side lines,
# keyword patterns, text scope). They are keyword filters, not a semantic judge: a
# differently worded wrong finding on the same lines could match, and a correct one
# in other words stays unmatched. Anything unmatched is left pending for a person and
# is never counted as a false positive.
#
# EARLIER_RULES are copied unchanged from ../2026-10-13-qualification-rerun/reproduce.py,
# where they were written after that package's outputs existed. Outputs of this
# package's jobs on those cases were read before this file was frozen, so every
# judgment made with them is post hoc.
EARLIER_RULES = {
    'security-auth-order': ('security-auth-order-defect', 'api.py', (5,), (
        r'(clear|delet)\w*.*before.*(authori|permi|ownership)',
        r'(authori|permi)\w*.*before.*clear'), 'problem'),
    'tests-consumer-fixture': ('tests-consumer-fixture-defect', 'formatting.py', (2,), (
        r'label.*(test|contract)',), 'problem'),
    'dev2-01': ('dev2-01-defect', 'src/concurrency/counter.py', (11,), (
        r'outside the lock.*(los|overwrit)', r'overwrit.*outside the lock'), 'problem'),
    'dev2-06': ('dev2-06-defect', 'src/ledger_transaction.py', (16, 17), (
        r'commit.*atomic',), 'problem'),
}
# FROZEN_RULES were written from the development-v3 labels and fixture sources alone
# (trigger, impact, evidence, match_criteria and the diff hunk of the changed file),
# before any model output of a development-v3 job was read, and committed in that
# state; frozen-rules.json records the hash. Admissible lines are the new-side range
# of the labeled file's diff hunks. The text scope is the problem statement and reason.
FROZEN_RULES = {
    'dev3-01': ('dev3-01-defect', 'src/reporting/formatters.py', tuple(range(111, 124)), (
        r'redefin', r'shadow', r'overrid', r'overwrit', r'same name', r'duplicate (def|func|name)',
        r'replac\w* the (existing|earlier|original|first)', r'typeerror',
        r'(existing|earlier|original|other) callers?', r'(one|single)[- ]argument'), 'both'),
    'dev3-02': ('dev3-02-defect', 'src/client/http_settings.py',
                (*range(1, 7), *range(10, 17)), (
        r'nameerror', r'upload_options', r'large upload',
        r'default_timeout\b(?!_).*(undefined|not defined|no longer|stale|still|remain)',
        r'(remaining|stale|old|leftover) (reference|use|name)'), 'both'),
    'dev3-04': ('dev3-04-defect', 'src/listing/pager.py', tuple(range(2, 8)), (
        r'off[- ]by[- ]one', r'(1|one)[- ]based', r'first page', r'skip\w* the first',
        r'page - 1', r'zero[- ]based|0[- ]based'), 'both'),
    'dev3-08': ('dev3-08-defect', 'src/render/page_cache.py', tuple(range(7, 16)), (
        r'currenc\w*.*(key|cache)', r'(key|cache)\w*.*currenc'), 'both'),
    'dev3-09': ('dev3-09-defect', 'src/concurrency/account_locks.py', tuple(range(6, 15)), (
        r'deadlock', r'lock[- ]order', r'order\w* (of|in which) (the )?(two )?locks',
        r'consistent order', r'opposite (direction|transfer)'), 'both'),
    'dev3-10': ('dev3-10-defect', 'src/migrations/0011_unique_email.py', tuple(range(2, 8)), (
        r'duplicate', r'differ\w* only (by|in) case', r'case[- ](variant|insensitive)',
        r'(existing|legacy|older|earlier) (rows|accounts|addresses|data)'), 'both'),
    'dev3-11': ('dev3-11-defect', 'src/inventory/stock.py', tuple(range(6, 14)), (
        r'outofstock', r'no longer rais', r'oversell|oversold|over-sell',
        r'(ignor|discard)\w* the (return|result)', r'fewer (units|items)',
        r'partial\w* reserv'), 'both'),
}
RULES = {**EARLIER_RULES, **FROZEN_RULES}
# Clean cases: frozen keyword filters for the labeled tempting allegation, written from
# the labels alone at the same time. A match says only that the finding states that
# allegation; it is never scored as a false positive here.
ALLEGATIONS = {
    'dev3-03': (r'unused', r'never used', r'not used', r'remov\w* .*import'),
    'dev3-05': (r'timing', r'constant[- ]time', r'compare_digest', r'side[- ]channel'),
    'dev3-06': (r'without (the |a )?lock', r'unlocked', r'unsynchroni', r'\brace', r'data race',
                r'thread[- ]safe'),
    'dev3-07': (r'atomic', r'partial', r'transaction', r'interrupt', r'crash', r'half'),
    'dev3-12': (r'keyerror', r'blank', r'empty', r'missing (value|key|placeholder)',
                r'silent'),
}
# Findings that name tool evidence in their own text.
TOOL_MENTION = re.compile(r'\bruff\b|\bF(401|811|821)\b|static[- ]analysis|\blint', re.I)
UNCAPTURED = ('dev3-11', 'dev3-12')


def read(path):
    return json.loads(path.read_text())


def finding_id(run, index):
    """Same identity as scrutare.evaluation.scoring.finding_id."""
    value = [run['run_id'], index, run['findings'][index]]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def text_of(finding):
    return (finding['problem'] + ' ' + finding.get('reason', '')).lower()


def decide(run, index, rules=None):
    """Return the matched defect id, or None when no rule applies (left pending)."""
    finding = run['findings'][index]
    rule = (RULES if rules is None else rules).get(run['case_id'])
    if rule is None:
        return None
    defect, path, lines, patterns, scope = rule
    text = text_of(finding) if scope == 'both' else finding['problem'].lower()
    if (finding['file'] == path and finding['side'] == 'RIGHT' and finding['line'] in lines
            and any(re.search(pattern, text) for pattern in patterns)):
        return defect
    return None


def rules_sha256():
    """Identity of the frozen development-v3 rules and allegation filters."""
    frozen = {'rules': {k: list(v) for k, v in sorted(FROZEN_RULES.items())},
              'allegations': {k: list(v) for k, v in sorted(ALLEGATIONS.items())}}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


def states_allegation(case, finding):
    patterns = ALLEGATIONS.get(case, ())
    return any(re.search(pattern, text_of(finding)) for pattern in patterns)


def decisions(package):
    rows = []
    for job in JOBS:
        for run in read(package / job / 'experiment.json')['runs']:
            for index in range(len(run['findings'])):
                defect = decide(run, index)
                if defect is not None:
                    rows.append({'job': job, 'finding_id': finding_id(run, index),
                                 'defect_id': defect, 'reviewer': 'reproduce.py RULES',
                                 'method': 'automated', 'ambiguous': False,
                                 'rationale': 'anchored on the labeled changed line; '
                                              'finding text passes the keyword filter'})
    return rows


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def nearest_rank(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)] if ordered else None


def usd(usage):
    return ((usage['input'] + usage['cache_read'] + usage['cache_write']) * INPUT_RATE
            + usage['output'] * OUTPUT_RATE)


def gate_tests(package, job):
    gate = read(package / job / 'gate.json')
    experiment = read(package / job / 'experiment.json')
    cells = read(package / job / 'mechanical-qualification.json')['cells']
    expected = {f'{case}/{arm}/{repeat}' for case in gate['cases'] for arm in gate['arms']
                for repeat in range(1, gate['repeats'] + 1)}
    runs = {row['run_id']: row for row in experiment['runs']}
    by_cell = {cell['run_id']: cell for cell in cells}
    if len(experiment['runs']) != len(runs) or len(cells) != len(by_cell):
        raise ValueError('duplicate run identity')
    for identity, cell in by_cell.items():
        row = runs[identity]
        if (cell['status'] != row['status'] or cell['tokens'] != row['usage']['total']
                or cell['accounting_complete'] != row['accounting_complete']
                or cell['findings'] != len(row['findings'])
                or len(cell['attempts']) != len(row.get('attempts') or [1])
                or cell['retry_tokens'] != (row.get('retry_usage') or {}).get('total', 0)):
            raise ValueError('normalized cell disagrees with public experiment')
    bugs = set(gate['cases']) - set(gate['clean_cases'])

    def arm(name):
        return [c for c in cells if c['run_id'].split('/')[0] in bugs
                and c['run_id'].split('/')[1] == name]
    policy = gate['transient_retry']
    tests = {
        'matrix_exact': set(runs) == expected == set(by_cell)
        and len(expected) == gate['expected_runs'],
        'experiment_terminal': experiment['status'] == 'complete',
        'all_runs_complete_and_accounted': all(
            c['status'] == 'complete' and c['accounting_complete'] for c in cells),
        'all_attempts_within_ceiling': all(a['tokens'] <= gate['max_tokens_per_run']
                                           for c in cells for a in c['attempts']),
        'retries_within_declared_policy': all(
            len(c['attempts']) - 1 <= policy['per_cell'] and all(
                a['transient_provider_failures'] for a in c['attempts'][:-1])
            for c in cells) and sum(len(c['attempts']) - 1 for c in cells) <= policy[
                'job_budget']}
    if 'aggregate_defect_case_repeats_nonempty_valid_v2' in gate:
        rows = arm('aggregate')
        tests['aggregate_bug_cells_nonempty_valid_v2'] = (
            len(rows) == gate['aggregate_defect_case_repeats_nonempty_valid_v2'] and all(
                c['findings'] > 0 and not c['finding_validation_errors'] for c in rows))
    if 'assessed_defect_case_repeats_actual_complete_assessor' in gate:
        rows = arm('assessed')
        tests['assessed_bug_cells_actual_complete_assessor'] = (
            len(rows) == gate['assessed_defect_case_repeats_actual_complete_assessor'] and all(
                c['assessment_status'] == 'complete' and c['assessor_complete'] for c in rows))
    return gate, tests, cells, runs


def summarize(package, job, matched):
    gate, tests, cells, runs = gate_tests(package, job)
    retained = read(package / job / 'mechanical-qualification.json')
    if tests != retained['tests']:
        raise ValueError('recomputed gate disagrees with retained mechanical summary')
    clean = set(gate['clean_cases'])
    arms = {}
    for name in gate['arms']:
        selected = [c for c in cells if c['run_id'].split('/')[1] == name]
        counts = collections.Counter()
        dispositions = collections.Counter()
        final = collections.Counter()
        per_case = {}
        for cell in selected:
            case = cell['run_id'].split('/')[0]
            run = runs[cell['run_id']]
            ids = [finding_id(run, i) for i in range(len(run['findings']))]
            hit = sum(identity in matched for identity in ids)
            alleged = sum(states_allegation(case, f) for f in run['findings'])
            usable = cell['status'] == 'complete' and cell['accounting_complete']
            refused = [r for r in cell['citation_refusals']]
            counts['matched_findings'] += hit
            counts['unmatched_findings'] += len(ids) - hit
            counts['sessions'] += len(cell['session_statuses'])
            counts['sessions_refused_for_citation'] += len(refused)
            counts['cells_lost_to_citation'] += bool(refused and cell['status'] != 'complete')
            counts['tool_mentions'] += sum(bool(TOOL_MENTION.search(text_of(f)))
                                           for f in run['findings'])
            counts['discovery_findings'] += cell['discovery_findings']
            counts['candidates'] += len(cell['candidates'])
            dispositions.update(cell['initial_dispositions'].values())
            final.update(cell['final_dispositions'].values())
            row = per_case.setdefault(case, {
                'cells': 0, 'complete': 0, 'detected': 0, 'cells_with_findings': 0,
                'findings': 0, 'findings_stating_tempting_allegation': 0,
                'discovery_findings': 0, 'candidates': 0, 'initial_dispositions': {},
                'final_dispositions': {}, 'discussion_attempted_rounds': 0,
                'discussion_completed_rounds': 0, 'sessions_refused_for_citation': 0,
                'tokens': [], 'latency_seconds': [], 'reviewers': set(),
                'specialists': set(), 'analysis_rules': None})
            row['cells'] += 1
            row['complete'] += usable
            row['detected'] += bool(usable and hit)
            row['cells_with_findings'] += bool(usable and ids)
            row['findings'] += len(ids)
            row['findings_stating_tempting_allegation'] += alleged
            row['discovery_findings'] += cell['discovery_findings']
            row['candidates'] += len(cell['candidates'])
            for key, source in (('initial_dispositions', cell['initial_dispositions']),
                                ('final_dispositions', cell['final_dispositions'])):
                for status in source.values():
                    row[key][status] = row[key].get(status, 0) + 1
            row['discussion_attempted_rounds'] += cell['discussion_attempted_rounds']
            row['discussion_completed_rounds'] += cell['discussion_completed_rounds']
            row['sessions_refused_for_citation'] += len(refused)
            row['tokens'].append(cell['tokens'] + cell['retry_tokens'])
            row['latency_seconds'].append(round(cell['latency_seconds'], 3))
            row['reviewers'].add(len(cell['effective_personas']))
            row['specialists'].update(cell['routing_activated'])
            if cell['analysis_rules'] is not None:
                row['analysis_rules'] = cell['analysis_rules']
            if case in clean:
                counts['clean_cells'] += 1
                counts['clean_cells_usable'] += usable
                counts['clean_cells_with_findings'] += bool(usable and ids)
            else:
                counts['defect_cells'] += 1
                counts['defect_cells_detected'] += bool(usable and hit)
        for row in per_case.values():
            row['reviewers'] = sorted(row['reviewers'])
            row['specialists'] = sorted(row['specialists'])
            row['mean_tokens'] = round(sum(row['tokens']) / len(row['tokens']), 1)
            row['mean_latency_seconds'] = round(
                sum(row['latency_seconds']) / len(row['latency_seconds']), 2)
        tokens = [c['tokens'] + c['retry_tokens'] for c in selected]
        latency = [c['latency_seconds'] for c in selected]
        failed = sum(c['status'] != 'complete' for c in selected)
        usage = collections.Counter()
        for cell in selected:
            usage.update({k: v for k, v in cell['usage'].items() if k != 'total'})
        arms[name] = {
            'cells': len(selected), 'complete_and_accounted': sum(
                c['status'] == 'complete' and c['accounting_complete'] for c in selected),
            'not_complete': failed, 'not_complete_rate_wilson95': wilson(failed, len(selected)),
            'not_complete_reasons': dict(collections.Counter(
                s['reason'] for c in selected if c['status'] != 'complete'
                for s in c['session_statuses'] if s['status'] != 'complete')),
            'cells_needing_rerun': sum(len(c['attempts']) > 1 for c in selected),
            'sessions': counts['sessions'],
            'sessions_refused_for_citation': counts['sessions_refused_for_citation'],
            'cells_lost_to_citation': counts['cells_lost_to_citation'],
            'findings': sum(c['findings'] for c in selected),
            'automated_matched_findings': counts['matched_findings'],
            'unmatched_findings': counts['unmatched_findings'],
            'findings_naming_tool_evidence': counts['tool_mentions'],
            'discovery_findings': counts['discovery_findings'],
            'assessed_candidates': counts['candidates'],
            'initial_dispositions': dict(dispositions),
            'final_dispositions': dict(final),
            'defect_cells': counts['defect_cells'],
            'defect_cells_detected': counts['defect_cells_detected'],
            'provisional_defect_recall': (round(counts['defect_cells_detected']
                                                / counts['defect_cells'], 4)
                                          if counts['defect_cells'] else None),
            'provisional_defect_recall_wilson95': wilson(counts['defect_cells_detected'],
                                                         counts['defect_cells']),
            'clean_cells': counts['clean_cells'],
            'clean_cells_with_findings': counts['clean_cells_with_findings'],
            'clean_cells_with_findings_wilson95': wilson(counts['clean_cells_with_findings'],
                                                         counts['clean_cells_usable']),
            'total_tokens': sum(tokens), 'mean_tokens': round(sum(tokens) / len(tokens), 1),
            'max_attempt_tokens': max(a['tokens'] for c in selected for a in c['attempts']),
            'usage_final_attempts': dict(usage),
            'mean_latency_seconds': round(sum(latency) / len(latency), 2),
            'p95_latency_seconds_nearest_rank': round(nearest_rank(latency, 0.95), 2),
            'actual_assessor_calls': sum(c['assessor_actual'] for c in selected),
            'complete_assessor_calls': sum(c['assessor_complete'] for c in selected),
            'discussion_attempted_rounds': sum(c['discussion_attempted_rounds']
                                               for c in selected),
            'discussion_completed_rounds': sum(c['discussion_completed_rounds']
                                               for c in selected),
            'cells_with_specialist': sum(bool(c['routing_activated']) for c in selected),
            'per_case': per_case}
    measured = 0.0
    for row in runs.values():
        measured += usd(row['usage'])
        if row.get('retry_usage'):
            measured += usd(row['retry_usage'])
    attempts = sum(len(c['attempts']) for c in cells)
    return {
        'job': job, 'profile': gate['profile'], 'tests': tests,
        'all_gate_tests_pass': all(tests.values()),
        'status_counts': dict(collections.Counter(c['status'] for c in cells)),
        'first_attempt_status_counts': dict(collections.Counter(
            c['first_attempt_status'] for c in cells)),
        'first_attempt_complete_and_accounted': sum(
            c['attempts'][0]['status'] == 'complete' and c['attempts'][0]['accounting_complete']
            for c in cells),
        'cells': len(cells), 'attempts': attempts, 'rerun_attempts': attempts - len(cells),
        'transient_http_statuses': dict(collections.Counter(
            str(f['http_status']) for c in cells for a in c['attempts']
            for f in (a['transient_provider_failures'] or []))),
        'arms': arms,
        'total_tokens': sum(c['tokens'] + c['retry_tokens'] for c in cells),
        'nominal_usd': round(attempts * gate['max_tokens_per_run'] * OUTPUT_RATE, 2),
        'measured_usd': round(measured, 4)}


def paired(summary, baseline, candidate):
    """Case-level differences between two arms of one job; means over three repeats."""
    rows = {}
    for case, base in summary['arms'][baseline]['per_case'].items():
        other = summary['arms'][candidate]['per_case'][case]
        rows[case] = {
            'detected': {baseline: f"{base['detected']}/{base['cells']}",
                         candidate: f"{other['detected']}/{other['cells']}"},
            'cells_with_findings': {baseline: base['cells_with_findings'],
                                    candidate: other['cells_with_findings']},
            'complete': {baseline: base['complete'], candidate: other['complete']},
            'mean_tokens': {baseline: base['mean_tokens'], candidate: other['mean_tokens']},
            'token_ratio': round(other['mean_tokens'] / base['mean_tokens'], 3),
            'mean_latency_seconds': {baseline: base['mean_latency_seconds'],
                                     candidate: other['mean_latency_seconds']},
            'latency_ratio': round(other['mean_latency_seconds']
                                   / base['mean_latency_seconds'], 3),
            'specialists': other['specialists']}
    ratios = [row['token_ratio'] for row in rows.values()]
    return {'job': summary['job'], 'baseline': baseline, 'candidate': candidate, 'cases': rows,
            'token_ratio_range': [min(ratios), max(ratios)],
            'detection_difference_cells': sum(
                int(r['detected'][candidate].split('/')[0])
                - int(r['detected'][baseline].split('/')[0]) for r in rows.values())}


def reliability(summaries):
    """Completion of the unchanged profile over every schedule of it that has run."""
    schedules = {name: {'cells': row['cells'], 'first_attempt': row['first_attempt'],
                        'passed': row['passed'], 'source': row['package']}
                 for name, row in EARLIER_SCHEDULES.items()}
    for name, job in (('r2', 'reliability-r2'), ('r3', 'reliability-r3')):
        summary = summaries[job]
        schedules[name] = {'cells': summary['cells'],
                           'first_attempt': summary['first_attempt_complete_and_accounted'],
                           'passed': summary['all_gate_tests_pass'], 'source': job,
                           'rerun_attempts': summary['rerun_attempts']}
    passed = sum(row['passed'] for row in schedules.values())
    cells = sum(row['cells'] for row in schedules.values())
    first = sum(row['first_attempt'] for row in schedules.values())
    return {'schedules': schedules,
            'schedules_passed': passed, 'schedules_run': len(schedules),
            'schedule_pass_rate': round(passed / len(schedules), 4),
            'schedule_pass_rate_wilson95': wilson(passed, len(schedules)),
            'cells': cells, 'cells_complete_on_first_attempt': first,
            'cell_first_attempt_rate': round(first / cells, 4),
            'cell_first_attempt_rate_wilson95': wilson(first, cells),
            'independence_caveat': 'cells within one schedule share an upstream window; all '
                                   'six losses are in one schedule, so the cell interval '
                                   'understates the uncertainty'}


def citations(summaries, jobs):
    sessions = sum(a['sessions'] for j in jobs for a in summaries[j]['arms'].values())
    refused = sum(a['sessions_refused_for_citation'] for j in jobs
                  for a in summaries[j]['arms'].values())
    cells = sum(a['cells'] for j in jobs for a in summaries[j]['arms'].values())
    lost = sum(a['cells_lost_to_citation'] for j in jobs for a in summaries[j]['arms'].values())
    return {'jobs': list(jobs), 'sessions': sessions, 'sessions_refused': refused,
            'sessions_refused_wilson95': wilson(refused, sessions),
            'cells': cells, 'cells_lost': lost, 'cells_lost_wilson95': wilson(lost, cells)}


def line_count_comparison(summaries):
    """Same cases and arms with the setting off (routing-a, routing-b) and on (line-counts)."""
    def side(jobs):
        rows = [summaries[j]['arms'][a] for j in jobs for a in ('evidence', 'routing')]
        sessions = sum(r['sessions'] for r in rows)
        refused = sum(r['sessions_refused_for_citation'] for r in rows)
        cells = sum(r['cells'] for r in rows)
        lost = sum(r['cells_lost_to_citation'] for r in rows)
        return {'cells': cells, 'cells_lost_to_citation': lost,
                'cells_lost_wilson95': wilson(lost, cells), 'sessions': sessions,
                'sessions_refused_for_citation': refused,
                'sessions_refused_wilson95': wilson(refused, sessions),
                'mean_tokens': round(sum(r['total_tokens'] for r in rows) / cells, 1)}
    return {'off': side(('routing-a', 'routing-b')), 'on': side(('line-counts',))}


def build(package):
    rows = decisions(package)
    matched = {row['finding_id'] for row in rows}
    summaries = {job: summarize(package, job, matched) for job in JOBS}
    off = tuple(job for job in JOBS if job != 'line-counts')
    measured = round(sum(s['measured_usd'] for s in summaries.values()), 2)
    return rows, {
        'schema_version': 1, 'judgments': 'automated keyword filters, provisional',
        'actual_human_judgments': 0, 'holdout_used': False, 'defaults_promoted': False,
        'automated_decisions': len(rows),
        'unmatched_findings': sum(a['unmatched_findings'] for s in summaries.values()
                                  for a in s['arms'].values()),
        'jobs': summaries,
        'reliability': reliability(summaries),
        'citation_refusals': {'setting_off': citations(summaries, off),
                              'setting_on': citations(summaries, ('line-counts',)),
                              'paired': line_count_comparison(summaries)},
        'discussion': {
            'attempted_rounds': sum(a['discussion_attempted_rounds'] for s in summaries.values()
                                    for a in s['arms'].values()),
            'completed_rounds': sum(a['discussion_completed_rounds'] for s in summaries.values()
                                    for a in s['arms'].values()),
            'focused_cells': sum(a['cells'] for s in summaries.values()
                                 for n, a in s['arms'].items() if n.startswith('focused'))},
        'paired': {
            'tools_vs_assessment': paired(summaries['tools'], 'assessment', 'tools'),
            'tools_vs_assessment_silent': paired(summaries['tools-silent'], 'assessment',
                                                 'tools'),
            'assessment_vs_aggregate_wrong_candidates': paired(
                summaries['focused-wrong-candidates'], 'aggregate', 'assessed'),
            'one_round_vs_assessment_wrong_candidates': paired(
                summaries['focused-wrong-candidates'], 'assessed', 'focused1'),
            'three_rounds_vs_one_wrong_candidates': paired(
                summaries['focused-wrong-candidates'], 'focused1', 'focused3'),
            'assessment_vs_aggregate_uncaptured': paired(
                summaries['focused-uncaptured'], 'aggregate', 'assessed'),
            'one_round_vs_assessment_uncaptured': paired(
                summaries['focused-uncaptured'], 'assessed', 'focused1'),
            'three_rounds_vs_one_uncaptured': paired(
                summaries['focused-uncaptured'], 'focused1', 'focused3'),
            'assessment_vs_aggregate_untried': paired(
                summaries['discussion-untried'], 'aggregate', 'assessed'),
            'routing_vs_fixed_a': paired(summaries['routing-a'], 'evidence', 'routing'),
            'routing_vs_fixed_b': paired(summaries['routing-b'], 'evidence', 'routing'),
            'assessment_vs_evidence_a': paired(summaries['routing-a'], 'evidence', 'assessment'),
            'assessment_vs_evidence_b': paired(summaries['routing-b'], 'evidence',
                                               'assessment')},
        'spend': {
            'limit_usd': SPEND_LIMIT_USD,
            'measured_method': 'reported tokens of every attempt at the advertised input and '
                               'output rates, cached input charged as uncached',
            'measured_usd_by_job': {j: s['measured_usd'] for j, s in summaries.items()},
            'measured_usd': measured,
            'nominal_method': 'attempts x configured ceiling x advertised output rate',
            'nominal_usd': round(sum(s['nominal_usd'] for s in summaries.values()), 2),
            'total_tokens': sum(s['total_tokens'] for s in summaries.values()),
            'within_limit': measured <= SPEND_LIMIT_USD,
            'note': 'no invoice or billed amount is asserted'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = args.package
    for name, expected in read(package / 'published-sha256.json').items():
        path = package / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'public artifact hash mismatch: {name}')
    rows, summary = build(package)
    if rows != read(package / 'automated-decisions.json'):
        raise ValueError('recomputed automated decisions differ from the published ones')
    if summary != read(package / 'summary.json'):
        raise ValueError('recomputed summary differs from the published one')
    args.output.write_text(json.dumps(summary, sort_keys=True, separators=(',', ':')) + '\n')
    print(json.dumps({'verified_artifacts': len(read(package / 'published-sha256.json')),
                      'automated_decisions': len(rows),
                      'schedules_passed': summary['reliability']['schedules_passed'],
                      'measured_usd': summary['spend']['measured_usd']}))


if __name__ == '__main__':
    main()
