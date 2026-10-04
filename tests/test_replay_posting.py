"""Real local producers with fake network, then strictly offline artifact replay.

These fixtures prove artifact integration, not actual strategy/model execution.
"""

import hashlib
import json
import socket
import subprocess
from pathlib import Path

import pytest
from test_post_escalation import Escalator
from test_post_review import REF, CaptureClient, FakePoster

from scrutare.config import parse_config
from scrutare.engine.ingestion import ingest_pr
from scrutare.findings import Anchor, Exhaustion, Finding, dedupe_findings, derive_verdict
from scrutare.poster import post_escalation, post_review
from scrutare.poster.journal import canonical
from scrutare.replay import replay_run as replay_artifacts


def replay_run(run):
    with pytest.MonkeyPatch.context() as monkeypatch:
        forbid_external(monkeypatch)
        return replay_artifacts(run)


def read(run, name="posting.json"):
    return json.loads((run / name).read_bytes())


def write(run, document, name="posting.json"):
    (run / name).write_bytes(canonical(document))


def produced(tmp_path, *, category="security", exhausted=False, request=True, targets=True):
    raw = (b"models: {default: {model: test-model}}\n" +
           (b"github: {human_reviewers: [Alice, alice, service-bot]}\n" if targets else b""))
    config = parse_config(raw)
    run = ingest_pr(CaptureClient(), REF, tmp_path, config_bytes=raw,
                    config_data=config.to_dict())
    findings = dedupe_findings([
        Finding(Anchor("example.py", 1), category, "Problem", "Risk", "security"),
    ])
    verdict = derive_verdict(findings, config.verdict,
                             exhaustion=Exhaustion("panel", 3, 3) if exhausted else None)
    if exhausted and request:
        post_escalation(run, verdict, client=Escalator(run))
    else:
        post_review(run, verdict, client=FakePoster())
    return run, verdict


