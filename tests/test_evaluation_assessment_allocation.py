"""An explicit assessor reservation for comparison arms; the total ceiling never moves."""
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_context import complete, declared
from test_evaluation_runner import config

from scrutare.config import ContextSettings
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.runner import comparison_configs, run_experiment


@pytest.mark.parametrize('comparison_set,assessed,unassessed', [
    ('architectures', ('assessment', 'tools'),
     ('baseline', 'context', 'procedures', 'evidence', 'routing')),
    ('focused', ('assessed', 'focused1', 'focused3'), ('aggregate',)),
])
def test_explicit_reservation_reaches_every_assessed_arm_and_no_other(
        comparison_set, assessed, unassessed):
    default = comparison_configs(config(), comparison_set=comparison_set)
    chosen = comparison_configs(config(), comparison_set=comparison_set, assessment_tokens=120)
    assert list(chosen) == list(default)
    for name in assessed:
        assert default[name].findings.assessment.tokens == 80
        assert chosen[name].findings.assessment.tokens == 120
        assert chosen[name] == replace(default[name], findings=replace(
            default[name].findings, assessment=replace(
                default[name].findings.assessment, tokens=120)))
    for name in unassessed:
        assert chosen[name] == default[name]
    assert all(v.budgets.review_max_tokens == 400 for v in chosen.values())


def test_unset_reservation_keeps_the_one_fifth_default():
    from scrutare.config import BudgetSettings

    for comparison_set, assessed in (('architectures', ('assessment', 'tools')),
                                     ('focused', ('assessed', 'focused1', 'focused3'))):
        variants = comparison_configs(config(), comparison_set=comparison_set)
        for name, variant in variants.items():
            assert variant.budgets == BudgetSettings(100, 400)
            assert variant.findings.assessment.enabled == (name in assessed)
            if name in assessed:
                assert variant.findings.assessment.tokens == 80


def test_largest_reservation_leaves_each_reviewer_one_token():
    chosen = comparison_configs(config(), comparison_set='focused', assessment_tokens=396)
    assert chosen['assessed'].findings.assessment.tokens == 396
    assert chosen['assessed'].budgets.review_max_tokens == 400


def test_reservation_is_refused_when_no_selected_arm_has_an_assessor():
    with pytest.raises(ValueError, match='assessment'):
        comparison_configs(config(), comparison_set='architectures',
                           variant_names=('baseline', 'context'), assessment_tokens=120)
    chosen = comparison_configs(config(), comparison_set='architectures',
                                variant_names=('evidence', 'assessment'), assessment_tokens=120)
    assert chosen['assessment'].findings.assessment.tokens == 120


@pytest.mark.parametrize('value', [0, -1, 397, 400, 4000, 1.5, True, '120'])
def test_reservation_must_leave_every_reviewer_a_grant(value):
    with pytest.raises(ValueError, match='assessment'):
        comparison_configs(config(), comparison_set='focused', assessment_tokens=value)


@pytest.mark.parametrize('comparison_set', ['baseline', 'procedures'])
def test_reservation_is_refused_where_no_arm_has_an_assessor(comparison_set):
    with pytest.raises(ValueError, match='assessment'):
        comparison_configs(config(), comparison_set=comparison_set, assessment_tokens=120)


def test_snapshot_records_the_reservation_and_the_shared_ledger_split(tmp_path, monkeypatch):
    cases = declared(tmp_path / 'corpus')
    async def inspect(runtime):
        return SimpleNamespace(version='2026.10.4', contract=1)
    async def execute(directory, conf, *, runtime):
        if conf.findings.discussion.enabled:
            (directory / 'debate.json').write_text(json.dumps({
                'schema_version': 2, 'kind': 'focused_evidence_discussion',
                'status': 'complete', 'reason': 'undisputed', 'bypassed_candidate_ids': [],
                'rounds': [], 'final_assessments': []}))
        return complete()
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime', inspect)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    conf = replace(config(), context=ContextSettings(related_paths=('caller.py',)))
    asyncio.run(run_experiment(cases, conf, tmp_path / 'chosen',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='focused', assessment_tokens=120))
    asyncio.run(run_experiment(cases, conf, tmp_path / 'default',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        comparison_set='focused'))
    chosen = json.loads((tmp_path / 'chosen/snapshot.json').read_text())
    default = json.loads((tmp_path / 'default/snapshot.json').read_text())
    assert chosen['assessment_tokens'] == 120 and default['assessment_tokens'] is None
    for name, cell in chosen['case_variants']['a'].items():
        assert sum(cell['allocations'].values()) == 400 == cell['review_ceiling']
        if name == 'aggregate':
            assert cell == default['case_variants']['a'][name]
        else:
            assert cell['allocations'] == {'senior-dev': 70, 'junior-dev': 70, 'security': 70,
                                           'devops': 70, 'evidence-assessor': 120}
            assert default['case_variants']['a'][name]['allocations'][
                'evidence-assessor'] == 80


def test_indivisible_remainder_still_sums_to_the_total_ceiling(tmp_path, monkeypatch):
    cases = declared(tmp_path / 'corpus')
    async def inspect(runtime):
        return SimpleNamespace(version='2026.10.4', contract=1)
    async def execute(directory, conf, *, runtime):
        return complete()
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime', inspect)
    monkeypatch.setattr('scrutare.evaluation.runner.run_review', execute)
    conf = replace(config(), context=ContextSettings(related_paths=('caller.py',)))
    for tokens, expected in ((121, [70, 70, 70, 69]), (396, [1, 1, 1, 1])):
        out = tmp_path / f'out-{tokens}'
        asyncio.run(run_experiment(cases, conf, out, runtime=NareRuntime(Path('/unused')),
            evidence_kind='offline', comparison_set='architectures',
            variant_names=('assessment',), assessment_tokens=tokens))
        cell = json.loads((out / 'snapshot.json').read_text())['case_variants']['a'][
            'assessment']
        assert cell['allocations']['evidence-assessor'] == tokens
        assert sorted((v for k, v in cell['allocations'].items()
                       if k != 'evidence-assessor'), reverse=True) == expected
        assert sum(cell['allocations'].values()) == 400


def test_cli_passes_the_reservation(tmp_path, monkeypatch):
    from test_evaluation_transient import corpus

    from scrutare.evaluation import __main__ as cli
    corpus(tmp_path / 'corpus')
    (tmp_path / 'config.yaml').write_text(
        'models: {default: {provider: openai, model: frozen-v1}}\n'
        'budgets: {review_max_tokens: 400, per_persona_tokens: 100}\n')
    seen = {}
    async def experiment(cases, conf, output, **options):
        seen.update(options)
    monkeypatch.setattr(cli, 'run_experiment', experiment)
    argv = ['scrutare.evaluation', 'run', '--corpus', str(tmp_path / 'corpus'), '--config',
            str(tmp_path / 'config.yaml'), '--output', str(tmp_path / 'out'),
            '--nare-executable', '/unused', '--evidence-kind', 'offline']
    monkeypatch.setattr(sys, 'argv', [*argv, '--comparison-set', 'focused',
                                      '--assessment-tokens', '120'])
    cli.main()
    assert seen['assessment_tokens'] == 120
    monkeypatch.setattr(sys, 'argv', argv)
    cli.main()
    assert seen['assessment_tokens'] is None
