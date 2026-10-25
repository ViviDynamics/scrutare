"""Recompute the #91 citation-correction measurement from this package's public files.

Standard library only, no network. Fails on any difference from summary.json. It checks
every published hash and refuses files outside the manifest; requires the three jobs to
differ only in the settings under test; checks each session diagnosis against its job's
cells and frozen rail; and recomputes the cell, session-cause, citation-defect and correction
tables and the token cost.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

JOBS = ('base', 'lines', 'lines-correct')
ROSTER = ('senior-dev', 'junior-dev', 'security', 'devops')
FILES = ('preparation.json', 'evaluation.json', 'experiment.json', 'snapshot.json',
         'corpus-manifest.json', 'original-artifact-sha256.json', 'session-diagnosis.json',
         'first-answers.json')


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_hashes(package):
    published = read(package / 'published-sha256.json')
    on_disk = {p.relative_to(package).as_posix() for p in package.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    if on_disk - {'published-sha256.json'} != set(published):
        raise ValueError('package files differ from published-sha256.json')
    for name, digest in published.items():
        if sha(package / name) != digest:
            raise ValueError(f'hash mismatch: {name}')


def check_jobs(package):
    plan = read(package / 'plan.json')
    evaluations = {}
    for job in JOBS:
        directory = package / job
        prep = read(directory / 'preparation.json')
        if (prep['label'] != job or prep['holdout_used'] is not False
                or prep['model_alias'] != 'onprem/glm-5.3-flash' or prep['split'] != 'development'
                or prep['input_sha256']['evaluation.json'] != sha(directory / 'evaluation.json')
                or prep['input_sha256']['plan.json'] != sha(package / 'plan.json')
                or prep['cases'] != plan['cases'] or prep['variants'] != plan['variants']
                or prep['expected_runs'] != plan['expected_runs_per_job']):
            raise ValueError(f'{job}: preparation disagrees with the plan or its inputs')
        evaluations[job] = read(directory / 'evaluation.json')
    base = evaluations['base']
    lines = json.loads(json.dumps(base))
    lines['context']['line_counts'] = True
    correct = json.loads(json.dumps(lines))
    correct['findings'] = {'evidence': 'v2', 'citation_correction': True}
    if evaluations['lines'] != lines or evaluations['lines-correct'] != correct:
        raise ValueError('the jobs differ in more than the settings under test')
    if 'line_counts' in base['context'] or 'findings' in base:
        raise ValueError('base must have neither setting')
    # Same source, wheel, corpus bytes and runtime: the preparations are equal once the
    # label, the two flags and the evaluation hash are set aside, and the flags match.
    def comparable(prep):
        rest = {k: v for k, v in prep.items()
                if k not in ('label', 'context_line_counts', 'citation_correction')}
        rest['input_sha256'] = {k: v for k, v in prep['input_sha256'].items()
                                if k != 'evaluation.json'}
        return rest
    preps = {job: read(package / job / 'preparation.json') for job in JOBS}
    if len({json.dumps(comparable(p), sort_keys=True) for p in preps.values()}) != 1:
        raise ValueError('the jobs ran different sources, inputs or runtimes')
    for job, prep in preps.items():
        if (prep['context_line_counts'] != ('line_counts' in evaluations[job]['context'])
                or prep['citation_correction'] != ('findings' in evaluations[job])):
            raise ValueError(f'{job}: preparation flags disagree with its evaluation')
    return plan, evaluations


def cause(row):
    """One cause per discovery session that did not complete, from recorded reasons only."""
    error = row['nare_error'] or ''
    if row['reason'] == 'timeout':
        return 'session_timeout'
    if row['reason'] == 'output_cap':
        return 'output_cap'
    if row['reason'] == 'provider' and 'repeating the same chunk' in error:
        return 'provider_aborted_repetition'
    if row['reason'] == 'citation_validation':
        return 'citation:' + (row['citation_validation_reason'] or 'unknown')
    if row['reason'] == 'protocol' and row['first_answer'] not in (None, 'valid'):
        return 'answer_evidence_refused'
    if row['reason'] == 'budget':
        return 'budget_overrun'
    if error.startswith('answer does not satisfy the schema'):
        return 'schema_failure'
    return f"other:{row['reason']}"


def wilson(k, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def job_summary(package, job, evaluation):
    directory = package / job
    experiment = read(directory / 'experiment.json')
    runs = experiment['runs']
    if experiment['status'] != 'complete' or len(runs) != 12:
        raise ValueError(f'{job}: incomplete experiment')
    cells = {r['run_id']: r for r in runs}
    rows = read(directory / 'session-diagnosis.json')['sessions']
    listed = read(directory / 'first-answers.json')['sessions']
    answers = {a['session']: a for a in listed}
    if len(answers) != len(listed):
        raise ValueError(f'{job}: duplicated first-answer entries')
    cap = evaluation['models']['default']['max_output_tokens']
    mode = evaluation['models']['default']['structured_output']
    discovery = []
    for row in rows:
        if row['cell'] not in cells or row['attempt'] != 'attempt-0001':
            raise ValueError(f'{job}: session outside the cells: {row["cell"]}')
        if row['structured_output_mode'] != mode or row['max_output_tokens']['limit'] != cap:
            raise ValueError(f'{job}: session off the frozen rail')
        if row['persona'] not in ROSTER:
            raise ValueError(f'{job}: unexpected session {row["persona"]}')
        key = f"{row['cell']}/sessions/{row['persona']}/{row['attempt']}"
        record = row['citation_correction']
        if (record is None) != (job != 'lines-correct'):
            raise ValueError(f'{job}: correction record where the setting is off, or missing')
        answer = answers.get(key)
        if answer is None or (answer['status'], answer['reason']) != (row['status'],
                                                                       row['reason']):
            raise ValueError(f'{job}: session without a matching first-answer entry {key}')
        first = answer.get('first_answer')
        if (first is None) != (row['nare_status'] != 'done'):
            raise ValueError(f'{job}: first answer classified for an unfinished session {key}')
        corrected = record is not None and record['outcome'] == 'corrected'
        offered = record is not None and record['offered']
        refused = first not in (None, 'valid')
        correctable = answer.get('correctable') is not None
        # A citation refusal: the validator refused a citation, or a malformed citation field
        # (a protocol refusal) that the released diagnoser names as correctable.
        citation_refused = (first or '').startswith('citation:') or (refused and correctable)
        if correctable and not refused:
            raise ValueError(f'{job}: correctable defects on a valid first answer {key}')
        if record is not None and offered != (refused and correctable):
            raise ValueError(f'{job}: a correction offered where none was owed, or missing {key}')
        if offered and record['outcome'] == 'corrected' and row['tokens'] <= (
                row['first_run_tokens'] or 0):
            raise ValueError(f'{job}: a corrected session with no correction tokens {key}')
        if record is None and row['first_run_tokens'] is not None and (
                row['tokens'] != row['first_run_tokens']):
            raise ValueError(f'{job}: session tokens beyond its own run with the setting off {key}')
        # Every recorded outcome must agree with how the first answer was classified.
        if ((offered and not refused)
                or (row['status'] == 'complete' and refused and not corrected)
                or (row['reason'] == 'citation_validation' and not offered
                    and first != 'citation:' + str(row['citation_validation_reason']))
                or (refused and not offered and row['status'] == 'complete')
                or (first == 'valid' and row['reason'] in ('citation_validation', 'protocol'))):
            raise ValueError(f'{job}: recorded outcome disagrees with the first answer {key}')
        correction_tokens = (row['tokens'] - row['first_run_tokens']
                             if record is not None and record['offered'] else 0)
        if correction_tokens < 0 or (record is not None and not record['offered']
                                     and row['tokens'] != row['first_run_tokens']
                                     and row['first_run_tokens'] is not None):
            raise ValueError(f'{job}: correction tokens disagree {key}')
        discovery.append({**row, 'first_answer': first, 'citation_refused': citation_refused,
                          'correctable': answer.get('correctable') if answer else None,
                          'correction_tokens': correction_tokens, 'key': key})
    if len(discovery) != 48 or len(answers) != 48:
        raise ValueError(f'{job}: expected 48 discovery sessions, found {len(discovery)}')
    per_cell = collections.defaultdict(list)
    for row in discovery:
        per_cell[row['cell']].append(row)
    for cell, members in per_cell.items():
        if sorted(r['persona'] for r in members) != sorted(ROSTER):
            raise ValueError(f'{job}: cell {cell} does not hold the four reviewers once each')
        if cells[cell]['usage']['total'] != sum(r['tokens'] for r in members):
            raise ValueError(f'{job}: cell {cell} usage disagrees with its sessions')
        if (cells[cell]['status'] == 'complete') != all(r['status'] == 'complete'
                                                        for r in members):
            raise ValueError(f'{job}: cell {cell} status disagrees with its sessions')
    # The frozen snapshot must schedule exactly this arm with exactly this job's settings.
    snapshot = read(directory / 'snapshot.json')
    configs = snapshot['configs']
    arm = configs.get('aggregate') if set(configs) == {'aggregate'} else None
    if (arm is None or sorted(r['run_id'] for r in snapshot['expected_runs']) != sorted(cells)
            or arm.get('context', {}).get('line_counts', False)
            != evaluation['context'].get('line_counts', False)
            or arm.get('findings', {}).get('citation_correction', False)
            != ('findings' in evaluation)
            or {k: v for k, v in arm['models']['default'].items() if v is not None}
            != evaluation['models']['default']):
        raise ValueError(f'{job}: snapshot disagrees with the job')
    manifest = read(directory / 'corpus-manifest.json')
    prepared = read(directory / 'preparation.json')['cases']
    if sorted(c['id'] for c in manifest['cases']) != sorted(prepared):
        raise ValueError(f'{job}: corpus manifest names other cases')
    complete = [r for r in discovery if r['status'] == 'complete']
    causes = collections.Counter(cause(r) for r in discovery if r['status'] != 'complete')
    refused_first = [r for r in discovery if r['citation_refused']]
    answered = [r for r in discovery if r['first_answer'] is not None]
    corrections = collections.Counter(
        r['citation_correction']['outcome'] for r in discovery
        if r['citation_correction'] and r['citation_correction']['offered'])
    cell_complete = [r for r in runs if r['status'] == 'complete' and r['accounting_complete']]
    tokens = sum(r['usage']['total'] for r in runs)
    session_tokens = sum(r['tokens'] for r in discovery)
    return {
        'cells': len(runs), 'cells_complete_and_accounted': len(cell_complete),
        'discovery_sessions': len(discovery), 'discovery_sessions_complete': len(complete),
        'not_complete_by_cause': dict(sorted(causes.items())),
        'first_answers_refused_for_citations': {
            'sessions': len(refused_first), 'answered_sessions': len(answered),
            'wilson_95': wilson(len(refused_first), len(answered)),
            'by_kind': dict(sorted(collections.Counter(
                r['first_answer'] for r in refused_first).items())),
            'correctable': sum(1 for r in refused_first if r['correctable'] is not None),
            'sessions_list': sorted(r['key'] for r in refused_first)},
        'corrections': {'offered': sum(corrections.values()),
                        'by_outcome': dict(sorted(corrections.items())),
                        'tokens': sum(r['correction_tokens'] for r in discovery)},
        'tokens': {'cells_total': tokens, 'discovery_sessions_total': session_tokens,
                   'output': sum(r['usage']['output'] for r in runs)},
        'mean_cell_latency_seconds': round(sum(r['latency_seconds'] for r in runs) / len(runs), 1),
        # Citations that passed validation and reached a cell's findings: a lower bound on the
        # chances to cite badly (sessions in failed cells and deduplicated findings are not in it).
        'valid_citations_in_cell_findings': sum(
            len(f['evidence']['citations']) for r in runs for f in r['findings']
            if f.get('evidence') and all(c['validation'] == 'valid'
                                         for c in f['evidence']['citations'])),
        'findings_in_cells': sum(len(r['findings']) for r in runs),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check_hashes(args.package)
    plan, evaluations = check_jobs(args.package)
    jobs = {job: job_summary(args.package, job, evaluations[job]) for job in JOBS}
    refused = sum(j['first_answers_refused_for_citations']['sessions'] for j in jobs.values())
    answered = sum(j['first_answers_refused_for_citations']['answered_sessions']
                   for j in jobs.values())
    summary = {'schema_version': 1, 'kind': plan['kind'], 'model': 'onprem/glm-5.3-flash',
               'holdout_used': False, 'qualification': False, 'defaults_changed': False,
               'jobs': jobs,
               'pooled_citation_refusals': {'sessions': refused, 'answered_sessions': answered,
                                            'wilson_95': wilson(refused, answered)}}
    text = json.dumps(summary, indent=1, sort_keys=True) + '\n'
    published = args.package / 'summary.json'
    if published.exists() and published.read_text() != text:
        raise ValueError('the recomputed summary differs from the published summary.json')
    args.output.write_text(text)


if __name__ == '__main__':
    main()