def forbid_external(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Replay attempted an external operation or artifact mutation")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("write_bytes", "write_text", "mkdir", "unlink", "rename", "replace", "touch"):
        monkeypatch.setattr(Path, name, forbidden)
    for target in (
        "scrutare.engine.github.resolve_pr", "scrutare.engine.github.GitHubClient.__init__",
        "scrutare.poster.client.ReviewClient.__init__", "scrutare.poster.posting.resolve_pr",
        "scrutare.poster.posting._capture", "scrutare.poster.posting._post_review_locked",
        "scrutare.poster.posting.run_lock", "scrutare.poster.posting.atomic_write",
        "scrutare.poster.escalation.resolve_pr", "scrutare.poster.escalation.run_lock",
        "scrutare.poster.escalation._validate_existing", "scrutare.poster.escalation._request",
        "scrutare.poster.journal.run_lock", "scrutare.poster.journal.atomic_write",
    ):
        monkeypatch.setattr(target, forbidden)


@pytest.mark.parametrize("category,exhausted,event", [
    ("docs", False, "APPROVE"), ("security", False, "REQUEST_CHANGES"),
    ("security", True, "COMMENT"),
])
def test_real_producer_bundles_replay_offline_without_changing_any_artifact(
    tmp_path, monkeypatch, category, exhausted, event,
):
    run, original = produced(tmp_path, category=category, exhausted=exhausted)
    assert read(run)["event"] == event
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    forbid_external(monkeypatch)
    result = replay_run(run)
    assert result.verdict.to_bytes() == original.to_bytes()
    assert result.saved_identical is True
    assert result.posted_identical is True
    assert result.posted_sha256 == hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.posting.status == "posted"
    assert result.posting.reviewer_request_status == ("posted" if exhausted else "absent")
    assert result.to_dict()["exhaustion_basis"] == ("recorded_assertion" if exhausted else "none")
    assert result.exit_code == 0
    assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before


def test_current_findings_and_matching_overwritten_saved_verdict_cannot_redefine_posted_target(
    tmp_path,
):
    run, original = produced(tmp_path)
    document = read(run, "findings.json")
    document[0]["sources"][0]["reason"] = "Changed reason"
    document[0]["reasons"] = ["Changed reason"]
    write(run, document, "findings.json")
    changed = replay_run(run)
    assert changed.posted_identical is False
    assert any(d.path == "verdict.findings[0].sources[0].reason" for d in changed.differences)
    (run / "verdict.json").write_bytes(changed.verdict.to_bytes())
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is False
    assert result.posted_sha256 == hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.differences == ()  # Semantic comparison is with saved bytes only.
    assert result.exit_code == 1


def test_formatting_only_saved_edit_preserves_original_posted_comparison(tmp_path):
    run, _ = produced(tmp_path)
    (run / "verdict.json").write_text(json.dumps(read(run, "verdict.json")))
    result = replay_run(run)
    assert result.saved_identical is False
    assert result.posted_identical is True
    assert [d.path for d in result.differences] == ["verdict.encoding"]
    assert result.exit_code == 1


@pytest.mark.parametrize("change", ["category", "policy", "model", "targets"])
def test_edited_current_evidence_recomputes_without_rebuilding_original_payload(tmp_path, change):
    run, _ = produced(tmp_path, exhausted=change == "targets")
    if change == "category":
        document = read(run, "findings.json")
        document[0]["sources"][0]["category"] = "docs"
        write(run, document, "findings.json")
    else:
        document = read(run, "config.json")
        if change == "policy":
            document["verdict"]["blocking_categories"].remove("security")
            document["verdict"]["advisory_categories"].append("security")
        elif change == "model":
            document["models"] = {"future": "unsupported"}
        else:
            document["github"]["human_reviewers"] = ["Other"]
        write(run, document, "config.json")
    result = replay_run(run)
    assert result.verdict is not None
    assert result.posted_identical is (change in ("model", "targets"))
    assert result.posting.status == "posted"
    if change in ("category", "policy"):
        assert result.verdict.verdict == "approve"
    if change != "category":
        assert any(i.code == "config_hash_disagrees" for i in result.posting.issues)
    if change == "targets":
        assert result.posting.reviewer_request_status == "posted"
    assert result.exit_code == 1


@pytest.mark.parametrize("missing", ["posting.json", "review-payload.json", "both"])
def test_absent_and_orphan_review_records_keep_saved_recomputation(tmp_path, missing):
    run, _ = produced(tmp_path)
    for name in (["posting.json", "review-payload.json"] if missing == "both" else [missing]):
        (run / name).unlink()
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posted_sha256 is None
    assert result.posting.status == ("absent" if missing == "both" else "invalid")
    assert result.exit_code == (0 if missing == "both" else 2)


@pytest.mark.parametrize("status,attempts,failure,http_status,retry_at", [
    ("prepared", 0, None, None, None), ("sending", 1, None, None, None),
    ("unknown", 1, None, None, None), ("rejected", 1, "unsent", None, None),
    ("rejected", 2, "throttle", 429, 10), ("rejected", 3, "permanent", 422, None),
])
def test_nonposted_states_are_incomplete_without_a_posted_target(
    tmp_path, status, attempts, failure, http_status, retry_at,
):
    run, _ = produced(tmp_path)
    state = read(run)
    state.update(status=status, attempts=attempts, receipt=None, failure=failure,
                 http_status=http_status, retry_at=retry_at)
    write(run, state)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posted_sha256 is None
    assert result.posting.status == status
    assert any(i.code == "review_not_posted" and i.severity == "incomplete"
               for i in result.posting.issues)
    assert result.exit_code == 2


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("pr_number", True), ("pr_number", 0),
    ("repository", "secret;pwd/repo"), ("repository", "owner/.."),
    ("head_sha", "secret"), ("run_id", "A" * 32), ("event", "APPROVE"),
    ("verdict_sha256", "F" * 64), ("diff_sha256", "0" * 63),
    ("config_sha256", None), ("payload_sha256", "0" * 64),
    ("status", []), ("status", "secret"), ("attempts", True), ("attempts", 0),
    ("attempts", 4), ("failure", "unsent"), ("retry_at", 0), ("http_status", 429),
    ("receipt", None), ("extra", "secret"),
])
def test_invalid_original_journal_cannot_claim_posted_identity(tmp_path, field, value):
    run, _ = produced(tmp_path)
    state = read(run)
    state[field] = value
    write(run, state)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posted_sha256 is None
    assert result.posting.status == "invalid"
    assert result.exit_code == 2
    assert "secret" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("field,value", [
    ("review_id", True), ("review_id", -1), ("html_url", "https://evil.example/secret"),
    ("html_url", "https://github.com/owner/repo/pull/13#pullrequestreview-42"),
    ("commit_id", "b" * 40), ("body", "secret"), ("state", "APPROVED"),
    ("login", "secret invalid"), ("extra", "secret"),
])
def test_invalid_original_receipt_cannot_claim_posted_identity(tmp_path, field, value):
    run, _ = produced(tmp_path)
    state = read(run)
    state["receipt"][field] = value
    write(run, state)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.exit_code == 2
    assert "secret" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("change", ["encoding", "extra", "anchor", "marker", "head", "event"])
