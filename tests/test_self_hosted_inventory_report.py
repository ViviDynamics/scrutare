"""The #92 self-hosted inventory and shortlist package reproduces from public files."""
import hashlib
import importlib.util
import json
import re
import shutil

import pytest
from test_glm_qualification_report import REPORTS

PACKAGE = REPORTS / '2026-10-25-self-hosted-inventory'


def run(monkeypatch, package, output):
    spec = importlib.util.spec_from_file_location('self_hosted_inventory',
                                                  PACKAGE / 'reproduce.py')
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr('sys.argv', ['reproduce.py', '--package', str(package),
                                     '--output', str(output)])
    loaded.main()
    return json.loads(output.read_text())


def test_public_package_reproduces_the_capability_table(tmp_path, monkeypatch):
    summary = run(monkeypatch, PACKAGE, tmp_path / 'summary.json')
    assert summary == json.loads((PACKAGE / 'summary.json').read_text())
    assert summary['groups_able_to_run_nare_reviews'] == ['spark/glm-5.3-flash']
    assert summary['declared'] == {'infrastructure_changed': False, 'screening_run': False,
                                   'holdout_used': False}
    assert summary['paid_provider_rows'] == 0
    groups = summary['groups']
    assert groups['spark/glm-5.3-flash'] == {'decode_tok_s': 22.9, 'http_error': False,
                                             'json_schema_valid': True, 'tool_calls': True}
    assert not any(row['tool_calls'] for name, row in groups.items()
                   if name != 'spark/glm-5.3-flash')
    assert sorted(name for name, row in groups.items() if row['http_error']) == [
        'ada/deepseek-r1-14b', 'ada/phi4-14b', 'ada/qwen2.5-coder-14b', 'ada/qwen3-14b']
    published = json.loads((PACKAGE / 'published-sha256.json').read_text())
    on_disk = {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*')
               if p.is_file() and '__pycache__' not in p.parts}
    assert set(published) == on_disk - {'published-sha256.json'}


@pytest.mark.parametrize('change', ['paid', 'tool', 'unlisted', 'summary', 'errored', 'group'])
def test_tampering_is_refused(tmp_path, monkeypatch, change):
    package = tmp_path / 'package'
    shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns('__pycache__'))

    def edit(name, mutate):
        path = package / name
        document = json.loads(path.read_text())
        mutate(document)
        path.write_text(json.dumps(document))
        hashes = json.loads((package / 'published-sha256.json').read_text())
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (package / 'published-sha256.json').write_text(json.dumps(hashes))

    if change == 'paid':
        edit('provider-evidence.json', lambda d: d['probe_spend_log'].update(paid_provider_rows=1))
    elif change == 'tool':
        edit('capability-probe.json', lambda d: d['groups'][1].update(tool_call_valid=True))
    elif change == 'errored':
        edit('capability-probe.json', lambda d: next(
            g for g in d['groups'] if g['json_schema']['error']).update(json_schema_valid=True))
    elif change == 'group':
        edit('provider-evidence.json', lambda d: d['probe_spend_log']['by_group_endpoint_status']
             .update({'ada/llama3.1-8b | ollama-ada-w4v.ollama | success': 4}))
    elif change == 'unlisted':
        (package / 'extra.json').write_text('{}')
    else:
        edit('summary.json', lambda d: d['declared'].update(screening_run=True))
    with pytest.raises(ValueError):
        run(monkeypatch, package, tmp_path / 'summary.json')


def test_no_public_file_names_a_local_path_a_key_or_model_text():
    for path in PACKAGE.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        text = path.read_text()
        assert '/home/' not in text and '/tmp/' not in text, path
        assert not re.search(r'\bsk-[A-Za-z0-9_-]{12,}', text) and 'Bearer ' not in text, path
        # No person's email (for example an external user's key alias) is published.
        assert not re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}', text), path
    probe = json.loads((PACKAGE / 'capability-probe.json').read_text())
    assert all('text' not in row[kind] for row in probe['groups']
               for kind in ('decode', 'json_schema', 'tool_call'))
