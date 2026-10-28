"""Whole-review recovery happens against captured bytes before any posting."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_evaluation_transient import session
from test_panel import install
from test_post_review import REF, SHA, CaptureClient, FakePoster
from test_review_run import RAW

from scrutare.config import parse_config
from scrutare.engine import review
from scrutare.engine.session_models import NareRuntime, TokenUsage


@pytest.mark.parametrize('outcome', [
    'success', 'failure', 'moved', 'posted', 'uncertain', 'unaccounted'])
def test_review_retry_is_frozen_bounded_and_never_posts_a_failed_attempt(
        tmp_path, monkeypatch, outcome):
    install(monkeypatch, {})
    actual = review.run_review
    seen = []
    poster = FakePoster()
    capture = CaptureClient()
    async def run(directory, conf, *, runtime):
        seen.append(directory)
        assert (directory / 'config.yaml').read_bytes() == RAW
        if len(seen) == 2:
            assert (directory / 'diff.patch').read_bytes() == (seen[0] / 'diff.patch').read_bytes()
            if outcome == 'success':
                return await actual(directory, conf, runtime=runtime)
        failed = session(directory, 'security', status='failed', reason='output_cap')
        (failed / 'invocation.json').write_text(json.dumps(
            {'rail': conf.models.for_persona('security').record()}))
        with (failed / 'stdout.jsonl').open('a') as stream:
            stream.write(json.dumps({'type': 'cost', 'text': '', 'timestamp': 'frozen',
                'detail': {'input': 7, 'output': 0, 'cache_read': 0, 'cache_write': 0,
                           'cost': None}}) + '\n')
        events = [dict(e, text=e.get('text', ''), detail=e.get('detail', {}),
                       timestamp='frozen') for e in map(json.loads,
                        (failed / 'stdout.jsonl').read_text().splitlines())]
        (failed / 'stdout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        data = json.loads((failed / 'result.json').read_text())
        data['accounting_complete'] = outcome != 'unaccounted'
        data['artifact_directory'] = str(failed)
        (directory / 'panel.json').write_text(json.dumps({'status': 'failed', 'reason': 'initial'}))
        (directory / 'fanout.json').write_text(json.dumps({
            'personas': [{'name': data['persona']}], 'result': {'outcomes': [data]}}))
        (failed / 'result.json').write_text(json.dumps(data))
        if outcome in ('posted', 'uncertain'):
            (directory / 'posting.json').write_text(json.dumps({
                'status': 'posted' if outcome == 'posted' else 'sending'}))
        if outcome == 'moved':
            capture.get_pr = lambda ref: {'head': {'sha': 'f' * 40}}
        return SimpleNamespace(status='failed', accounting_complete=outcome != 'unaccounted',
                               usage=TokenUsage(input=7), verdict=None)
    monkeypatch.setattr(review, 'run_review', run)
    def invoke():
        return asyncio.run(review.review_pr(REF, parse_config(RAW), config_bytes=RAW,
            runs_root=tmp_path, runtime=NareRuntime(Path(sys.executable)),
            capture_client=capture, review_client=poster, whole_review_retry=True,
            expected_head=SHA))
    if outcome == 'success':
        result = invoke()
        assert len(seen) == 2 and result.run_dir == seen[1]
        assert len(poster.creates) == 1
        recovery = result.to_dict()['recovery']
        assert recovery['selected_attempt'] == 2
        assert recovery['cumulative_usage']['total'] == result.usage.total + 7
        assert (seen[0] / 'artifacts.json').is_file()
    else:
        with pytest.raises(review.ReviewRunError):
            invoke()
        assert len(seen) == (2 if outcome == 'failure' else 1)
        assert not poster.creates
        assert all((p / 'artifacts.json').is_file() for p in seen)


def test_retry_freezes_iterative_history_and_refuses_concurrent_history_change(tmp_path):
    from scrutare.engine.iterative import _history, _save
    from scrutare.engine.recovery import whole_review_history
    from scrutare.engine.review_inputs import ReviewInputError
    root = tmp_path / 'run'
    root.mkdir()
    (root / 'metadata.json').write_text(json.dumps({'repository': 'owner/repo', 'pr_number': 12}))
    conf = parse_config(RAW)
    with whole_review_history():
        with _history(root, conf) as (state, path):
            state['rounds_completed'] += 1
            _save(path, state)
        with _history(root, conf) as (state, path):
            assert state['rounds_completed'] == 0
            state['rounds_completed'] += 1
            _save(path, state)
        path.write_text(path.read_text() + '\n')
        with pytest.raises(ReviewInputError, match='changed'):
            with _history(root, conf):
                pytest.fail('changed history must be refused')


def test_posting_transport_head_move_supersedes_without_post(tmp_path, monkeypatch):
    from test_post_review import metadata
    install(monkeypatch, {})
    poster = FakePoster()
    poster.get_pr = lambda ref: metadata(head={'sha': 'f' * 40})
    with pytest.raises(review.ReviewSupersededError):
        asyncio.run(review.review_pr(REF, parse_config(RAW), config_bytes=RAW,
            runs_root=tmp_path, runtime=NareRuntime(Path(sys.executable)),
            capture_client=CaptureClient(), review_client=poster, whole_review_retry=True))
    assert not poster.creates


def test_head_move_during_posting_backoff_supersedes_before_second_post(tmp_path, monkeypatch):
    from test_post_review import metadata

    import scrutare.poster.posting as posting
    from scrutare.poster.errors import PostingRateLimited
    install(monkeypatch, {})
    poster = FakePoster(outcomes=[PostingRateLimited(status=429, retry_after=1)])
    now = [0.0]
    monkeypatch.setattr(posting, '_now', lambda: now[0])
    def wait(seconds):
        now[0] += seconds
        poster.current = metadata(head={'sha': 'f' * 40})
    monkeypatch.setattr(posting, 'sleep', wait)
    with pytest.raises(review.ReviewSupersededError):
        asyncio.run(review.review_pr(REF, parse_config(RAW), config_bytes=RAW,
            runs_root=tmp_path, runtime=NareRuntime(Path(sys.executable)),
            capture_client=CaptureClient(), review_client=poster, whole_review_retry=True))
    assert len(poster.creates) == 1
