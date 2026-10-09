"""Trusted routing retains general coverage, related evidence and explicit fixed allocations."""
import json
from hashlib import sha256

import pytest
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, parse_config
from scrutare.personas import PersonaDefinition, load_persona, resolve_personas
from scrutare.personas.registry import procedure_record


def parse_routing(routing, *, personas=None):
    data = {'routing': routing, 'models': {'default': {'model': 'frozen'}}}
    if personas is not None:
        data['personas'] = personas
    return parse_config(json.dumps(data))


def test_routing_default_is_omitted_and_domain_config_roundtrips():
    baseline = parse_config('models: {default: {model: frozen}}')
    assert not baseline.routing.enabled and 'routing' not in baseline.to_dict()
    conf = parse_routing({'enabled': True, 'mode': 'manual', 'force': ['billing-domain'],
        'rules': [{'persona': {'name': 'billing-domain', 'system_prompt': 'exact domain text\n'},
                   'paths': ['billing/**']}],
        'groups': [{'name': 'billing', 'implementation': ['billing/**'],
                    'callers': ['api/**'], 'contracts': ['schema/**'], 'tests': ['tests/**']}]})
    assert parse_config(json.dumps(conf.to_dict())) == conf
    assert conf.routing.rules[0].persona == PersonaDefinition(
        'billing-domain', 'exact domain text\n')


@pytest.mark.parametrize('routing', [
    {'enabled': 'yes'}, {'enabled': True, 'mode': 'model'},
    {'enabled': True, 'force': ['unknown']},
    {'enabled': True, 'rules': [{'persona': 'security', 'paths': ['../bad']}]},
    {'enabled': True, 'rules': [{'persona': 'unknown', 'paths': ['src/**']}]},
    {'enabled': True, 'groups': [{'name': 'x', 'tests': ['/absolute']}]},
    {'enabled': True, 'rules': [{'persona': 'security', 'paths': []}]},
])
def test_invalid_routing_is_rejected(routing):
    with pytest.raises(ConfigError, match='routing'):
        parse_routing(routing)


def test_specialist_procedures_have_explicit_version_and_hash_without_inline_replacement():
    for name in ('performance-concurrency', 'data-integrity-migrations'):
        persona = load_persona(name)
        assert 'read-only' in persona.system_prompt.lower()
        assert 'counterexample' in persona.system_prompt.lower()
        record = procedure_record(persona, procedures='baseline', inline=False)
        assert record['origin'] == 'builtin' and record['version'] == 'routing-v1'
        assert record['procedure_sha256'] == sha256(persona.system_prompt.encode()).hexdigest()
        inline = PersonaDefinition(name, 'caller custom bytes\n')
        assert resolve_personas((inline,), procedures='v1')[0] is inline
        assert procedure_record(inline, procedures='v1', inline=True)['version'] == 'inline'


def test_pure_routing_renamed_mixed_unknown_and_manual_preserve_coverage():
    from scrutare.engine.routing import plan_routing

    files = [{'filename': 'src/renamed.py', 'previous_filename': 'src/async_queue.py',
              'status': 'renamed'}, {'filename': 'migrations/001.sql', 'status': 'added'},
             {'filename': 'docs/unknown.md', 'status': 'modified'}]
    conf = parse_routing({'enabled': True}, personas=['junior-dev'])
    plan = plan_routing(files, (), conf)
    assert [p.name for p in plan.personas] == [
        'junior-dev', 'senior-dev', 'security', 'performance-concurrency',
        'data-integrity-migrations']
    assert plan.record['selected_paths'] == [
        'docs/unknown.md', 'migrations/001.sql', 'src/async_queue.py', 'src/renamed.py']
    assert plan.record['generalist_fallback']
    assert [p.name for p in plan_routing(files, (), parse_routing(
        {'enabled': True, 'mode': 'manual', 'force': ['data-integrity-migrations']})).personas
            ][-1] == 'data-integrity-migrations'
    unknown = plan_routing([{'filename': 'odd.ext', 'status': 'modified'}], (), conf)
    assert [p.name for p in unknown.personas] == ['junior-dev', 'senior-dev', 'security']
    assert all(item['reason'] == 'no_matching_path' for item in unknown.record['omitted'])


