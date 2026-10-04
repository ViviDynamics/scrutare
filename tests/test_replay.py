"""Offline recomputation uses captured source evidence and resolved policy."""

import hashlib
import json
from dataclasses import replace

import pytest

from scrutare.config import VerdictSettings
from scrutare.findings import Anchor, Finding, dedupe_findings, derive_verdict
from scrutare.findings.dedupe import MergedFinding
from scrutare.findings.verdict import Exhaustion
from scrutare.replay.models import AuditIssue, PostingAudit, ReplayDifference


def bundle(path, *, category="security", exhausted=False, empty=False, settings=None):
    settings = settings or VerdictSettings()
    groups = dedupe_findings([] if empty else [
        Finding(Anchor("src/café.py", 2, "LEFT"), category, "Wrong result",
                "Private data 🔒", "security"),
    ])
    exhaustion = Exhaustion("debate", 3, 3) if exhausted else None
    original = derive_verdict(groups, settings, exhaustion=exhaustion)
    (path / "findings.json").write_text(json.dumps([group.to_dict() for group in groups]))
    (path / "config.json").write_text(json.dumps({
        "strategy": "debate", "rounds": {"max": 3}, "verdict": {
            "blocking_categories": list(settings.blocking_categories),
            "advisory_categories": list(settings.advisory_categories),
        }, "models": {"future": "unusable"}, "producer_version": "9999",
    }))
    (path / "verdict.json").write_bytes(original.to_bytes())
    return original


@pytest.mark.parametrize("category,exhausted,empty,status", [
    ("security", False, False, "changes_requested"),
    ("docs", False, False, "approve"), ("docs", False, True, "approve"),
    ("security", True, False, "escalated"), ("docs", True, False, "escalated"),
    ("docs", True, True, "escalated"),
])
def test_minimal_capture_reproduces_canonical_bytes_without_writes(
    tmp_path, category, exhausted, empty, status,
):
    from scrutare.replay import replay_run

    original = bundle(tmp_path, category=category, exhausted=exhausted, empty=empty)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = replay_run(tmp_path)
    assert result.verdict.to_bytes() == original.to_bytes()
    assert result.verdict.verdict == status
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posting.status == "absent"
    assert result.exit_code == 0
    assert result.to_dict()["exhaustion_basis"] == (
        "unverified_recorded_assertion" if exhausted else "none"
    )
    assert replay_run(tmp_path).to_dict() == result.to_dict()
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_changed_source_is_deciding_even_with_stale_summaries_and_saved_classification(tmp_path):
    from scrutare.replay import replay_run

    bundle(tmp_path)
    findings = json.loads((tmp_path / "findings.json").read_bytes())
    findings[0]["sources"][0]["category"] = "docs"
    (tmp_path / "findings.json").write_text(json.dumps(findings))
    result = replay_run(tmp_path)
    assert result.verdict.verdict == "approve"
    assert result.verdict.rule == "no_blocking_findings"
    changes = {difference.path: difference for difference in result.differences}
    assert changes["verdict.verdict"].before == "changes_requested"
    assert changes["verdict.verdict"].after == "approve"
    assert changes["verdict.rule"].before == "any_blocking_finding"
    source = changes["verdict.findings[0].sources[0].category"]
    assert (source.before, source.after) == ("security", "docs")
    assert "src/café.py" in source.finding and "Wrong result" in source.finding
    assert result.issues == (AuditIssue("findings_summary_disagrees",
                                       "findings[0].categories", "difference"),)
    assert result.exit_code == 1


def test_recomputation_retains_mixed_duplicate_sources_and_group_display_text(tmp_path):
    from scrutare.replay import replay_run

    bundle(tmp_path)
    anchor = Anchor("src/café.py", 2, "LEFT")
    advisory = Finding(anchor, "style", "Wrong\tresult", "Readability", "junior")
    blocker = Finding(anchor, "security", "Wrong result", "Private data 🔒", "security")
    groups = (
        MergedFinding(anchor, " Wrong   result ", (advisory, blocker, advisory)),
        MergedFinding(Anchor("b.py", 1), "Example", (
            Finding(Anchor("b.py", 1), "docs", "Example", "Usage", "docs"),
        )),
    )
    original = derive_verdict(groups, VerdictSettings())
    (tmp_path / "findings.json").write_text(json.dumps([group.to_dict() for group in groups]))
    (tmp_path / "verdict.json").write_bytes(original.to_bytes())
    result = replay_run(tmp_path)
    assert result.verdict.to_bytes() == original.to_bytes()
    assert result.verdict.findings[0].problem == " Wrong   result "
    assert result.verdict.findings[0].sources == (advisory, blocker, advisory)
    assert result.saved_identical is True
    assert result.exit_code == 0


