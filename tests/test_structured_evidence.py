from hashlib import sha256

import pytest

from scrutare.findings.models import Anchor, FindingError, parse_finding


def wire():
    return dict(
        file="a.py",
        line=1,
        category="correctness",
        problem="failure",
        reason="why",
        evidence=dict(
            version=2,
            trigger="input",
            preconditions=["enabled"],
            expected="success",
            observed="failure",
            impact="lost result",
            citations=[
                dict(
                    side="head",
                    revision="a" * 40,
                    path="a.py",
                    start_line=1,
                    end_line=2,
                    sha256=sha256(b"x\ny\n").hexdigest(),
                )
            ],
        ),
    )


def test_v2_is_explicit_and_ids_are_caller_owned():
    with pytest.raises(FindingError):
        parse_finding(wire(), persona="tester")
    result = parse_finding(wire(), persona="tester", evidence_version=2, candidate_id="candidate-1")
    assert result.candidate_id == "candidate-1"
    assert result.evidence.version == 2
    assert result.evidence.citations[0].validation == "unvalidated"
    with pytest.raises(FindingError):
        parse_finding(
            wire() | {"candidate_id": "invented"},
            persona="tester",
            evidence_version=2,
            candidate_id="candidate-1",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", "main"),
        ("path", "../secret"),
        ("start_line", True),
        ("end_line", 0),
        ("sha256", "bad"),
    ],
)
def test_malformed_citations_fail(field, value):
    data = wire()
    data["evidence"]["citations"][0][field] = value
    with pytest.raises(FindingError):
        parse_finding(data, persona="tester", evidence_version=2, candidate_id="candidate-1")


def test_config_and_schema_opt_in():
    from scrutare.config import ConfigError, parse_config
    from scrutare.engine.session_output import findings_schema

    baseline = parse_config("models: {default: {model: test}}")
    assert "findings" not in baseline.to_dict()
    with pytest.raises(ConfigError):
        parse_config("models: {default: {model: test}}\nfindings: {evidence: v2}")
    config = parse_config(
        "models: {default: {model: test}}\ncontext: {enabled: true}\nfindings: {evidence: v2}"
    )
    assert config.findings.evidence == "v2"
    assert (
        "evidence"
        in findings_schema(evidence_version=2)["properties"]["findings"]["items"]["required"]
    )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("revision", "b" * 40, "revision_mismatch"),
        ("path", "missing.py", "path_not_captured"),
        ("end_line", 3, "line_range_outside_capture"),
        ("sha256", "b" * 64, "content_hash_mismatch"),
    ],
)
def test_citations_bind_prepared_bytes(tmp_path, field, value, reason):
    import json

    from scrutare.findings.evidence import validate_evidence

    data = wire()
    (tmp_path / "repository-context.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                revisions=dict(head=dict(sha="a" * 40)),
                entries=[
                    dict(
                        side="head",
                        path="a.py",
                        status="captured",
                        revision="a" * 40,
                        artifact="blob.txt",
                        sha256=sha256(b"x\ny\n").hexdigest(),
                    )
                ],
            )
        )
    )
    (tmp_path / "blob.txt").write_bytes(b"x\ny\n")
    finding = parse_finding(data, persona="tester", evidence_version=2, candidate_id="candidate-1")
    checked = validate_evidence(finding, tmp_path)
    assert checked.evidence.citations[0].validation == "valid"
    data["evidence"]["citations"][0][field] = value
    finding = parse_finding(data, persona="tester", evidence_version=2, candidate_id="candidate-1")
    with pytest.raises(FindingError, match=reason):
        validate_evidence(finding, tmp_path)
    (tmp_path / "blob.txt").write_bytes(b"changed\n")
    with pytest.raises(FindingError):
        validate_evidence(checked, tmp_path)


def test_reanchor_dedupe_replay_preserve_evidence():
    from scrutare.findings.dedupe import dedupe_findings
    from scrutare.findings.verification import ReanchorCorrection, check_anchors, finish_reanchor
    from scrutare.replay.artifacts import parse_findings
    from scrutare.replay.models import ReplayError

    original = parse_finding(
        wire(), persona="tester", evidence_version=2, candidate_id="candidate-1"
    )
    anchor = Anchor("a.py", 2)
    checked = finish_reanchor(
        check_anchors((original,), frozenset({anchor})), (ReanchorCorrection(original, anchor),)
    ).accepted[0]
    assert checked.evidence is original.evidence
    assert checked.candidate_id == original.candidate_id
    group = dedupe_findings((checked, checked))[0]
    artifact = group.to_dict()
    assert artifact["sources"][0]["evidence"]["version"] == 2
    assert len(parse_findings([artifact], evidence_version=2)[0][0].sources) == 2
    with pytest.raises(ReplayError):
        parse_findings([artifact])
    del artifact["sources"][1]["evidence"]
    with pytest.raises(ReplayError):
        parse_findings([artifact], evidence_version=2)


