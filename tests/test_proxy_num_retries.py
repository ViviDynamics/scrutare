"""Opt-in retry settings survive configuration, preparation and the native boundary."""

import asyncio
import json
from hashlib import sha256

import pytest
from test_nare_contract_capability import advertised, contract2, inspect
from test_nare_session import module, running_executable, setup
from test_review_inputs import capture as capture
from test_structured_output_wave import configure, wave

from scrutare.config import ConfigError, ModelRail, parse_config
from scrutare.engine.session_models import NareCapability, NareRuntime

OPENAI = 'models:\n  default: {provider: openai, model: m'
ENTRY = {'flag': '--proxy-num-retries', 'providers': ['openai'],
         'request_field': 'num_retries', 'default': None}


def test_default_and_explicit_null_preserve_canonical_bytes():
    off = parse_config(OPENAI + '}\n')
    assert parse_config(OPENAI + ', proxy_num_retries: null}\n') == off
    assert off.models.default.record() == {'provider': 'openai', 'base_url': None, 'model': 'm'}


def test_zero_inherits_and_explicit_null_clears():
    config = parse_config(OPENAI + ', proxy_num_retries: 0}\n  overrides:\n'
                          '    security: {model: other}\n    devops: {proxy_num_retries: null}\n')
    assert config.models.for_persona('security').proxy_num_retries == 0
    assert config.models.for_persona('devops').proxy_num_retries is None
    assert config.to_dict()['models']['default']['proxy_num_retries'] == 0
    assert config.to_dict()['models']['overrides']['devops']['proxy_num_retries'] is None


@pytest.mark.parametrize('value', ['-1', 'true', '0.5', '"0"'])
def test_invalid_values_refuse(value):
    with pytest.raises(ConfigError, match='proxy_num_retries: expected a nonnegative'):
        parse_config(OPENAI + f', proxy_num_retries: {value}}}\n')


def test_other_provider_refuses_inherited_setting():
    with pytest.raises(ConfigError, match='proxy_num_retries.*openai'):
        parse_config(OPENAI + ', proxy_num_retries: 0}\n  overrides:\n'
                     '    security: {provider: anthropic}\n')


@pytest.mark.parametrize('entry', [ENTRY, {}, {**ENTRY, 'request_field': 'other'}])
def test_runtime_reads_exact_advertised_capability(tmp_path, entry):
    path = advertised(tmp_path, version='2026.10.12', contract=2)
    path.write_text(path.read_text().replace(
        '"nare": \'2026.10.12\'}',
        f'"nare": \'2026.10.12\', "proxy_num_retries": {entry!r}}}'))
    assert inspect(path).proxy_num_retries is (entry == ENTRY)


def launch(capture, tmp_path, capability):
    descriptor, ledger, lease, attempt = setup(capture)
    path = contract2(running_executable(tmp_path))
    path.write_text(path.read_text().replace('a = parser.parse_args()',
                    'parser.add_argument("--proxy-num-retries")\na = parser.parse_args()'))
    outcome = asyncio.run(module().run_persona_session(
        descriptor, ModelRail('openai', None, 'm', proxy_num_retries=0), lease,
        ledger=ledger, artifact_directory=attempt, runtime=NareRuntime(path, 7, 5),
        capability=capability))
    return outcome, attempt


def test_native_invocation_records_zero(capture, tmp_path):
    outcome, attempt = launch(capture, tmp_path,
                              NareCapability('2026.10.12', 2, proxy_num_retries=True))
    assert outcome.status == 'complete'
    invocation = json.loads((attempt / 'invocation.json').read_text())
    assert invocation['argv'][-2:] == ['--proxy-num-retries', '0']
    assert invocation['rail']['proxy_num_retries'] == 0
    observed = json.loads((attempt / 'cwd/observed.json').read_text())
    assert observed['argv'][-2:] == ['--proxy-num-retries', '0']


def test_replay_refuses_a_lost_or_changed_retry_flag(capture, tmp_path):
    from scrutare.replay.audit import _proxy_num_retries_issues
    outcome, attempt = launch(capture, tmp_path,
                              NareCapability('2026.10.12', 2, proxy_num_retries=True))
    assert outcome.status == 'complete'
    config = parse_config(OPENAI + ', proxy_num_retries: 0}\n').to_dict()
    assert _proxy_num_retries_issues(capture, config) == ()
    path = attempt / 'invocation.json'
    saved = json.loads(path.read_text())
    for replacement in ([], ['--proxy-num-retries', '2'], ['--proxy-num-retries', 'False'],
                        ['--proxy-num-retries', '0', '--proxy-num-retries=2']):
        changed = {**saved, 'argv': saved['argv'][:-2] + replacement}
        path.write_text(json.dumps(changed))
        assert _proxy_num_retries_issues(capture, config)[0].severity == 'invalid'


def test_incapable_session_never_starts(capture, tmp_path):
    outcome, attempt = launch(capture, tmp_path, NareCapability('2026.10.12', 2))
    assert outcome.status == 'failed'
    assert not (attempt / 'cwd/observed.json').exists()


def test_wave_records_setting_in_prepared_inputs_and_provenance(capture, monkeypatch):
    config = configure(capture, {'default': {'provider': 'openai', 'model': 'm',
                                            'proxy_num_retries': 0}})
    wave(capture, monkeypatch, config,
         NareCapability('2026.10.12', 2, proxy_num_retries=True))
    manifest = json.loads((capture / 'fanout.json').read_text())
    assert all(x['rail']['proxy_num_retries'] == 0 for x in manifest['personas'])
    prepared = json.loads((capture / 'config.json').read_text())
    assert prepared['models']['default']['proxy_num_retries'] == 0
    bound = json.loads((capture / 'effective-files.json').read_text())
    assert bound['config_sha256'] == sha256(
        (json.dumps(prepared, ensure_ascii=False, sort_keys=True, indent=2,
                    allow_nan=False) + '\n').encode()).hexdigest()


def test_incapable_wave_refuses_before_any_launch(capture, monkeypatch):
    config = configure(capture, {'default': {'provider': 'openai', 'model': 'm',
                                            'proxy_num_retries': 0}})
    with pytest.raises(module().SessionRuntimeError, match='proxy_num_retries'):
        wave(capture, monkeypatch, config, NareCapability('2026.10.12', 2))
