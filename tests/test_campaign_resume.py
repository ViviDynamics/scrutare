"""A rule A campaign halted by its rail resumes under its freeze; scored cells never rerun."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_runner import config, corpus
from test_evaluation_transient import session

from scrutare.config import parse_config
from scrutare.engine.session_models import NareRuntime, TokenUsage
from scrutare.evaluation.runner import run_experiment


class Killed(BaseException):
    """The process dying mid-cell: nothing after the spend is written."""


def cell(directory):
    retry = '_retries' in directory.parts
    repeat = int(directory.parent.name if retry else directory.name)
    return repeat, retry


def fake_review(plan, calls, entered):
    """`plan` maps (repeat, is_rerun) to complete, output_cap, hang or kill.

    A hanging cell has spent (its session is on disk) and waits for the launcher's halt.
    """
    async def review(directory, conf, *, runtime):
        key = cell(directory)
        calls.append(key)
        action = plan.get(key, 'complete')
        reason = 'complete' if action in ('complete', 'hang', 'kill') else action
        status = 'complete' if reason == 'complete' else 'failed'
        path = session(directory, 'senior-dev', status=status, reason=reason)
        (path / 'invocation.json').write_text(json.dumps(
            {'rail': conf.models.for_persona('senior-dev').record()}))
        with (path / 'stdout.jsonl').open('a') as stream:
            stream.write(json.dumps({'type': 'cost', 'text': '', 'timestamp': 'frozen',
                'detail': {'input': 7, 'output': 0, 'cache_read': 0, 'cache_write': 0,
                           'cost': None}}) + '\n')
        events = [dict(e, text=e.get('text', ''), detail=e.get('detail', {}),
                       timestamp='frozen') for e in map(json.loads,
                        (path / 'stdout.jsonl').read_text().splitlines())]
        (path / 'stdout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        if action == 'hang':
            entered.set()
            await asyncio.Event().wait()
        if action == 'kill':
            raise Killed
        saved = json.loads((path / 'result.json').read_text())
        saved.update(accounting_complete=True, artifact_directory=str(path),
                     nare_status='done' if status == 'complete' else 'error')
        (path / 'result.json').write_text(json.dumps(saved))
        (directory / 'panel.json').write_text(json.dumps({'status': 'failed',
                                                          'reason': 'initial'}))
        (directory / 'fanout.json').write_text(json.dumps({
            'personas': [{'name': 'senior-dev'}], 'result': {'outcomes': [saved]}}))
        return SimpleNamespace(status=status, accounting_complete=True,
            usage=TokenUsage(input=7), verdict=SimpleNamespace(findings=())
            if status == 'complete' else None)
    return review


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    cases = corpus(tmp_path / 'corpus')
    calls = []
    version = {'value': '2026.11.109'}
    monkeypatch.setattr('scrutare.evaluation.runner.inspect_nare_runtime',
        lambda runtime: asyncio.sleep(0, result=SimpleNamespace(
            version=version['value'], contract=2)))

    def launch(plan, *, resume=None, conf=None, **options):
        async def main():
            # The launcher halts the job the way a stop signal does: by cancelling it.
            entered = asyncio.Event()
            monkeypatch.setattr('scrutare.evaluation.runner.run_review',
                                fake_review(plan, calls, entered))
            job = asyncio.create_task(run_experiment(cases, conf or config(),
                tmp_path / 'out', runtime=NareRuntime(Path('/unused')),
                evidence_kind='offline', variant_names=('senior',),
                qualification_rule_a=True, resume=resume, **options))
            if 'hang' in plan.values():
                await asyncio.wait([job, asyncio.create_task(entered.wait())],
                                   return_when=asyncio.FIRST_COMPLETED)
                job.cancel()
            return await job
        return asyncio.run(main())
    return SimpleNamespace(launch=launch, calls=calls, out=tmp_path / 'out',
                           version=version, cases=cases)


def records(out):
    return {row['run_id']: row for row in
            json.loads((out / 'experiment.json').read_text())['runs']}


def test_resume_keeps_scored_cells_and_reruns_only_the_halted_and_unstarted(campaign):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    assert campaign.calls == [(1, False)]
    before = records(campaign.out)
    assert before['a/senior/1']['error'] == 'Interrupted'
    assert before['a/senior/2']['status'] == before['a/senior/3']['status'] == 'missing'

    result = campaign.launch({}, resume='transport')
    assert campaign.calls[1:] == [(1, False), (2, False), (3, False)]
    after = {row['run_id']: row for row in result['runs']}
    assert result['status'] == 'complete'
    halted = after['a/senior/1']['halted_attempts']
    assert [h['error'] for h in halted] == ['Interrupted']
    assert '/_halted/segment-0001/a/senior/1' in halted[0]['run_dir']
    assert halted[0]['usage']['input'] == 7
    assert result['halted_usage']['input'] == 7
    assert result['cumulative_usage']['input'] == 21
    segments = json.loads((campaign.out / 'segments.json').read_text())
    assert [s['reason'] for s in segments] == [None, 'transport']
    assert segments[1]['retained_cells'] == []
    assert segments[1]['halted_cells'] == ['a/senior/1']
    assert all(s['nare_version'] == '2026.11.109' for s in segments)


def test_a_cell_killed_before_its_record_was_written_is_set_aside(campaign):
    with pytest.raises(Killed):
        campaign.launch({(1, False): 'kill'})
    assert not (campaign.out / 'a/senior/1/evaluation.json').exists()
    first = len(campaign.calls)
    before = records(campaign.out)
    retained = [r for r in before if before[r]['status'] == 'complete']
    scored = {r: json.dumps(before[r], sort_keys=True) for r in retained}
    result = campaign.launch({}, resume='gateway')
    rows = {row['run_id']: row for row in result['runs']}
    # Cells that finished before the kill keep their outcomes byte for byte; the rest run.
    assert {r: json.dumps(rows[r], sort_keys=True) for r in retained} == scored
    assert campaign.calls[first:] == [(int(r[-1]), False) for r in sorted(rows)
                                      if r not in retained]
    halted = {row['run_id']: row for row in result['runs']}['a/senior/1']['halted_attempts']
    assert halted[0]['status'] == 'unrecorded' and halted[0]['usage']['input'] == 7


def test_a_halt_in_the_recovery_phase_never_reruns_an_original(campaign):
    plan = {(1, False): 'output_cap', (2, False): 'output_cap', (3, False): 'output_cap',
            (2, True): 'hang'}
    with pytest.raises(asyncio.CancelledError):
        campaign.launch(plan)
    plan.pop((2, True))
    assert campaign.calls == [(1, False), (2, False), (3, False), (1, True), (2, True)]
    first = records(campaign.out)['a/senior/1']
    assert first['selected_attempt'] == 2
    result = campaign.launch(plan, resume='transport')
    # Only the halted replay and the replay never started run; no original runs again.
    assert campaign.calls[5:] == [(2, True), (3, True)]
    rows = {row['run_id']: row for row in result['runs']}
    assert json.dumps(rows['a/senior/1'], sort_keys=True) == json.dumps(first, sort_keys=True)
    assert all(row['status'] == 'complete' and len(row['attempts']) == 2
               for row in rows.values())
    assert rows['a/senior/2']['halted_attempts'][0]['error'] == 'Interrupted'


def test_resume_refuses_a_changed_freeze(campaign):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    changed = parse_config(b'models: {default: {provider: openai, model: frozen-v2}}\n'
                           b'budgets: {review_max_tokens: 400, per_persona_tokens: 100}\n')
    with pytest.raises(ValueError, match='changed since its freeze: configs'):
        campaign.launch({}, resume='transport', conf=changed)
    campaign.version['value'] = '2026.11.110'
    with pytest.raises(ValueError, match='frozen on nare 2026.11.109'):
        campaign.launch({}, resume='transport')
    assert len(json.loads((campaign.out / 'segments.json').read_text())) == 1


def test_resume_refuses_changed_inputs_in_an_unstarted_cell(campaign):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    (campaign.out / 'a/senior/3/diff.patch').write_text('tampered')
    with pytest.raises(ValueError, match='frozen inputs of a/senior/3 changed'):
        campaign.launch({}, resume='transport')


def test_resume_needs_an_unfinished_rule_a_campaign_and_an_infrastructure_halt(campaign):
    campaign.launch({})
    with pytest.raises(ValueError, match='complete'):
        campaign.launch({}, resume='transport')
    with pytest.raises(ValueError, match='halt kind'):
        campaign.launch({}, resume='model')
    with pytest.raises(ValueError, match='snapshot, experiment and segment'):
        asyncio.run(run_experiment(campaign.cases, config(), campaign.out.parent / 'none',
            runtime=NareRuntime(Path('/unused')), evidence_kind='offline',
            variant_names=('senior',), qualification_rule_a=True, resume='transport'))


def test_resume_runs_its_preflight_before_touching_the_campaign(campaign, monkeypatch):
    from scrutare.engine.preflight import PreflightError, PreflightReport
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    before = sorted(p.relative_to(campaign.out) for p in campaign.out.rglob('*'))
    async def refuse(conf, runtime):
        raise PreflightError(PreflightReport(({'check': 'route', 'status': 'failed',
                                               'reason': 'gateway_error'},)))
    monkeypatch.setattr('scrutare.evaluation.runner.run_preflight', refuse)
    with pytest.raises(PreflightError):
        campaign.launch({}, resume='preflight')
    assert sorted(p.relative_to(campaign.out) for p in campaign.out.rglob('*')) == before


def test_a_kill_in_the_replay_phase_leaves_the_campaign_resumable(campaign):
    plan = {(1, False): 'output_cap', (2, False): 'output_cap', (3, False): 'output_cap',
            (2, True): 'kill'}
    with pytest.raises(Killed):
        campaign.launch(plan)
    # Replays are still owed, so the campaign is not complete.
    assert json.loads((campaign.out / 'experiment.json').read_text())['status'] == 'incomplete'
    first = len(campaign.calls)
    plan.pop((2, True))
    result = campaign.launch(plan, resume='transport')
    assert campaign.calls[first:] == [(2, True), (3, True)]
    rows = {row['run_id']: row for row in result['runs']}
    assert result['status'] == 'complete'
    assert rows['a/senior/2']['halted_attempts'][0]['status'] == 'unrecorded'
    assert '_retries' in rows['a/senior/2']['halted_attempts'][0]['run_dir']


def test_a_second_halt_keeps_the_first_halts_spend(campaign):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'}, resume='transport')
    assert len(records(campaign.out)['a/senior/1']['halted_attempts']) == 1
    result = campaign.launch({}, resume='gateway')
    rows = {row['run_id']: row for row in result['runs']}
    assert [h['set_aside_in'] for h in rows['a/senior/1']['halted_attempts']] == [1, 2]
    assert result['halted_usage']['input'] == 14
    # Cells set aside never; a re-prepared cell that never ran is not a halted attempt.
    assert 'halted_attempts' not in rows['a/senior/2']
    assert 'halted_attempts' not in rows['a/senior/3']
    assert len(json.loads((campaign.out / 'segments.json').read_text())) == 3


def test_a_resume_killed_while_setting_cells_aside_loses_nothing(campaign, monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang', (2, False): 'hang'}, concurrency=2)
    assert campaign.calls == [(1, False), (2, False)]
    import scrutare.evaluation.runner as runner
    observed, seen = runner._observed_usage, []
    def dying(directory):
        seen.append(directory)
        if len(seen) == 2:
            raise Killed
        return observed(directory)
    monkeypatch.setattr(runner, '_observed_usage', dying)
    with pytest.raises(Killed):
        campaign.launch({}, resume='transport', concurrency=2)
    monkeypatch.setattr(runner, '_observed_usage', observed)
    result = campaign.launch({}, resume='transport', concurrency=2)
    rows = {row['run_id']: row for row in result['runs']}
    assert result['status'] == 'complete'
    assert [len(rows[f'a/senior/{n}']['halted_attempts']) for n in (1, 2)] == [1, 1]
    assert 'halted_attempts' not in rows['a/senior/3']
    assert result['halted_usage']['input'] == 14


def test_a_refused_resume_leaves_the_campaign_untouched(campaign):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    (campaign.out / 'a/senior/3/diff.patch').write_text('tampered')
    before = sorted(p.relative_to(campaign.out) for p in campaign.out.rglob('*'))
    with pytest.raises(ValueError, match='frozen inputs of a/senior/3'):
        campaign.launch({}, resume='transport')
    assert sorted(p.relative_to(campaign.out) for p in campaign.out.rglob('*')) == before


def test_a_resume_killed_while_preparing_a_cell_again_stays_resumable(campaign,
                                                                       monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        campaign.launch({(1, False): 'hang'})
    import scrutare.evaluation.runner as runner
    original, seen = runner.prepare_review_inputs, []
    def dying(directory, conf):
        seen.append(directory)
        # Cells 2 and 3 are verified first; the third call prepares cell 1 again.
        if len(seen) == 3:
            (directory / 'review-inputs').mkdir()
            raise Killed
        return original(directory, conf)
    monkeypatch.setattr(runner, 'prepare_review_inputs', dying)
    with pytest.raises(Killed):
        campaign.launch({}, resume='transport')
    monkeypatch.setattr(runner, 'prepare_review_inputs', original)
    result = campaign.launch({}, resume='transport')
    rows = {row['run_id']: row for row in result['runs']}
    assert result['status'] == 'complete'
    assert len(rows['a/senior/1']['halted_attempts']) == 1
    assert result['halted_usage']['input'] == 7


def test_a_replay_prepared_but_never_started_is_not_a_halted_attempt(campaign, monkeypatch):
    plan = {(1, False): 'output_cap', (2, False): 'output_cap', (3, False): 'output_cap'}
    import scrutare.evaluation.runner as runner
    original = runner.prepare_review_inputs
    def dying(directory, conf):
        inputs = original(directory, conf)
        if '_retries' in directory.parts:
            raise Killed
        return inputs
    monkeypatch.setattr(runner, 'prepare_review_inputs', dying)
    with pytest.raises(Killed):
        campaign.launch(plan)
    monkeypatch.setattr(runner, 'prepare_review_inputs', original)
    result = campaign.launch(plan, resume='launch')
    assert result['status'] == 'complete'
    assert all('halted_attempts' not in row for row in result['runs'])
