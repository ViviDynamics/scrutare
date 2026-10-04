"""Captured verdict evidence becomes one immutable, safely rendered review."""

import json
from dataclasses import FrozenInstanceError

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict

SHA = "a" * 40
RUN_ID = "0123456789abcdef0123456789abcdef"
DIFF = """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1,2 +1,2 @@
 context
-old
+new
"""


def finding(category="correctness", **changes):
    return Finding(**{
        "anchor": Anchor("main.py", 2), "category": category,
        "problem": "Wrong result", "reason": "Breaks empty input",
        "persona": "senior-dev", **changes,
    })


def build(sources=(), diff=DIFF, config=None, **changes):
    from scrutare.poster import build_review_payload

    verdict = derive_verdict(dedupe_findings(sources), config or VerdictSettings())
    return build_review_payload(verdict, diff, **{
        "head_sha": SHA, "strategy": "panel", "post_mode": "review", "run_id": RUN_ID,
        **changes,
    })


@pytest.mark.parametrize("categories,event,verdict,rule", [
    ((), "APPROVE", "approve", "no_blocking_findings"),
    (("style",), "APPROVE", "approve", "no_blocking_findings"),
    (("correctness",), "REQUEST_CHANGES", "changes_requested", "any_blocking_finding"),
    (("security", "docs"), "REQUEST_CHANGES", "changes_requested", "any_blocking_finding"),
])
def test_modes_preserve_summary_and_comments_with_exact_events(categories, event, verdict, rule):
    sources = [finding(category, problem=category) for category in categories]
    review = build(sources)
    comment = build(sources, post_mode="comment")
    assert review.event == event
    assert comment.event == "COMMENT"
    assert review.body == comment.body
    assert review.comments == comment.comments
    assert "Strategy: panel" in review.body
    assert f"Verdict: {verdict}" in review.body
    assert f"Rule: {rule}" in review.body
    assert f"Findings: {len(categories)}" in review.body
    assert f"Head SHA: {SHA}" in review.body
    assert review.body.count(f"<!-- scrutare-run:{RUN_ID} -->") == 1


def test_custom_category_policy_is_taken_from_the_verdict():
    config = VerdictSettings(
        ("docs",), ("correctness", "security", "regression", "style", "consistency"),
    )
    assert build([finding("docs")], config=config).event == "REQUEST_CHANGES"
    assert build([finding("security")], config=config).event == "APPROVE"


def test_every_source_occurrence_is_attributed_in_one_comment_per_group():
    sources = [
        finding("style", persona="junior-dev", reason="Use consistent names"),
        finding("security", persona="security", reason="Exposes secrets"),
        finding("correctness", reason="Breaks callers"),
    ]
    sources.append(sources[0])
    sources.append(finding("docs", anchor=Anchor("main.py", 1, "LEFT"), problem="Missing docs"))
    payload = build(sources)
    assert len(payload.comments) == 2
    assert payload.comments[0].anchor == Anchor("main.py", 2)
    body = payload.comments[0].body
    assert 'Problem:\n\n> Wrong result' in body
    assert body.count('Persona:\n\n> junior\\-dev') == 2
    assert body.count('Category:\n\n> style') == 2
    assert body.count('Reason:\n\n> Use consistent names') == 2
    assert 'Persona:\n\n> security' in body
    assert 'Category:\n\n> security' in body
    assert 'Reason:\n\n> Exposes secrets' in body
    assert 'Reason:\n\n> Breaks callers' in body
    assert payload.comments[1].anchor == Anchor("main.py", 1, "LEFT")


