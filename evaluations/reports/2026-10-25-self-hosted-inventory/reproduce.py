"""Recompute the #92 inventory's capability table from this package's public files.

Standard library only, no network. Checks every published hash, refuses files outside the
manifest, requires every probed group to be self-hosted with no fallback or paid row, and
fails if the recomputed table differs from summary.json.
"""
import argparse
import hashlib
import json
from pathlib import Path

SELF_HOSTED = ('spark/', 'ada/', 'arc/')


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = args.package
    published = read(package / 'published-sha256.json')
    on_disk = {p.relative_to(package).as_posix() for p in package.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    if on_disk - {'published-sha256.json'} != set(published):
        raise ValueError('package files differ from published-sha256.json')
    for name, digest in published.items():
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'hash mismatch: {name}')
    if 'summary.json' not in published:
        raise ValueError('summary.json is missing from the package')
    probe = read(package / 'capability-probe.json')
    evidence = read(package / 'provider-evidence.json')
    spend = evidence['probe_spend_log']
    if (spend['paid_provider_rows'] != 0 or set(spend['attempted_fallbacks']) != {'0'}
            or sum(spend['by_group_endpoint_status'].values()) != spend['rows_from_this_probe']
            or not all(key.startswith(SELF_HOSTED) for key in spend['by_group_endpoint_status'])):
        raise ValueError('the probe touched something other than a self-hosted group')
    # Every probed group has its three requests in the spend log (two failing groups also have
    # the one request that read their error body), and no other group appears.
    rows = {}
    for key, count in spend['by_group_endpoint_status'].items():
        group = key.split(' | ')[0]
        rows[group] = rows.get(group, 0) + count
    probed = [row['group'] for row in probe['groups']]
    extra = sorted(rows[g] - 3 for g in probed if g in rows)
    if set(rows) != set(probed) or extra != [0] * (len(probed) - 2) + [1, 1]:
        raise ValueError('the spend log does not match the probed groups')
    table = {}
    for row in probe['groups']:
        if not row['group'].startswith(SELF_HOSTED):
            raise ValueError(f"probed a group that is not self-hosted: {row['group']}")
        decode = row['decode']
        tools = row['tool_call']
        if row['tool_call_valid'] != (tools['tool_call_names'] == ['read']):
            raise ValueError(f"tool-call validity disagrees with the recorded call: {row['group']}")
        if (row['json_schema']['error'] and row['json_schema_valid']) or (
                tools['error'] and row['tool_call_valid']):
            raise ValueError(f"a failed request is recorded as valid: {row['group']}")
        table[row['group']] = {
            'decode_tok_s': decode['decode_tok_s'], 'http_error': bool(decode['error']),
            'json_schema_valid': row['json_schema_valid'],
            'tool_calls': row['tool_call_valid'],
        }
    runs_reviews = sorted(g for g, r in table.items() if r['tool_calls'] and r['json_schema_valid'])
    summary = {'schema_version': 1, 'probed_groups': len(table), 'groups': table,
               'groups_able_to_run_nare_reviews': runs_reviews,
               'probe_rows': spend['rows_from_this_probe'], 'paid_provider_rows': 0,
               # Declarations by the operator, not derived from evidence.
               'declared': {'infrastructure_changed': False, 'screening_run': False,
                            'holdout_used': False}}
    text = json.dumps(summary, indent=1, sort_keys=True) + '\n'
    if (package / 'summary.json').exists() and (package / 'summary.json').read_text() != text:
        raise ValueError('the recomputed summary differs from the published summary.json')
    args.output.write_text(text)


if __name__ == '__main__':
    main()
