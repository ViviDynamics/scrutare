"""Iterative review executes real panel scheduling against cross-push evidence."""
import asyncio
import importlib
import json
import shutil
from dataclasses import replace

import pytest
from test_fanout import configure
from test_panel import finding, install
from test_review_inputs import capture as capture

from scrutare.engine.session_models import NareRuntime


def setup(capture, *, rounds=3):
    config = replace(configure(capture, personas=["security"], per=100, review=200),
                     strategy="iterative")
    config = replace(config, rounds=replace(config.rounds, max=rounds))
    for name in ("config.yaml", "config.json"):
        (capture / name).write_text(json.dumps(config.to_dict()))
    return config


def push(capture, name, *, patch=None):
    target = capture.parent / name
    target.mkdir()
    for path in capture.iterdir():
        if path.is_file() and path.name in ("metadata.json", "files.json", "diff.patch",
                                           "config.yaml", "config.json", "reviews.json",
                                           "comments.json", "review_comments.json"):
            shutil.copyfile(path, target / path.name)
    metadata = json.loads((target / "metadata.json").read_bytes())
    metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = name
    (target / "metadata.json").write_text(json.dumps(metadata))
    if patch is not None:
        (target / "diff.patch").write_bytes(patch)
    return target


def run(capture, config):
    module = importlib.import_module("scrutare.engine.iterative")
    return asyncio.run(module.run_iterative(capture, config, runtime=NareRuntime(capture / "nare")))


def test_new_push_preserves_pool_and_shared_bound(capture, monkeypatch):
    config = setup(capture, rounds=2)
    _, calls, _, _ = install(monkeypatch, {"security": (finding(),)})
    result = run(capture, config)
    assert result.verdict.verdict == "changes_requested"
    second = push(capture, "head-two")
    run(second, config)
    # Identical patch and no contest does not re-litigate or consume a round.
    assert len([call for call in calls if isinstance(call, tuple)]) == 1
    third = push(second, "head-three", patch=(second / "diff.patch").read_bytes().replace(
        b"+new", b"+changed"))
    result = run(third, config)
    assert result.verdict.verdict == "escalated"
    assert result.verdict.exhaustion.rounds_completed == 2
    pool = json.loads((third / "iterative.json").read_bytes())
    assert pool["rounds_completed"] == 2
    assert pool["pool"][0]["disposition"] == "upheld"


def test_only_new_hunks_reviewed_and_unchanged_finding_survives(capture, monkeypatch):
    config = setup(capture)
    extra = b"@@ -10 +10 @@\n-other\n+fresh\n"
    (capture / "diff.patch").write_bytes((capture / "diff.patch").read_bytes().replace(
        b"diff --git a/docs", extra + b"diff --git a/docs").replace(
            b"\\ No newline at end of file\r\n", b""))
    _, calls, _, _ = install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "second", patch=(capture / "diff.patch").read_bytes().replace(
        b"+fresh", b"+different"))
    install(monkeypatch, {"security": ()})
    result = run(second, config)
    assert result.verdict.verdict == "changes_requested"
    prepared = (second / "iterative-round/review-inputs/diff.patch").read_bytes()
    assert b"+different" in prepared and b"+new" not in prepared
    assert result.verdict.findings[0].anchor.line == 1


def test_fixed_and_withdrawn_remain_visible(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "fixed", patch=(capture / "diff.patch").read_bytes().replace(
        b"+new", b"+repaired"))
    install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "approve"
    assert json.loads((second / "iterative.json").read_bytes())["pool"][0]["disposition"] == "fixed"


def test_contested_unchanged_finding_is_withdrawn(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "contested")
    (second / "review_comments.json").write_text(json.dumps([
        {"id": 7, "path": "src/app.py", "line": 1, "body": "This behavior is intentional."}]))
    install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "approve"
    pool = json.loads((second / "iterative.json").read_bytes())["pool"]
    assert pool[0]["disposition"] == "withdrawn"


def test_exhausted_unreviewed_push_never_approves_same_head(capture, monkeypatch):
    config = setup(capture, rounds=1)
    install(monkeypatch, {"security": ()})
    assert run(capture, config).verdict.verdict == "approve"
    second = push(capture, "new", patch=(capture / "diff.patch").read_bytes().replace(
        b"+new", b"+unreviewed"))
    assert run(second, config).verdict.verdict == "escalated"
    repeat = push(second, "repeat")
    metadata = json.loads((repeat / "metadata.json").read_bytes())
    metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = "new"
    (repeat / "metadata.json").write_text(json.dumps(metadata))
    assert run(repeat, config).verdict.verdict == "escalated"