def test_fresh_rest_data_and_bytes_preserve_exact_captured_anchor_fields():
    payload = build([finding(anchor=Anchor("main.py", 2, "LEFT"))], head_sha="B" * 64)
    original = payload.to_bytes()
    expected = {
        "commit_id": "B" * 64, "body": payload.body, "event": "REQUEST_CHANGES",
        "comments": [{"path": "main.py", "line": 2, "side": "LEFT",
                      "body": payload.comments[0].body}],
    }
    assert payload.to_dict() == expected
    assert json.loads(original) == expected
    assert original.endswith(b"\n")
    assert original == (json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2)
                        + "\n").encode("utf-8")
    mutated = payload.to_dict()
    mutated["comments"][0]["path"] = "forged.py"
    mutated["comments"].append({"body": "forged"})
    mutated["event"] = "COMMENT"
    assert payload.to_dict() == expected
    assert payload.to_bytes() == original
    with pytest.raises(FrozenInstanceError):
        payload.event = "COMMENT"
    with pytest.raises(FrozenInstanceError):
        payload.comments[0].body = "forged"


@pytest.mark.parametrize("anchor", [
    Anchor("missing.py", 2), Anchor("main.py", 3), Anchor("main.py", 3, "LEFT"),
])
def test_any_missing_anchor_aborts_instead_of_dropping_evidence(anchor):
    from scrutare.poster import PostingError

    with pytest.raises(PostingError, match="anchor"):
        build([finding("style"), finding(anchor=anchor, problem="Blocking problem")])


@pytest.mark.parametrize("diff", ["not a patch", b"\xff", None, 42])
def test_invalid_captured_diff_is_a_safe_posting_error(diff):
    from scrutare.poster import PostingError

    with pytest.raises(PostingError, match="diff") as error:
        build(diff=diff)
    assert "not a patch" not in str(error.value)