def test_payload_shape_hash_marker_and_intent_links_are_checked(tmp_path, change):
    run, _ = produced(tmp_path)
    payload = read(run, "review-payload.json")
    if change == "extra":
        payload["extra"] = "secret"
    elif change == "anchor":
        payload["comments"][0]["line"] = True
    elif change == "marker":
        payload["body"] = payload["body"].replace(read(run)["run_id"], "f" * 32)
    elif change == "head":
        payload["commit_id"] = "b" * 40
    elif change == "event":
        payload["event"] = "APPROVE"
    raw = json.dumps(payload).encode() if change == "encoding" else canonical(payload)
    (run / "review-payload.json").write_bytes(raw)
    state = read(run)
    state["payload_sha256"] = hashlib.sha256(raw).hexdigest()
    state["receipt"]["body"] = payload["body"]
    write(run, state)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.exit_code == 2


@pytest.mark.parametrize("artifact", ["posting.json", "review-payload.json"])
@pytest.mark.parametrize("kind", ["invalid", "duplicate", "missing_field", "symlink"])
def test_unsafe_or_partial_posting_artifacts_return_safe_diagnostics(tmp_path, artifact, kind):
    run, _ = produced(tmp_path)
    path = run / artifact
    if kind == "invalid":
        path.write_bytes(b"secret\xff")
    elif kind == "duplicate":
        path.write_bytes(b'{"status":"secret","status":"posted"}')
    elif kind == "missing_field":
        document = read(run, artifact)
        del document[next(iter(document))]
        write(run, document, artifact)
    else:
        path.unlink()
        path.symlink_to(run / "missing")
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.exit_code == 2
    assert "secret" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("artifact,kind,code", [
    ("diff.patch", "missing", None), ("metadata.json", "missing", None),
    ("diff.patch", "edit", "diff_hash_disagrees"),
    ("metadata.json", "edit", "capture_identity_disagrees"),
])
def test_historical_capture_diagnostics_do_not_redefine_original_digest(
    tmp_path, artifact, kind, code,
):
    run, _ = produced(tmp_path)
    if kind == "missing":
        (run / artifact).unlink()
    elif artifact == "diff.patch":
        (run / artifact).write_bytes(b"changed raw diff\r\n")
    else:
        document = read(run, artifact)
        document["head_sha"] = "b" * 40
        write(run, document, artifact)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is True
    assert result.exit_code == (1 if code else 0)
    assert [i.code for i in result.posting.issues] == ([code] if code else [])


@pytest.mark.parametrize("with_request,targets,status", [
    (False, True, "absent"), (True, True, "posted"), (True, False, "skipped"),
])
def test_optional_request_stage_does_not_limit_lower_level_escalation(
    tmp_path, with_request, targets, status,
):
    run, _ = produced(tmp_path, exhausted=True, request=with_request, targets=targets)
    result = replay_run(run)
    assert result.posted_identical is True
    assert result.posting.reviewer_request_status == status
    assert result.exit_code == 0


@pytest.mark.parametrize("status,provenance", [
    ("prepared", None), ("sending", None), ("unknown", None),
    ("rejected", None), ("posted", "observed_requested"),
])
def test_pending_or_observed_request_preserves_posted_verdict_proof(tmp_path, status, provenance):
    run, _ = produced(tmp_path, exhausted=True)
    state = read(run, "escalation.json")
    state.update(status=status, attempts=0 if status == "prepared" else 1)
    if provenance:
        state["receipt"]["provenance"] = provenance
    else:
        state["receipt"] = None
    if status == "rejected":
        state.update(failure="permanent", http_status=422)
    write(run, state, "escalation.json")
    result = replay_run(run)
    assert result.posted_identical is True
    assert result.posting.reviewer_request_status == (provenance or status)
    assert result.to_dict()["exhaustion_basis"] == "recorded_assertion"
    assert result.exit_code == 0