def test_related_groups_and_domains_preserve_inline_identity_and_global_dependencies():
    from scrutare.engine.routing import plan_routing

    conf = parse_routing({'enabled': True,
        'rules': [{'persona': {'name': 'billing-domain', 'system_prompt': 'exact bytes\n'},
                   'paths': ['billing/**']}],
        'groups': [{'name': 'billing', 'implementation': ['billing/**'],
                    'callers': ['api/**'], 'contracts': ['contracts/**'], 'tests': ['tests/**']}]})
    files = [{'filename': 'billing/charge.py', 'status': 'modified'}]
    supporting = ('api/client.py', 'contracts/charge.json',
                  'tests/test_charge.py', 'shared/util.py')
    plan = plan_routing(files, supporting, conf)
    assert plan.personas[-1] is conf.routing.rules[0].persona
    group = next(g for g in plan.record['groups'] if g['name'] == 'billing')
    assert group['roles'] == {'implementation': ['billing/charge.py'],
                             'callers': ['api/client.py'], 'contracts': ['contracts/charge.json'],
                             'tests': ['tests/test_charge.py']}
    assert group['rationale'] == 'trusted_config_selectors'
    assert 'shared/util.py' in plan.record['global_supporting_paths']
    assert plan.record['context_policy'] == 'shared_global_read_root'


def test_large_sets_are_stable_and_auto_group_matching_tests():
    from scrutare.engine.routing import plan_routing

    files = [{'filename': f'src/item{i}.py', 'status': 'modified'} for i in range(1000)]
    files += [{'filename': 'tests/test_item0.py', 'status': 'modified'}]
    conf = parse_routing({'enabled': True})
    a = plan_routing(files, (), conf)
    b = plan_routing(list(reversed(files)), (), conf)
    assert a.record == b.record
    group = next(g for g in a.record['groups'] if g['name'] == 'auto-item0')
    assert group['roles']['implementation'] == ['src/item0.py']
    assert group['roles']['tests'] == ['tests/test_item0.py']


def test_routed_definition_cannot_replace_configured_inline_procedure():
    from scrutare.engine.routing import plan_routing

    conf = parse_routing({'enabled': True,
        'rules': [{'persona': {'name': 'domain', 'system_prompt': 'replacement'},
                   'paths': ['src/**']}]},
        personas=[{'name': 'domain', 'system_prompt': 'original'}])
    with pytest.raises(ValueError, match='conflicting'):
        plan_routing([{'filename': 'src/a.py', 'status': 'modified'}], (), conf)