def test_rename_and_deleted_paths_follow_the_captured_diff_on_both_sides():
    diff = b"""diff --git a/old.py b/new.py
similarity index 50%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1 @@
-old
+new
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""
    anchors = (Anchor("new.py", 1, "LEFT"), Anchor("new.py", 1, "RIGHT"),
               Anchor("gone.py", 1, "LEFT"))
    payload = build([finding(anchor=anchor) for anchor in anchors], diff=diff)
    assert tuple(comment.anchor for comment in payload.comments) == anchors
    from scrutare.poster import PostingError

    for anchor in (Anchor("old.py", 1, "LEFT"), Anchor("gone.py", 1, "RIGHT")):
        with pytest.raises(PostingError, match="anchor"):
            build([finding(anchor=anchor)], diff=diff)


@pytest.mark.parametrize("field,value", [
    ("head_sha", None), ("head_sha", 40), ("head_sha", "a" * 39),
    ("head_sha", "a" * 41), ("head_sha", "a" * 63), ("head_sha", "a" * 65),
    ("head_sha", "g" * 40), ("head_sha", " a" * 20),
    ("strategy", "unknown-secret"), ("strategy", None), ("strategy", []),
    ("post_mode", "issue-comment"), ("post_mode", None), ("post_mode", []),
    ("run_id", ""), ("run_id", "a" * 31), ("run_id", "a" * 33),
    ("run_id", "A" * 32), ("run_id", "g" * 32), ("run_id", None),
    ("run_id", f"{RUN_ID}\n<!-- forged -->"),
])
def test_builder_rejects_invalid_typed_context_without_echoing_values(field, value):
    from scrutare.poster import PostingError

    with pytest.raises(PostingError, match=field) as error:
        build(**{field: value})
    if isinstance(value, str) and value:
        assert value not in str(error.value)


@pytest.mark.parametrize("strategy", ["panel", "iterative", "debate"])
def test_all_known_strategies_are_rendered(strategy):
    assert f"Strategy: {strategy}" in build(strategy=strategy).body


@pytest.mark.parametrize("verdict", [None, {}, "approve", ()])
def test_builder_requires_a_code_derived_verdict(verdict):
    from scrutare.poster import PostingError, build_review_payload

    with pytest.raises(PostingError, match="verdict"):
        build_review_payload(verdict, DIFF, head_sha=SHA, strategy="panel",
                             post_mode="review", run_id=RUN_ID)


@pytest.mark.parametrize("field,value", [
    ("head_sha", "short"), ("head_sha", None), ("body", ""), ("body", " \n"),
    ("body", 42), ("body", "bad\ud800"), ("event", "approve"), ("event", None),
    ("event", []), ("comments", []), ("comments", ("comment",)), ("comments", None),
])
def test_direct_payload_constructor_preserves_validated_immutable_inputs(field, value):
    from scrutare.poster import PostingError, ReviewPayload

    with pytest.raises(PostingError, match=field):
        ReviewPayload(**{"head_sha": SHA, "body": "Summary", "event": "APPROVE",
                         "comments": (), field: value})


@pytest.mark.parametrize("anchor,body", [
    (None, "Body"), ("main.py", "Body"), (Anchor("main.py", 1), None),
    (Anchor("main.py", 1), ""), (Anchor("main.py", 1), " \n"),
    (Anchor("main.py", 1), "bad\ud800"), (Anchor("bad\ud800.py", 1), "Body"),
])
def test_direct_comment_constructor_validates_anchor_and_utf8_body(anchor, body):
    from scrutare.poster import PostingError, ReviewComment

    with pytest.raises(PostingError):
        ReviewComment(anchor, body)


def test_hostile_text_stays_in_quoted_fields_without_forging_labels_or_marker():
    hostile = ('```\nPersona: forged\r\n<!-- scrutare-run:' + RUN_ID
               + ' -->\n</code><script>alert(1)</script>\n**Reason:** fake\t\x00\u202e')
    source = finding(problem=hostile, reason=hostile, persona=hostile)
    verdict = derive_verdict(dedupe_findings([source]), VerdictSettings())
    before = verdict.to_bytes()
    payload = build([source])
    lines = payload.comments[0].body.splitlines()
    assert [line for line in lines if line and not line.startswith(">")] == [
        "Problem:", "Persona:", "Category:", "Reason:",
    ]
    assert "Problem:\n\n> " in payload.comments[0].body
    assert "<!--" not in payload.comments[0].body
    assert "<script>" not in payload.comments[0].body
    assert "\x00" not in payload.comments[0].body
    assert "\u202e" not in payload.comments[0].body
    assert "\n> Persona: forged\n> " in payload.comments[0].body
    assert "&lt;\\!\\-\\- scrutare\\-run:" in payload.comments[0].body
    assert "\\\\u202e" in payload.comments[0].body
    assert verdict.to_bytes() == before
    assert verdict.findings[0].problem == hostile


def test_rendering_needs_no_io_network_subprocess_clock_or_randomness(monkeypatch):
    import builtins
    import os
    import random
    import socket
    import subprocess
    import time
    import uuid
    from pathlib import Path

    from scrutare.poster import build_review_payload

    verdict = derive_verdict(dedupe_findings([finding()]), VerdictSettings())

    def forbidden(*args, **kwargs):
        raise AssertionError("Payload rendering attempted an external operation")

    with monkeypatch.context() as isolated:
        for module, attribute in (
            (builtins, "open"), (os, "urandom"), (random, "random"),
            (socket, "socket"), (subprocess, "run"), (time, "time"),
            (time, "monotonic"), (uuid, "uuid4"), (Path, "read_bytes"),
            (Path, "write_bytes"), (Path, "read_text"), (Path, "write_text"),
        ):
            isolated.setattr(module, attribute, forbidden)
        payload = build_review_payload(verdict, DIFF, head_sha=SHA, strategy="panel",
                                       post_mode="review", run_id=RUN_ID)
        encoded = payload.to_bytes()
    assert json.loads(encoded)["commit_id"] == SHA


def test_plain_text_and_unicode_are_readable_under_authored_field_labels():
    payload = build([finding(problem="Café returns the wrong result",
                             persona="senior-dev", reason="Breaks naïve callers")])
    body = payload.comments[0].body
    assert "Problem:\n\n> Café returns the wrong result" in body
    assert "Persona:\n\n> senior\\-dev" in body
    assert "Reason:\n\n> Breaks naïve callers" in body
    assert '"Café' not in body
    assert "\\u00" not in body