def test_corrupt_history_fails_before_sessions(capture, monkeypatch):
    config = setup(capture)
    _, calls, _, _ = install(monkeypatch, {"security": ()})
    run(capture, config)
    state = next((capture.parent / ".iterative").glob("*/state.json"))
    state.write_text('{"schema_version": 1}')
    second = push(capture, "next")
    from scrutare.engine.review_inputs import ReviewInputError
    with pytest.raises(ReviewInputError, match="history"):
        run(second, config)
    assert len([call for call in calls if isinstance(call, tuple)]) == 1


def test_existing_discussion_does_not_contest_every_later_push(capture, monkeypatch):
    config = setup(capture)
    (capture / "review_comments.json").write_text(json.dumps([
        {"id": 1, "path": "src/app.py", "line": 1, "body": "Question"}]))
    _, calls, _, _ = install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "later")
    run(second, config)
    assert len([call for call in calls if isinstance(call, tuple)]) == 1


def test_same_head_new_contest_executes_a_round(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "same-head-contested")
    metadata = json.loads((second / "metadata.json").read_bytes())
    metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = "abc123"
    (second / "metadata.json").write_text(json.dumps(metadata))
    (second / "review_comments.json").write_text(json.dumps([
        {"id": 1, "path": "src/app.py", "line": 1, "body": "Intentional behavior"}]))
    install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "approve"
    assert json.loads((second / "iterative.json").read_bytes())["rounds_completed"] == 2


def test_rereview_prompt_contains_existing_pool(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(problem="Existing flaw"),)})
    run(capture, config)
    second = push(capture, "new-head", patch=(capture / "diff.patch").read_bytes().replace(
        b"+new", b"+different"))
    wave = importlib.import_module("scrutare.engine.fanout")
    install(monkeypatch, {"security": ()})
    original = wave.run_persona_session

    async def inspect(descriptor, *args, **kwargs):
        assert "Existing flaw" in descriptor.persona.system_prompt
        return await original(descriptor, *args, **kwargs)

    monkeypatch.setattr(wave, "run_persona_session", inspect)
    run(second, config)


def test_git_index_change_does_not_reintroduce_unchanged_hunks(capture, monkeypatch):
    config = setup(capture)
    patch = (capture / "diff.patch").read_bytes().replace(
        b"--- a/src/app.py", b"index abc123..def456 100644\r\n--- a/src/app.py").replace(
        b"\\ No newline at end of file\r\n", b"@@ -10 +10 @@\n-old-ten\n+new-ten\n")
    (capture / "diff.patch").write_bytes(patch)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "indexed", patch=patch.replace(b"def456", b"fed789").replace(
        b"+new-ten", b"+changed-ten"))
    install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "changes_requested"
    prepared = (second / "iterative-round/review-inputs/diff.patch").read_bytes()
    assert b"+new\r" not in prepared and b"+changed-ten" in prepared


def test_failed_execution_reservation_survives_restart(capture, monkeypatch):
    config = setup(capture, rounds=1)
    install(monkeypatch, {"security": ()}, initial_status="failed")
    assert run(capture, config).verdict is None
    second = push(capture, "retry")
    _, calls, _, _ = install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "escalated"
    assert calls == []


def test_linked_history_is_refused(capture, monkeypatch):
    config = setup(capture)
    _, calls, _, _ = install(monkeypatch, {"security": ()})
    (capture.parent / ".iterative").symlink_to(capture, target_is_directory=True)
    from scrutare.engine.review_inputs import ReviewInputError
    with pytest.raises(ReviewInputError, match="unsafe"):
        run(capture, config)
    assert calls == []


def test_removed_hunk_fixes_finding_without_rechecking_unchanged_hunks(capture, monkeypatch):
    config = setup(capture)
    source = (capture / "diff.patch").read_bytes().replace(
        b"\\ No newline at end of file\r\n", b"@@ -10 +10 @@\n-old-ten\n+new-ten\n")
    (capture / "diff.patch").write_bytes(source)
    _, calls, _, _ = install(monkeypatch, {"security": (finding(line=10),)})
    run(capture, config)
    second = push(capture, "removed-hunk", patch=source.replace(
        b"@@ -10 +10 @@\n-old-ten\n+new-ten\n", b""))
    assert run(second, config).verdict.verdict == "approve"
    assert len([call for call in calls if isinstance(call, tuple)]) == 1
    assert json.loads((second / "iterative.json").read_bytes())["pool"][0]["disposition"] == "fixed"