def test_pipeline_persists_routing_focus_and_actual_allocations(capture, monkeypatch):
    import asyncio
    from pathlib import Path

    from scrutare.engine.fanout import fan_out
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.engine.session_models import (
        NareCapability,
        NareRuntime,
        SessionOutcome,
        TokenUsage,
    )

    conf = parse_routing({'enabled': True, 'mode': 'manual',
                          'force': ['performance-concurrency']})
    conf = parse_config(json.dumps({**conf.to_dict(),
        'budgets': {'per_persona_tokens': 100, 'review_max_tokens': 403}}))
    (capture / 'config.yaml').write_text(json.dumps(conf.to_dict()))
    (capture / 'config.json').write_text(json.dumps(conf.to_dict()))
    seen = []
    async def inspect(runtime):
        return NareCapability('2026.10.4', 1)
    async def execute(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        seen.append(descriptor)
        assert 'routing-focus.json' in descriptor.prompt
        assert (descriptor.inputs.root / 'routing-focus.json').exists()
        assert ledger.allocations[lease.persona] == lease.allocated_tokens
        usage = TokenUsage(input=1)
        ledger.settle(lease, usage, True)
        return SessionOutcome(lease.persona, 'complete', 'done', (), True, usage, True,
                              lease.allocated_tokens, 0, lease.persona, artifact_directory,
                              lease.limit_tokens)
    monkeypatch.setattr('scrutare.engine.fanout.inspect_nare_runtime', inspect)
    monkeypatch.setattr('scrutare.engine.fanout.run_persona_session', execute)
    result = asyncio.run(fan_out(capture, conf, runtime=NareRuntime(Path('/unused'))))
    assert len(seen) == len(result.outcomes) == 5
    routing = json.loads((capture / 'routing.json').read_bytes())
    assert routing['allocations'] == {'senior-dev': 81, 'junior-dev': 81,
        'security': 81, 'devops': 80, 'performance-concurrency': 80}
    assert sum(routing['allocations'].values()) == routing['allocated_total'] == 403
    assert routing['review_ceiling'] == 403
    assert 'routing-focus.json' in routing['inputs_sha256']
    focus = json.loads((capture / 'review-inputs/routing-focus.json').read_bytes())
    assert focus['effective_personas'] == routing['effective_personas']
    fanout = json.loads((capture / 'fanout.json').read_bytes())
    assert fanout['routing'] == routing
    assert fanout['personas'][-1]['procedure']['version'] == 'routing-v1'
    assert prepare_review_inputs(capture, conf).root == seen[0].inputs.root


def test_routing_focus_is_immutable_and_disabled_root_stays_legacy(capture):
    from scrutare.engine.review_inputs import ReviewInputError, prepare_review_inputs

    conf = parse_routing({'enabled': True})
    (capture / 'config.yaml').write_text(json.dumps(conf.to_dict()))
    (capture / 'config.json').write_text(json.dumps(conf.to_dict()))
    root = prepare_review_inputs(capture, conf).root
    assert (root / 'routing-focus.json').exists()
    (root / 'routing-focus.json').write_text('{}')
    with pytest.raises(ReviewInputError):
        prepare_review_inputs(capture, conf)


def test_debate_chair_reservation_avoids_activated_domain_name(capture, monkeypatch):
    import asyncio
    from pathlib import Path

    from test_debate import mock_debate
    from test_panel import finding, install

    from scrutare.engine.debate import run_debate
    from scrutare.engine.session_models import NareRuntime

    conf = parse_routing({'enabled': True, 'mode': 'manual', 'force': ['debate-chair'],
        'rules': [{'persona': {'name': 'debate-chair', 'system_prompt': 'domain procedure'},
                   'paths': ['src/**']}]}, personas=['security'])
    conf = parse_config(json.dumps({**conf.to_dict(), 'strategy': 'debate',
        'budgets': {'per_persona_tokens': 100, 'review_max_tokens': 400}}))
    for name in ('config.json', 'config.yaml'):
        (capture / name).write_text(json.dumps(conf.to_dict()))
    install(monkeypatch, {'security': (finding(),)})
    mock_debate(monkeypatch)
    result = asyncio.run(run_debate(capture, conf, runtime=NareRuntime(Path('/unused'))))
    assert result.status == 'complete'
    record = json.loads((capture / 'routing.json').read_bytes())
    assert record['reserved_phase_personas'] == ['debate-chair-chair']
    assert record['allocations']['debate-chair'] == 100
    assert record['allocations']['debate-chair-chair'] == 100


def test_builtin_activation_preserves_configured_inline_specialist_bytes():
    from scrutare.engine.routing import plan_routing

    conf = parse_routing({'enabled': True}, personas=[
        {'name': 'performance-concurrency', 'system_prompt': 'custom with prior context\n'}])
    plan = plan_routing([{'filename': 'async_queue.py', 'status': 'modified'}], (), conf)
    assert plan.personas[0] is conf.personas[0]
    active = next(a for a in plan.record['activated'] if a['name'] == 'performance-concurrency')
    assert active['procedure']['version'] == 'inline'


@pytest.mark.parametrize('excluded', [False, True])
def test_prepared_routing_preserves_old_rename_identity_and_respects_selection(capture, excluded):
    from scrutare.engine.review_inputs import prepare_review_inputs

    changed = [{'filename': 'src/new.py', 'status': 'renamed',
                'previous_filename': 'src/async_queue.py'}]
    (capture / 'files.json').write_text(json.dumps(changed))
    metadata = json.loads((capture / 'metadata.json').read_bytes())
    metadata['pull_request']['changed_files'] = 1
    (capture / 'metadata.json').write_text(json.dumps(metadata))
    (capture / 'diff.patch').write_text(
        'diff --git a/src/async_queue.py b/src/new.py\n'
        'similarity index 100%\nrename from src/async_queue.py\nrename to src/new.py\n')
    conf = parse_routing({'enabled': True})
    if excluded:
        conf = parse_config(json.dumps({**conf.to_dict(),
            'github': {'paths': {'exclude': ['*queue*']}}}))
    for name in ('config.json', 'config.yaml'):
        (capture / name).write_text(json.dumps(conf.to_dict()))
    root = prepare_review_inputs(capture, conf).root
    focus = json.loads((root / 'routing-focus.json').read_bytes())
    if excluded:
        assert focus['selected_paths'] == [] and focus['activated'] == []
    else:
        assert focus['selected_paths'] == ['src/async_queue.py', 'src/new.py']
        assert focus['activated'][0]['name'] == 'performance-concurrency'
        assert json.loads((root / 'files.json').read_bytes())[0]['previous_filename'] == (
            'src/async_queue.py')