@pytest.mark.parametrize("change", [
    "missing_journal", "missing_payload", "malformed", "hash", "receipt_hash", "review_id",
    "intent", "targets", "receipt_targets", "receipt_provenance", "attempts", "non_escalated",
])
def test_invalid_request_cross_links_invalidate_posting_provenance(tmp_path, change):
    run, _ = produced(tmp_path, exhausted=True)
    state = read(run, "escalation.json")
    if change == "missing_journal":
        (run / "escalation.json").unlink()
    elif change == "missing_payload":
        (run / "reviewer-request.json").unlink()
    elif change == "malformed":
        (run / "escalation.json").write_bytes(b"secret")
    else:
        if change == "hash":
            state["request_sha256"] = "0" * 64
        elif change == "receipt_hash":
            state["review_receipt_sha256"] = "0" * 64
        elif change == "review_id":
            state["review_id"] = True
        elif change == "intent":
            state["verdict_sha256"] = "0" * 64
        elif change == "targets":
            state["reviewers"] = ["Other"]
            request = canonical({"reviewers": ["Other"]})
            (run / "reviewer-request.json").write_bytes(request)
            state["request_sha256"] = hashlib.sha256(request).hexdigest()
            state["receipt"]["reviewers"] = ["Other"]
        elif change == "receipt_targets":
            state["receipt"]["reviewers"] = ["Other"]
        elif change == "receipt_provenance":
            state["receipt"]["provenance"] = "secret"
        elif change == "attempts":
            state["attempts"] = True
        else:
            posting = read(run)
            payload = read(run, "review-payload.json")
            payload["body"] = payload["body"].replace("Scrutare escalation", "Scrutare review")
            posting["receipt"]["body"] = payload["body"]
            write(run, payload, "review-payload.json")
            posting["payload_sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
            write(run, posting)
        write(run, state, "escalation.json")
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posted_sha256 is None
    assert result.to_dict()["exhaustion_basis"] == "unverified_recorded_assertion"
    assert result.exit_code == 2
    assert "secret" not in json.dumps(result.to_dict())


def test_changed_exhaustion_is_recomputed_but_remains_unverified(tmp_path):
    run, _ = produced(tmp_path, exhausted=True)
    document = read(run, "verdict.json")
    document["exhaustion"].update(rounds_completed=4, round_limit=4)
    write(run, document, "verdict.json")
    config = read(run, "config.json")
    config["rounds"]["max"] = 4
    write(run, config, "config.json")
    result = replay_run(run)
    assert result.verdict.exhaustion.rounds_completed == 4
    assert result.saved_identical is True
    assert result.posted_identical is False
    assert result.to_dict()["exhaustion_basis"] == "unverified_recorded_assertion"
    assert result.exit_code == 1


@pytest.mark.parametrize("artifact", ["verdict.json", "config.json"])
def test_posting_inspector_does_not_need_current_deciding_inputs(tmp_path, artifact, monkeypatch):
    from scrutare.replay.posting import inspect_posting

    run, original = produced(tmp_path)
    (run / artifact).unlink()
    forbid_external(monkeypatch)
    result = inspect_posting(run)
    assert result.status == "posted"
    assert result.verdict_sha256 == hashlib.sha256(original.to_bytes()).hexdigest()


def test_unavailable_saved_baseline_still_reports_original_posted_target(tmp_path):
    run, original = produced(tmp_path)
    (run / "verdict.json").unlink()
    result = replay_run(run)
    assert result.verdict is None
    assert result.posted_identical is None
    assert result.posted_sha256 == hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.posting.status == "posted"
    assert result.exit_code == 2


@pytest.mark.parametrize("field,value", [
    ("attempts", 0), ("attempts", 4), ("receipt", {}), ("failure", None),
    ("failure", "secret"), ("http_status", True), ("http_status", 500),
    ("retry_at", True), ("retry_at", -1), ("retry_at", "secret"),
    ("retry_at", 10**400),
])
def test_rejected_state_invariants_are_checked_before_posted_comparison(tmp_path, field, value):
    run, _ = produced(tmp_path)
    state = read(run)
    state.update(status="rejected", attempts=1, receipt=None, failure="throttle",
                 http_status=429, retry_at=10)
    state[field] = value
    write(run, state)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posting.status == "invalid"
    assert result.exit_code == 2
    assert "secret" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("name,field,value", [
    ("posting.json", "status", "prepared"),
    ("posting.json", "status", "sending"),
    ("posting.json", "status", "unknown"),
    ("posting.json", "receipt", {}),
    ("escalation.json", "request_sha256", "F" * 64),
    ("escalation.json", "review_receipt_sha256", "F" * 64),
    ("escalation.json", "reviewers", ["Alice", "alice", "service-bot"]),
    ("escalation.json", "extra", True),
])
def test_escalation_never_promotes_invalid_original_evidence(tmp_path, name, field, value):
    run, _ = produced(tmp_path, exhausted=True)
    state = read(run, name)
    state[field] = value
    write(run, state, name)
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.to_dict()["exhaustion_basis"] == "unverified_recorded_assertion"
    assert result.exit_code == 2


@pytest.mark.parametrize("field,value", [
    ("attempts", 1), ("failure", "unsent"), ("receipt", None),
    ("receipt", {"reviewers": [], "provenance": "observed_requested"}),
    ("receipt", {"reviewers": ["Alice"], "provenance": "post_response"}),
    ("status", "posted"),
])
def test_skipped_requests_require_zero_attempts_and_exact_no_targets_receipt(
    tmp_path, field, value,
):
    run, _ = produced(tmp_path, exhausted=True, targets=False)
    state = read(run, "escalation.json")
    state[field] = value
    write(run, state, "escalation.json")
    result = replay_run(run)
    assert result.posted_identical is None
    assert result.to_dict()["exhaustion_basis"] == "unverified_recorded_assertion"
    assert result.exit_code == 2


@pytest.mark.parametrize("artifact", ["escalation.json", "reviewer-request.json", "both"])
def test_orphan_request_without_any_review_is_invalid(tmp_path, artifact):
    run, _ = produced(tmp_path, exhausted=True)
    (run / "posting.json").unlink()
    (run / "review-payload.json").unlink()
    if artifact != "both":
        (run / artifact).unlink()
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posting.status == "invalid"
    assert result.posting.reviewer_request_status == "invalid"
    assert result.exit_code == 2


@pytest.mark.parametrize("artifact", ["diff.patch", "metadata.json"])
def test_malformed_optional_capture_reports_incomplete_but_retains_original_digest(
    tmp_path, artifact,
):
    run, original = produced(tmp_path)
    (run / artifact).unlink()
    (run / artifact).symlink_to(run / "missing")
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is True
    assert result.posted_sha256 == hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.exit_code == 2
    assert any(i.code == "capture_unreadable" and i.path == artifact for i in result.posting.issues)


def test_config_encoding_and_producer_version_are_not_format_identity(tmp_path):
    run, _ = produced(tmp_path)
    (run / "config.json").write_text(json.dumps(read(run, "config.json")))
    metadata = read(run, "metadata.json")
    metadata["scrutare_version"] = "9999"
    write(run, metadata, "metadata.json")
    result = replay_run(run)
    assert result.posted_identical is True
    assert result.saved_identical is True
    assert result.exit_code == 0


def test_conflicting_exhaustion_preserves_original_posted_target_without_candidate(tmp_path):
    run, original = produced(tmp_path, exhausted=True)
    config = read(run, "config.json")
    config["rounds"]["max"] = 4
    write(run, config, "config.json")
    result = replay_run(run)
    assert result.verdict is None
    assert result.posted_identical is None
    assert result.posted_sha256 == hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.posting.status == "posted"
    assert result.exit_code == 2


@pytest.mark.parametrize("targets", [
    "Escalation targets for human review: @Alice @alice @service-bot",
    "Escalation targets for human review: Alice @service-bot",
    "Escalation targets for human review: @Alice  @service-bot",
    "Escalation targets for human review: @secret/bad",
    "Escalation targets for human review: ",
    "secret invalid original target header",
    "This repository has no escalation targets configured.",
])
def test_original_escalation_target_header_must_match_request_without_current_config_fallback(
    tmp_path, targets,
):
    run, _ = produced(tmp_path, exhausted=True)
    state = read(run)
    payload = read(run, "review-payload.json")
    payload["body"] = payload["body"].replace(
        "Escalation targets for human review: @Alice @service-bot", targets,
    )
    write(run, payload, "review-payload.json")
    state["payload_sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    state["receipt"]["body"] = payload["body"]
    write(run, state)
    request = read(run, "escalation.json")
    request["payload_sha256"] = state["payload_sha256"]
    request["review_receipt_sha256"] = hashlib.sha256(canonical(state["receipt"])).hexdigest()
    write(run, request, "escalation.json")
    result = replay_run(run)
    assert result.saved_identical is True
    assert result.posted_identical is None
    assert result.posting.reviewer_request_status == "invalid"
    assert result.exit_code == 2
    assert "secret" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("artifact", ["metadata.json", "diff.patch"])
def test_unreadable_optional_capture_preserves_recorded_exhaustion_basis(tmp_path, artifact):
    run, original = produced(tmp_path, exhausted=True)
    (run / artifact).unlink()
    (run / artifact).symlink_to(run / "missing")
    result = replay_run(run)
    digest = hashlib.sha256(original.to_bytes()).hexdigest()
    assert result.saved_identical is True
    assert result.posted_identical is True
    assert result.saved_sha256 == result.posted_sha256 == digest
    assert result.posting.status == "posted"
    assert result.posting.verdict_sha256 == digest
    assert any(i.code == "capture_unreadable" and i.path == artifact
               and i.severity == "incomplete" for i in result.posting.issues)
    assert result.to_dict()["exhaustion_basis"] == "recorded_assertion"
    assert result.to_dict()["status"] == "incomplete"
    assert result.exit_code == 2
