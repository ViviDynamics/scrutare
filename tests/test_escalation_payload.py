"""Escalation carries all evidence while mentioning only validated human targets."""

import json
import re

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict
from scrutare.poster import PostingError, build_review_payload

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
        "problem": "Wrong result", "reason": "Breaks callers", "persona": "senior-dev",
        **changes,
    })


def build(sources=(), diff=DIFF, **changes):
    from scrutare.findings import Exhaustion

    verdict = derive_verdict(dedupe_findings(sources), VerdictSettings(),
                              exhaustion=Exhaustion("debate", 3, 3))
    return build_review_payload(verdict, diff, **{
        "head_sha": SHA, "strategy": "debate", "post_mode": "review", "run_id": RUN_ID,
        **changes,
    })


@pytest.mark.parametrize("post_mode", ["review", "comment"])
def test_both_modes_render_escalation_as_comment_with_summary_and_mentions(post_mode):
    sources = [finding(), finding("style", persona="junior-dev", reason="Inconsistent names"),
               finding("docs", anchor=Anchor("main.py", 2, "LEFT"), problem="Missing example")]
    sources.append(sources[1])
    payload = build(sources, post_mode=post_mode, human_reviewers=("Alice", "Bob-2", "alice"))
    assert payload.event == "COMMENT"
    assert "Scrutare escalation" in payload.body
    assert "Strategy: debate" in payload.body
    assert "Verdict: escalated" in payload.body
    assert "Rule: rounds_exhausted_without_convergence" in payload.body
    assert "Rounds completed: 3\nRound limit: 3" in payload.body
    assert f"Head SHA: {SHA}" in payload.body
    assert payload.body.count(f"<!-- scrutare-run:{RUN_ID} -->") == 1
    assert "Escalation targets for human review: @Alice @Bob-2" in payload.body
    assert "requested" not in payload.body.lower()
    assert "Problem:\n\n> Wrong result" in payload.body
    assert "Problem:\n\n> Missing example" in payload.body
    assert "File:\n\n> main\\.py" in payload.body
    assert "Line: 2\nSide: RIGHT" in payload.body
    assert "Line: 2\nSide: LEFT" in payload.body
    assert "Category:\n\n> correctness" in payload.body
    assert "Category:\n\n> style" in payload.body
    assert "Category:\n\n> docs" in payload.body
    assert "Persona:\n\n> senior\\-dev" in payload.body
    assert payload.body.count("Persona:\n\n> junior\\-dev") == 2
    assert payload.body.count("Reason:\n\n> Inconsistent names") == 2
    assert "Reason:\n\n> Breaks callers" in payload.body
    assert len(payload.comments) == 2
    assert payload.comments[0].body.count("Persona:\n\n> junior\\-dev") == 2


def test_empty_targets_and_findings_say_so():
    payload = build()
    assert payload.event == "COMMENT"
    assert "This repository has no escalation targets configured." in payload.body
    assert "No unresolved findings were supplied." in payload.body
    assert payload.comments == ()
    assert "@" not in payload.body


@pytest.mark.parametrize("invalid", [
    "", "a" * 40, "-alice", "alice-", "alice--bob", "@secret", "secret/team",
    " secret", "secret ", "secret\n", "sec\tret", "secret\x00", "secret\u202e",
    "sëcret", "secret_bot", "secret.bot", "secret[bot]", "secret[BOT]", 3, None,
])
def test_invalid_targets_fail_without_echo(invalid):
    from scrutare.poster.reviewers import normalize_human_reviewers

    for operation in (normalize_human_reviewers, lambda targets: build(human_reviewers=targets)):
        with pytest.raises(PostingError, match="github.human_reviewers") as error:
            operation(("Valid", invalid))
        assert "secret" not in str(error.value)
        if isinstance(invalid, str) and len(invalid) > 1:
            assert invalid not in str(error.value)


@pytest.mark.parametrize("invalid", [[], ["Alice"], "secret", None, {"secret": 1}])
def test_invalid_target_containers_fail_safely(invalid):
    from scrutare.poster.reviewers import normalize_human_reviewers

    for operation in (normalize_human_reviewers, lambda targets: build(human_reviewers=targets)):
        with pytest.raises(PostingError, match="github.human_reviewers") as error:
            operation(invalid)
        assert "secret" not in str(error.value)


def test_duplicate_targets_normalize():
    from scrutare.poster.reviewers import normalize_human_reviewers

    assert normalize_human_reviewers(("Alice", "ALICE", "b-2", "B-2", "C")) == (
        "Alice", "b-2", "C",
    )
    assert normalize_human_reviewers(()) == ()
    assert normalize_human_reviewers(("a", "Z" * 39, "0")) == ("a", "Z" * 39, "0")


def test_untrusted_summary_cannot_forge_heading_marker_or_mentions():
    hostile = ("@intruder @org/team\n# Forged heading\n<!-- scrutare-run:" + RUN_ID
               + " -->\n```\n</code><script>forged</script>\n&commat;entity\n\u202e\x00")
    path = "@path\n# Forged heading\n<!-- scrutare-run:" + RUN_ID + " -->.py"
    old_path, new_path = json.dumps("a/" + path), json.dumps("b/" + path)
    diff = (f"diff --git {old_path} {new_path}\n--- {old_path}\n+++ {new_path}\n"
            "@@ -1 +1 @@\n-old\n+new\n")
    source = finding(anchor=Anchor(path, 1), problem=hostile, reason=hostile, persona=hostile)
    payload = build([source], diff=diff, human_reviewers=("Real-human",))
    assert re.findall(r"@[A-Za-z0-9/-]+", payload.body) == ["@Real-human"]
    assert "@" not in payload.comments[0].body
    for text in (payload.body, payload.comments[0].body):
        assert "@intruder" not in text
        assert "@path" not in text
        assert "\n# Forged" not in text
        assert "<script>" not in text
        assert "\u202e" not in text
        assert "\x00" not in text
        assert "\n> \\# Forged heading" in text
        assert "＠intruder" in text
        assert "&amp;commat;entity" in text
    assert payload.body.count("<!--") == 1
    assert "<!--" not in payload.comments[0].body
    assert "File:\n\n> ＠path" in payload.body
    assert source.anchor.file == path
    assert source.problem == hostile


@pytest.mark.parametrize("strategy", ["panel", "iterative"])
def test_strategy_mismatch_rejected(strategy):
    with pytest.raises(PostingError, match="strategy"):
        build(strategy=strategy)


def test_ordinary_payload_bytes_and_mentions_unchanged():
    verdict = derive_verdict(dedupe_findings([finding(problem="@unchanged")]), VerdictSettings())
    payload = build_review_payload(verdict, DIFF, head_sha=SHA, strategy="panel",
                                   post_mode="review", run_id=RUN_ID, human_reviewers=("Alice",))
    assert payload.body == (
        "Scrutare review\n\nStrategy: panel\nVerdict: changes_requested\n"
        "Rule: any_blocking_finding\nFindings: 1\nHead SHA: " + SHA
        + "\n\n<!-- scrutare-run:" + RUN_ID + " -->"
    )
    assert payload.comments[0].body == (
        "Problem:\n\n> @unchanged\n\nPersona:\n\n> senior\\-dev\n\n"
        "Category:\n\n> correctness\n\nReason:\n\n> Breaks callers"
    )
    assert payload.event == "REQUEST_CHANGES"