def test_iterative_roundtrip_and_legacy_omission():
    from scrutare.engine.iterative import _finding
    from scrutare.findings.models import artifact_data

    original = parse_finding(
        wire(), persona="tester", evidence_version=2, candidate_id="candidate-1"
    )
    assert _finding({"finding": artifact_data(original)}, evidence_version=2) == original
    legacy = parse_finding({k: v for k, v in wire().items() if k != "evidence"}, persona="tester")
    assert set(artifact_data(legacy)) == {"anchor", "category", "problem", "reason", "persona"}
    with pytest.raises(FindingError):
        _finding({"finding": artifact_data(original)})


def test_debate_v2_selection_preserves_sources_and_refuses_mutation():
    from unittest.mock import patch

    from scrutare.engine.debate_inputs import DebateInput
    from scrutare.personas import load_persona

    a = parse_finding(wire(), persona="tester", evidence_version=2, candidate_id="candidate-1")
    b = parse_finding(wire(), persona="security", evidence_version=2, candidate_id="candidate-2")
    with patch("scrutare.engine.persona_inputs.validate_prepared_inputs"):
        decision = DebateInput(load_persona("senior-dev"), None, (a, b), (), True, ("style",))
    selected = decision.select(
        {
            "findings": [
                {"candidate_id": "candidate-1", "category": "style"},
                {"candidate_id": "candidate-2", "category": "style"},
            ],
            "converged": True,
        }
    )
    assert [f.evidence for f in selected] == [a.evidence, b.evidence]
    assert [f.candidate_id for f in selected] == ["candidate-1", "candidate-2"]
    with pytest.raises(FindingError):
        decision.select(
            {
                "findings": [
                    {
                        "candidate_id": "candidate-1",
                        "category": "style",
                        "evidence": wire()["evidence"],
                    }
                ],
                "converged": True,
            }
        )
    with pytest.raises(FindingError):
        decision.select(
            {"findings": [{"candidate_id": "unknown", "category": "style"}], "converged": True}
        )


def test_iterative_preserves_distinct_caller_occurrences():
    from scrutare.engine.iterative import _key

    a = parse_finding(wire(), persona="tester", evidence_version=2, candidate_id="candidate-1")
    b = parse_finding(wire(), persona="tester", evidence_version=2, candidate_id="candidate-2")
    assert _key(a) != _key(b)


def test_v2_verdict_refuses_unvalidated_citations_and_versions():
    from scrutare.config import VerdictSettings
    from scrutare.findings.dedupe import dedupe_findings
    from scrutare.findings.verdict import derive_verdict
    from scrutare.replay.artifacts import parse_saved_verdict
    from scrutare.replay.models import ReplayError

    unvalidated = parse_finding(
        wire(), persona="tester", evidence_version=2, candidate_id="candidate-1"
    )
    with pytest.raises(FindingError):
        derive_verdict(dedupe_findings((unvalidated,)), VerdictSettings())
    empty = derive_verdict((), VerdictSettings(), evidence_version=2)
    assert empty.to_dict()["schema_version"] == 2
    with pytest.raises(ReplayError):
        parse_saved_verdict(empty.to_bytes())
    assert parse_saved_verdict(empty.to_bytes(), evidence_version=2)


def test_posting_quotes_v2_assertions_and_labels_identity_only():
    from dataclasses import replace

    from test_review_payload import build

    data = wire()
    data.update(file="main.py", line=2)
    data["evidence"]["observed"] = "<script>untrusted</script>"
    finding = parse_finding(data, persona="tester", evidence_version=2, candidate_id="candidate-1")
    citation = replace(finding.evidence.citations[0], validation="valid")
    finding = replace(finding, evidence=replace(finding.evidence, citations=(citation,)))
    payload = build((finding,)).to_dict()
    assert set(payload) == {"commit_id", "event", "body", "comments"}
    body = payload["comments"][0]["body"]
    assert "identity checked; claim unproven" in body
    assert "&lt;script&gt;" in body
    assert "<script>" not in body


def test_citation_lines_follow_file_newlines(tmp_path):
    import json

    from scrutare.findings.evidence import validate_evidence

    content = b"first\x0bsecond\n"
    data = wire()
    citation = data["evidence"]["citations"][0]
    citation.update(sha256=sha256(content).hexdigest(), end_line=2)
    (tmp_path / "blob.txt").write_bytes(content)
    (tmp_path / "repository-context.json").write_text(
        json.dumps(
            dict(
                revisions=dict(head=dict(sha="a" * 40)),
                entries=[
                    dict(
                        side="head",
                        path="a.py",
                        revision="a" * 40,
                        status="captured",
                        sha256=citation["sha256"],
                        artifact="blob.txt",
                    )
                ],
            )
        )
    )
    finding = parse_finding(data, persona="tester", evidence_version=2, candidate_id="candidate-1")
    with pytest.raises(FindingError, match="line_range_outside_capture"):
        validate_evidence(finding, tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 1},
        {"citations": []},
        {"extra": "untrusted"},
        {"preconditions": "conjecture"},
        {"observed": " "},
    ],
)
def test_v2_evidence_is_a_closed_required_contract(changes):
    data = wire()
    data["evidence"].update(changes)
    with pytest.raises(FindingError):
        parse_finding(data, persona="tester", evidence_version=2, candidate_id="candidate-1")
    legacy = {key: value for key, value in wire().items() if key != "evidence"}
    with pytest.raises(FindingError):
        parse_finding(legacy, persona="tester", evidence_version=2, candidate_id="candidate-1")