def test_changed_captured_partition_recomputes_independently(tmp_path):
    from scrutare.replay import replay_run

    bundle(tmp_path)
    policy = json.loads((tmp_path / "config.json").read_bytes())
    policy["verdict"] = {"blocking_categories": ["correctness", "regression"],
                         "advisory_categories": ["security", "style", "consistency", "docs"]}
    (tmp_path / "config.json").write_text(json.dumps(policy))
    result = replay_run(tmp_path)
    assert result.verdict.verdict == "approve"
    assert result.exit_code == 1
    assert "verdict.config.blocking_categories[1]" in {d.path for d in result.differences}


def test_equal_json_with_different_encoding_is_named(tmp_path):
    from scrutare.replay import replay_run

    original = bundle(tmp_path)
    raw = json.dumps(original.to_dict(), ensure_ascii=True).encode()
    (tmp_path / "verdict.json").write_bytes(raw)
    result = replay_run(tmp_path)
    assert result.saved_identical is False
    assert result.differences == (ReplayDifference(
        "verdict.encoding", "encoding", hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(original.to_bytes()).hexdigest(),
    ),)
    assert result.exit_code == 1


@pytest.mark.parametrize("artifact", ["findings.json", "config.json"])
@pytest.mark.parametrize("kind", ["missing", "malformed", "unsupported"])
def test_required_invalid_inputs_raise_safe_public_error(tmp_path, artifact, kind):
    from scrutare.replay import ReplayError, replay_run

    bundle(tmp_path)
    path = tmp_path / artifact
    if kind == "missing":
        path.unlink()
    else:
        path.write_bytes(b"secret invalid" if kind == "malformed" else b"{}")
    with pytest.raises(ReplayError) as error:
        replay_run(tmp_path)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("raw", [None, b"secret invalid", b'{"schema_version":999}'])
def test_unavailable_baseline_never_guesses_ordinary_approval(tmp_path, raw):
    from scrutare.replay import replay_run

    bundle(tmp_path, empty=True, exhausted=True)
    path = tmp_path / "verdict.json"
    if raw is None:
        path.unlink()
    else:
        path.write_bytes(raw)
    result = replay_run(tmp_path)
    assert result.verdict is None
    assert result.saved_identical is None
    assert result.saved_sha256 == (hashlib.sha256(raw).hexdigest() if raw else None)
    assert result.exit_code == 2
    assert result.to_dict()["status"] == "incomplete"
    assert result.to_dict()["verdict"] is None
    assert result.issues


def test_conflicting_exhaustion_and_captured_bound_suppresses_candidate(tmp_path):
    from scrutare.replay import replay_run

    bundle(tmp_path, exhausted=True, empty=True)
    policy = json.loads((tmp_path / "config.json").read_bytes())
    policy["rounds"]["max"] = 4
    (tmp_path / "config.json").write_text(json.dumps(policy))
    result = replay_run(tmp_path)
    assert result.verdict is None
    assert result.saved_sha256 is not None
    assert result.saved_identical is None
    assert result.exit_code == 2
    assert result.issues == (AuditIssue("exhaustion_config_disagrees",
                                       "verdict.exhaustion", "incomplete"),)


