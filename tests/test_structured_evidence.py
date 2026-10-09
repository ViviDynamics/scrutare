from hashlib import sha256

import pytest
from test_review_inputs import capture as capture

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


@pytest.mark.parametrize("mode", ["exhausted", "partial"])
def test_stale_iterative_citations_retain_claim_without_current_approval(
    capture, monkeypatch, mode,
):
    from dataclasses import replace

    from test_iterative import push, run, setup
    from test_panel import finding, install
    from test_repository_context import contextual
    from test_review_inputs import read_json, save_json

    from scrutare.config import FindingSettings
    from scrutare.findings.models import Citation, EvidenceV2

    contextual_config, _ = contextual(capture)
    config = replace(setup(capture, rounds=1 if mode == "exhausted" else 3),
                     context=contextual_config.context, findings=FindingSettings("v2"))
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    original = replace(finding(), candidate_id="original", evidence=EvidenceV2(
        "call", (), "result", "failure", "loss", (Citation(
            "head", "a" * 40, "src/caller.py", 1, 1,
            sha256(b"call_changed()\n").hexdigest(), validation="valid"),)))
    install(monkeypatch, {"security": (original,)})
    run(capture, config)
    second = push(capture, "next")
    contextual(second)
    metadata = read_json(second / "metadata.json")
    metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = "f" * 40
    save_json(second / "metadata.json", metadata)
    manifest_path = second / "repository-context/repository-context.json"
    manifest = read_json(manifest_path)
    manifest["revisions"]["head"]["sha"] = "f" * 40
    for entry in manifest["entries"]:
        if entry["side"] == "head":
            entry["revision"] = "f" * 40
    save_json(manifest_path, manifest)
    for name in ("config.yaml", "config.json"):
        save_json(second / name, config.to_dict())
    install(monkeypatch, {"security": ()}, initial_status=(
        "partial" if mode == "partial" else "complete"))
    result = run(second, config)
    assert result.status == "partial"
    assert result.accounting_complete
    assert result.verdict is None if mode == "partial" else result.verdict.verdict == "escalated"
    state = read_json(second / "iterative.json")
    assert state["pool"][0]["disposition"] == "upheld"
    assert state["pool"][0]["dependency_status"] == "stale"
    assert state["pool"][0]["finding"]["evidence"]["citations"][0]["revision"] == "a" * 40
    import shutil
    repeat = push(second, "retry")
    save_json(repeat / "metadata.json", metadata)
    shutil.copytree(second / "repository-context", repeat / "repository-context")
    if mode == "partial":
        assert not (second / "verdict.json").exists()
        # A complete same-head reassessment can explicitly withdraw the old claim.
        install(monkeypatch, {"security": ()})
        resumed = run(repeat, config)
        assert resumed.verdict.verdict == "approve"
        assert read_json(repeat / "iterative.json")["pool"][0]["disposition"] == "withdrawn"
    else:
        assert not result.verdict.findings
        assert run(repeat, config).verdict.verdict == "escalated"