def test_contest_reviews_only_the_matching_hunk(capture, monkeypatch):
    config = setup(capture)
    source = (capture / "diff.patch").read_bytes().replace(
        b"\\ No newline at end of file\r\n", b"@@ -10 +10 @@\n-old-ten\n+new-ten\n")
    (capture / "diff.patch").write_bytes(source)
    install(monkeypatch, {"security": (finding(), finding(line=10, problem="Other problem"))})
    run(capture, config)
    second = push(capture, "hunk-contested")
    (second / "review_comments.json").write_text(json.dumps([
        {"id": 7, "path": "src/app.py", "line": 1, "body": "Intentional behavior"}]))
    install(monkeypatch, {"security": ()})
    result = run(second, config)
    prepared = (second / "iterative-round/review-inputs/diff.patch").read_bytes()
    assert b"+new-ten" not in prepared and b"+new\r" in prepared
    assert result.verdict.findings[0].anchor.line == 10


def test_partial_rereview_does_not_clear_prior_blocking_findings(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "partial", patch=(capture / "diff.patch").read_bytes().replace(
        b"+new", b"+different"))
    install(monkeypatch, {"security": ()}, initial_status="partial")
    assert run(second, config).verdict.verdict == "changes_requested"
    pool = json.loads((second / "iterative.json").read_bytes())["pool"]
    assert pool[0]["disposition"] == "upheld"


def test_scrutare_posted_inline_findings_do_not_contest_themselves(capture, monkeypatch):
    config = setup(capture)
    _, calls, _, _ = install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "posted")
    (second / "reviews.json").write_text(json.dumps([
        {"id": 901, "body": "Scrutare review\n\n<!-- scrutare-run:abc -->"}]))
    (second / "review_comments.json").write_text(json.dumps([
        {"id": 7, "pull_request_review_id": 901, "path": "src/app.py", "line": 1,
         "body": "Problem: security risk"}]))
    run(second, config)
    assert len([call for call in calls if isinstance(call, tuple)]) == 1


def test_same_pr_lock_refuses_parallel_review(capture, monkeypatch):
    config = setup(capture)
    module = importlib.import_module("scrutare.engine.iterative")
    from scrutare.engine.review_inputs import ReviewInputError
    _, calls, _, _ = install(monkeypatch, {"security": ()})
    with module._history(capture, config):
        with pytest.raises(ReviewInputError, match="another review"):
            run(capture, config)
    assert calls == []
    assert run(capture, config).verdict.verdict == "approve"


def test_config_change_cannot_reset_history_bound(capture, monkeypatch):
    config = setup(capture, rounds=1)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "configuration-changed")
    modified = replace(config, rounds=replace(config.rounds, max=3))
    for name in ("config.yaml", "config.json"):
        (second / name).write_text(json.dumps(modified.to_dict()))
    from scrutare.engine.review_inputs import ReviewInputError
    with pytest.raises(ReviewInputError, match="configuration changed"):
        run(second, modified)


def test_human_reply_on_generated_review_contests_finding(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "reply")
    (second / "reviews.json").write_text(json.dumps([
        {"id": 901, "body": "Scrutare review\n\n<!-- scrutare-run:abc -->"}]))
    (second / "review_comments.json").write_text(json.dumps([
        {"id": 8, "pull_request_review_id": 901, "in_reply_to_id": 7,
         "path": "src/app.py", "line": 1, "body": "This is intentional."}]))
    install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "approve"
    assert json.loads((second / "iterative.json").read_bytes())["rounds_completed"] == 2


def test_missing_generated_review_id_cannot_suppress_unthreaded_contest(capture, monkeypatch):
    config = setup(capture)
    install(monkeypatch, {"security": (finding(),)})
    run(capture, config)
    second = push(capture, "malformed-review")
    (second / "reviews.json").write_text(json.dumps([
        {"body": "Scrutare review\n\n<!-- scrutare-run:abc -->"}]))
    (second / "review_comments.json").write_text(json.dumps([
        {"path": "src/app.py", "line": 1, "body": "This is intentional."}]))
    install(monkeypatch, {"security": ()})
    assert run(second, config).verdict.verdict == "approve"
