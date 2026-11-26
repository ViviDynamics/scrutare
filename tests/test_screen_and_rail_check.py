"""The screening tier and the rail watch a self-hosted qualification launches behind."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from scrutare.config import parse_config
from scrutare.engine.preflight import PreflightError, PreflightReport
from scrutare.engine.session_models import NareRuntime
from scrutare.evaluation.corpus import load_corpus
from scrutare.evaluation.rail_check import watch_rail, watched
from scrutare.evaluation.screen import load_screen, screen_cases, screen_gate

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / 'evaluations/screens/screen-v1.json'
CORPUS = ROOT / 'evaluations/development-v2'


def test_screen_v1_is_two_development_cases_one_defect_one_clean():
    screen = load_screen(SCREEN, CORPUS)
    cases = screen_cases(load_corpus(CORPUS), screen)
    assert [case.id for case in cases] == ['dev2-01', 'dev2-03']
    defects = [len(json.loads(case.labels.read_text())['defects']) for case in cases]
    assert defects == [1, 0]
    # One arm times three repeats: six cells, against a qualification's thirty-six.
    assert len(cases) * 3 == 6


def test_screen_refuses_another_corpus_and_unknown_or_holdout_cases(tmp_path):
    with pytest.raises(ValueError, match='another corpus manifest'):
        load_screen(SCREEN, ROOT / 'evaluations/development-v3')
    manifest = json.loads(SCREEN.read_text())
    manifest['cases'] = ['dev2-01', 'nope']
    (tmp_path / 'screen.json').write_text(json.dumps(manifest))
    screen = load_screen(tmp_path / 'screen.json', CORPUS)
    with pytest.raises(ValueError, match='missing from the corpus: nope'):
        screen_cases(load_corpus(CORPUS), screen)
    pilot = ROOT / 'evaluations/pilot'
    holdout = next(case for case in load_corpus(pilot, 'all') if case.split == 'holdout')
    with pytest.raises(ValueError, match='development cases only'):
        screen_cases(load_corpus(pilot, 'all'), {'cases': [holdout.id]})
    manifest['gate'] = 'most cells'
    (tmp_path / 'screen.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='screen gate'):
        load_screen(tmp_path / 'screen.json', CORPUS)


def experiment(tmp_path, statuses, *, status='complete', screen=True):
    root = tmp_path / 'run'
    root.mkdir()
    snapshot = {'variant_selection': ['senior'], 'qualification': {'rule': 'A'}}
    if screen:
        snapshot['screen'] = {'id': 'screen-v1', 'cases': ['dev2-01', 'dev2-03']}
    (root / 'snapshot.json').write_text(json.dumps(snapshot))
    (root / 'experiment.json').write_text(json.dumps({'status': status, 'runs': [
        {'run_id': f'cell/{n}', 'status': s, 'accounting_complete': True}
        for n, s in enumerate(statuses)]}))
    return root


def test_screen_gate_passes_only_six_complete_accounted_cells(tmp_path):
    assert screen_gate(experiment(tmp_path, ['complete'] * 6))['decision'] == 'pass'


@pytest.mark.parametrize('statuses,status', [
    (['complete'] * 5 + ['failed'], 'complete'),
    (['complete'] * 5 + ['invalid'], 'complete'),
    (['complete'] * 5, 'complete'),
    (['complete'] * 6, 'incomplete'),
])
def test_screen_gate_fails_any_failed_invalid_missing_or_unfinished_cell(tmp_path, statuses,
                                                                          status):
    report = screen_gate(experiment(tmp_path, statuses, status=status))
    assert report['decision'] == 'fail'


def test_screen_gate_refuses_a_job_that_was_not_a_screen(tmp_path):
    with pytest.raises(ValueError, match='not a screen'):
        screen_gate(experiment(tmp_path, ['complete'] * 6, screen=False))


def test_screen_is_part_of_the_frozen_snapshot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from test_campaign_resume import fake_review
    from test_evaluation_runner import config, corpus

    from scrutare.evaluation.runner import run_experiment
    monkeypatch.setattr('scrutare.evaluation.runner.run_review',
                        fake_review({}, [], asyncio.Event()))
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(version='v', contract=2)))
    record = {'id': 'screen-x', 'sha256': '0' * 64, 'cases': ['a'], 'gate': 'g'}
    asyncio.run(run_experiment(corpus(tmp_path / 'corpus'), config(), tmp_path / 'out',
        runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
        variant_names=('senior',), qualification_rule_a=True, screen=record))
    assert json.loads((tmp_path / 'out/snapshot.json').read_text())['screen'] == record
    assert screen_gate(tmp_path / 'out')['decision'] == 'pass'
    with pytest.raises(ValueError, match='rule A'):
        asyncio.run(run_experiment(corpus(tmp_path / 'c2'), config(), tmp_path / 'o2',
            runtime=NareRuntime(Path('/unused')), evidence_kind='offline', screen=record))


def test_screen_flag_needs_rule_a_and_one_arm(tmp_path, monkeypatch, capsys):
    from scrutare.evaluation.__main__ import main
    (tmp_path / 'c.yaml').write_text('models: {default: {provider: openai, model: m}}\n')
    base = ['x', 'run', '--corpus', str(CORPUS), '--config', str(tmp_path / 'c.yaml'),
            '--output', str(tmp_path / 'o'), '--nare-executable', '/unused',
            '--screen', str(SCREEN)]
    for extra in ([], ['--qualification-rule-a'],
                  ['--qualification-rule-a', '--variants', 'senior,panel']):
        monkeypatch.setattr(sys, 'argv', base + extra)
        with pytest.raises(SystemExit) as stop:
            main()
        assert stop.value.code == 2
    assert 'exactly one --variants' in capsys.readouterr().err


RAIL = parse_config(b'models: {default: {provider: openai, model: m, '
                    b'base_url: "http://route.invalid/v1"}}\n')


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def report(reason=None):
    checks = ({'check': 'route', 'status': 'passed', 'reason': None, 'seconds': 0.01},
              {'check': 'request', 'status': 'failed' if reason else 'passed',
               'reason': reason, 'seconds': 2.0, 'url': 'http://route.invalid/v1/models'})
    return PreflightReport(checks)


def watch(results, **options):
    clock, seen = Clock(), []
    async def probe(conf, runtime):
        seen.append(conf)
        outcome = report(results(len(seen)))
        if not outcome.passed:
            raise PreflightError(outcome)
        return outcome
    result = asyncio.run(watch_rail(RAIL, NareRuntime(Path('/unused')), probe=probe,
                                    clock=clock, sleep=clock.sleep, **options))
    return result, seen


def test_a_healthy_rail_passes_after_the_whole_window_and_probe_floor():
    result, seen = watch(lambda n: None, window_seconds=600, interval_seconds=60,
                         min_probes=5)
    assert result['decision'] == 'pass'
    assert result['probes'] == 11 and result['span_seconds'] == 600
    assert result['request_checks'] == {'passed': 11, 'total': 11,
                                        'p50_seconds': 2.0, 'p95_seconds': 2.0}
    assert all(c.models.preflight.route and c.models.preflight.request for c in seen)
    # A route's address never reaches the report.
    assert 'route.invalid' not in json.dumps(result)


def test_the_probe_floor_extends_a_short_window():
    result, _ = watch(lambda n: None, window_seconds=60, interval_seconds=60, min_probes=4)
    assert result['probes'] == 4 and result['decision'] == 'pass'


def test_one_failed_probe_fails_the_rail_and_names_why():
    result, _ = watch(lambda n: 'gateway_error' if n == 7 else None,
                      window_seconds=600, interval_seconds=60, min_probes=5)
    assert result['decision'] == 'fail'
    assert result['failed_probes'] == 1 and result['reasons'] == ['gateway_error']
    assert result['request_checks']['passed'] == 10


def test_an_anthropic_only_rail_watches_requests_without_a_route_check():
    conf = watched(parse_config(b'models: {default: {provider: anthropic, model: m}}\n'))
    assert conf.models.preflight.route is False and conf.models.preflight.request is True


def test_a_watch_refuses_unbounded_or_empty_settings():
    for options in ({'min_probes': 0}, {'interval_seconds': 0},
                    {'window_seconds': 90000}):
        with pytest.raises(ValueError, match='rail watch'):
            asyncio.run(watch_rail(RAIL, NareRuntime(Path('/unused')), **options))