@pytest.mark.parametrize("yaml_text,code,exit_code", [
    ("models: {future: unsupported}\nfuture: ignored\n", None, 0),
    ("verdict: {}\nrounds: {}\n", None, 0),
    ("strategy: debate\nrounds: {max: 3}\n", None, 0),
    ("verdict: {blocking_categories: [regression, security, correctness]}\n", None, 0),
    ("strategy: panel\n", "config_snapshots_disagree", 1),
    ("rounds: {max: 4}\n", "config_snapshots_disagree", 1),
    ("verdict: {blocking_categories: [security]}\n", "config_snapshots_disagree", 1),
    ("secret: [\n", "config_snapshot_unreadable", 2),
    ("strategy: secret-invalid\n", "config_snapshot_unsupported", 2),
    ("rounds: {max: true}\n", "config_snapshot_unsupported", 2),
    ("verdict: {blocking_categories: [security, security]}\n",
     "config_snapshot_unsupported", 2),
    ("strategy: panel\nstrategy: debate\n", "config_snapshot_unreadable", 2),
])
def test_optional_yaml_checks_only_supplied_deciding_fields(
    tmp_path, yaml_text, code, exit_code,
):
    from scrutare.replay import replay_run

    original = bundle(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml_text)
    result = replay_run(tmp_path)
    assert result.verdict.to_bytes() == original.to_bytes()
    assert result.saved_identical is True
    assert result.exit_code == exit_code
    assert [issue.code for issue in result.issues] == ([code] if code else [])
    assert "secret" not in json.dumps(result.to_dict())


def test_optional_yaml_nonregular_capture_is_incomplete_without_blocking_json(tmp_path):
    from scrutare.replay import replay_run

    bundle(tmp_path)
    (tmp_path / "config.yaml").symlink_to(tmp_path / "missing")
    result = replay_run(tmp_path)
    assert result.saved_identical is True
    assert result.verdict.verdict == "changes_requested"
    assert result.exit_code == 2
    assert result.issues == (AuditIssue("config_snapshot_unreadable",
                                       "config.yaml", "incomplete"),)


def test_result_has_fresh_json_data_and_independent_saved_and_posted_comparisons(tmp_path):
    from scrutare.replay import replay_run

    original = bundle(tmp_path)
    result = replay_run(tmp_path)
    digest = hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.to_dict() == {
        "schema_version": 1, "status": "identical", "verdict": "changes_requested",
        "rule": "any_blocking_finding", "recomputed_sha256": digest,
        "saved_verdict": {"byte_identical": True, "sha256": digest},
        "posted_verdict": {"byte_identical": None, "sha256": None, "status": "absent"},
        "exhaustion_basis": "none", "reviewer_request_status": "absent",
        "differences": [], "issues": [],
    }
    mutable = {"nested": ["before"]}
    result = replace(result, differences=(ReplayDifference("path", "changed", mutable, []),))
    first = result.to_dict()
    first["differences"][0]["before"]["nested"].append("changed")
    assert result.to_dict()["differences"][0]["before"] == {"nested": ["before"]}
    assert result.differences[0].before == mutable
    assert json.loads(json.dumps(result.to_dict())) == result.to_dict()


@pytest.mark.parametrize("change,expected", [
    ({"saved_identical": False}, 1),
    ({"posted_identical": False, "posted_sha256": "0" * 64}, 1),
    ({"verdict": None}, 2), ({"saved_identical": None}, 2),
    ({"saved_sha256": None}, 2),
    ({"posted_identical": True, "posted_sha256": None}, 2),
    ({"posted_identical": False, "posted_sha256": None}, 2),
    ({"issues": (AuditIssue("x", "x", "difference"),)}, 1),
    ({"issues": (AuditIssue("x", "x", "incomplete"),), "saved_identical": False}, 2),
    ({"posting": PostingAudit("unknown", None, "absent",
                              (AuditIssue("x", "x", "invalid"),))}, 2),
])
def test_exit_precedence_and_target_availability(tmp_path, change, expected):
    from scrutare.replay import replay_run

    bundle(tmp_path)
    result = replace(replay_run(tmp_path), **change)
    assert result.exit_code == expected
    assert result.to_dict()["status"] == {0: "identical", 1: "different", 2: "incomplete"}[expected]


def test_exhaustion_basis_requires_original_posted_digest_to_match_saved_bytes(tmp_path):
    from scrutare.replay import replay_run

    bundle(tmp_path, exhausted=True)
    result = replay_run(tmp_path)
    assert result.to_dict()["exhaustion_basis"] == "unverified_recorded_assertion"
    result = replace(result, posting=PostingAudit("posted", result.saved_sha256, "pending", ()),
                     posted_sha256=result.saved_sha256, posted_identical=True)
    assert result.to_dict()["exhaustion_basis"] == "recorded_assertion"
    assert result.exit_code == 0
    result = replace(result, saved_sha256="0" * 64)
    assert result.to_dict()["exhaustion_basis"] == "unverified_recorded_assertion"
