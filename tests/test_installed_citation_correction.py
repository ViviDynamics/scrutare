"""Installed wheel and native nare 2026.10.31: one citation correction, on and off, replayed."""

import base64
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from test_installed_review import GH_WORKER
from test_installed_review import wheel_cli as wheel_cli
from test_nare_cli_integration import FINDING, offline_runtime, text, tool
from test_nare_cli_integration import installed as installed
from test_repository_context import Objects, blob_id
from test_review_inputs import SOURCE

PREFIX = "Scrutare could not accept"


def load(path):
    return json.loads(path.read_bytes())


def first_answer(provider, **changes):
    cited = dict(side="head", revision="a" * 40, path="src/caller.py", start_line=1,
                 end_line=1, sha256=sha256(provider.content["src/caller.py"]).hexdigest())
    cited.update(changes)
    finding = json.loads(json.dumps(FINDING))
    finding["findings"][0]["evidence"] = dict(
        version=2, trigger="changed call", preconditions=["caller runs"], expected="result",
        observed="failure", impact="lost result", citations=[cited])
    return finding


def fixed(provider, *, end_line=1, digest=None):
    return {"citations": [dict(
        id="c0001", revision="a" * 40, start_line=1, end_line=end_line,
        sha256=digest or sha256(provider.content["src/caller.py"]).hexdigest())]}


def review(tmp_path, installed, wheel_cli, *, answer, correction=None, enabled=True,
           structured=False, succeeds=True, policy="citation_correction", assessment=False,
           provider_error=False, correction_summary=None, limit=400):
    """Run `scrutare review` once against offline GitHub and native offline nare."""
    from scrutare.engine.repository_context import _artifact

    _, _, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    provider = Objects()
    objects = {}
    for repository, revision in (("owner/repo", "b" * 40), ("fork/repo", "a" * 40)):
        objects[f"repos/{repository}/git/commits/{revision}"] = {
            "sha": revision, "tree": {"sha": "c" * 40}}
        for tree in ("c" * 40, "e" * 40, "f" * 40, "d" * 40):
            objects[f"repos/{repository}/git/trees/{tree}"] = {
                "sha": tree, "tree": provider.get_tree(repository, tree), "truncated": False}
        for data in provider.content.values():
            blob = blob_id(data)
            objects[f"repos/{repository}/git/blobs/{blob}"] = {
                "sha": blob, "size": len(data), "encoding": "base64",
                "content": base64.b64encode(data).decode()}
    flag = {"structured_output": True} if structured else {}
    replies = [tool(args={"path": "repository-context.json"}),
               tool(args={"path": _artifact("head", "src/caller.py")}, call_id="caller"),
               text(answer(provider))]
    correction_replies = [] if correction is None else [text(correction(provider))]
    if correction_summary is not None:
        correction_replies.append(text(correction_summary(provider)))
    scenarios = [] if correction is None else [{
        "purpose": "review", "attempt": "attempt-0001", "prompt_prefix": PREFIX,
        "scenario": {"replies": correction_replies, **flag}}]
    if assessment:
        scenarios.extend([
            {"purpose": "review", "attempt": "attempt-0001",
             "prompt_prefix": "Independently assess", "scenario": {"replies": [
                 {"assess_candidates": "supported", "extra_assessment": True}], **flag}},
            {"purpose": "review", "attempt": "attempt-0001",
             "prompt_prefix": PREFIX, "scenario": {"replies": [
                 {"assess_candidates": "supported"}], **flag}},
        ])
    (tmp_path / "offline-spec.json").write_text(json.dumps(
        {"default": {"replies": replies, **flag,
          **({"error": "offline provider failure"} if provider_error else {})},
         "scenarios": scenarios}))
    working = tmp_path / "outside-checkout"
    working.mkdir()
    rail = ', structured_output: json_schema' if structured else ''
    (working / "scrutare.yaml").write_text(
        "personas: [senior-dev]\n"
        "models: {default: {provider: openai, model: offline-model, "
        f'base_url: "https://offline.invalid/v1"{rail}}}}}\n'
        f"budgets: {{per_persona_tokens: {limit}, review_max_tokens: {limit * 2}}}\n"
        "context: {enabled: true, related_paths: [src/caller.py, tests/test_caller.py]}\n"
        "findings: {evidence: v2" + (f", {policy}: true" if enabled else "")
        + (", assessment: {enabled: true, tokens: 400}" if assessment else "") + "}\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_WORKER.read_text())
    gh.chmod(0o700)
    spec = tmp_path / "gh-spec.json"
    spec.write_text(json.dumps({
        "case": "context", "objects": objects, "diff": SOURCE.decode(),
        "files": [{"filename": "src/app.py", "status": "modified"}],
        "metadata": {"number": 12, "state": "open", "merged": False, "changed_files": 1,
                     "head": {"sha": "a" * 40, "repo": {"full_name": "fork/repo"}},
                     "base": {"ref": "main", "sha": "b" * 40,
                              "repo": {"full_name": "owner/repo"}}}}))
    env = {"PATH": f"{binary}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8",
           "SCRUTARE_GH_SPEC": str(spec), "SCRUTARE_GH_LOG": str(tmp_path / "gh.jsonl"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable)],
        cwd=working, env=env, capture_output=True, timeout=90)
    assert (result.returncode == 0) == succeeds, result.stderr.decode()
    run, = {path.parent.parent.parent for path in working.rglob("sessions/*/attempt-0001")}
    attempt = run / "sessions/senior-dev/attempt-0001"
    for path in run.rglob("offline-observations*.json"):
        observed = load(path)
        assert observed["guard_violations"] == observed["credential_names"] == []
        assert observed["version"] == "2026.10.31"
    replay = subprocess.run([str(console), "replay", str(run)], cwd=working, env=env,
                            capture_output=True, timeout=30)
    return result, run, attempt, replay, provider


def resumed(attempt):
    return load(attempt / "offline-observations.resume.json")


def test_installed_a_past_end_citation_is_corrected_and_replays(tmp_path, installed, wheel_cli):
    result, run, attempt, replay, provider = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: fixed(p, end_line=1))
    envelope = json.loads(result.stdout)
    assert (envelope["verdict"], envelope["panel_status"]) == ("changes_requested", "complete")
    [cited] = load(run / "verdict.json")["findings"][0]["sources"][0]["evidence"]["citations"]
    assert (cited["end_line"], cited["validation"]) == (1, "valid")
    outcome = load(attempt / "result.json")
    assert outcome["citation_correction"] == {"offered": True, "defects": 1,
                                              "outcome": "corrected", "exit_code": 0}
    record = load(attempt / "citation-correction.json")
    assert record["defects"][0]["last_line"] == 1 and record["defects"][0]["end_line"] == 2
    # Real nare resumed the same session for one turn with no tools and the correction schema.
    observed = resumed(attempt)
    assert len(observed["calls"]) == 1
    call = observed["calls"][0]
    assert call["tools"] == []
    blocks = call["messages"][-1]["content"]
    assert [b["text"] for b in blocks if b.get("text", "").startswith(PREFIX)] == [record["prompt"]]
    assert blocks[-1]["text"] == "1 turns left: wrap up and summarise."
    assert any(block.get("type") == "tool_result" for message in call["messages"]
               for block in message["content"])  # the reads it made are still in context
    saved = load(attempt / "correction-session.json")
    assert saved["id"] == load(attempt / "session.json")["id"]
    assert saved["policy"]["tools"] == [] and saved["status"] == "done"
    assert outcome["usage"]["input"] == saved["usage"]["input"]
    assert envelope["usage"]["total"] == outcome["usage"]["total"]
    assert replay.returncode == 0, replay.stderr.decode()
    replayed = json.loads(replay.stdout)
    assert replayed["saved_verdict"]["byte_identical"] is True and replayed["issues"] == []


def test_installed_a_placeholder_hash_is_corrected_under_the_constraint(
        tmp_path, installed, wheel_cli):
    result, run, attempt, replay, provider = review(
        tmp_path, installed, wheel_cli, structured=True,
        answer=lambda p: first_answer(p, sha256="334e0..placeholder"),
        correction=lambda p: fixed(p))
    assert json.loads(result.stdout)["verdict"] == "changes_requested"
    record = load(attempt / "citation-correction.json")
    assert record["refusal"] == {"reason": "protocol", "citation_reason": None}
    assert "334e0..placeholder" not in json.dumps(record)
    call = resumed(attempt)["calls"][0]
    # A tool-free run on a constrained rail decodes every turn under the correction schema.
    assert call["response_schema"] == load(attempt / "citation-correction.schema.json")
    assert call["tools"] == []
    events = [json.loads(line) for line in
              (attempt / "correction.stdout.jsonl").read_text().splitlines()]
    assert [e["detail"]["structured_output"] for e in events
            if e["type"] == "progress" and "structured_output" in e["detail"]] == [
        {"mode": "json_schema", "tools_withheld": False}]
    assert replay.returncode == 0 and json.loads(replay.stdout)["issues"] == []


def test_installed_a_correction_that_fails_again_fails_the_review(tmp_path, installed, wheel_cli):
    _, run, attempt, replay, provider = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: fixed(p, end_line=2), succeeds=False)
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("failed", "citation_validation")
    assert outcome["citation_correction"]["outcome"] == "refused"
    assert load(attempt / "citation-validation.json")["reason"] == "line_range_outside_capture"
    assert not (run / "verdict.json").exists()
    assert load(run / "panel.json")["status"] == "failed"
    # No verdict to replay; the retained sessions still audit clean.
    from scrutare.replay.audit import _citation_correction_issues
    assert _citation_correction_issues(run, load(run / "config.json")) == ()


def test_installed_off_a_past_end_citation_fails_as_before(tmp_path, installed, wheel_cli):
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: fixed(p), enabled=False, succeeds=False)
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("failed", "citation_validation")
    assert "citation_correction" not in outcome
    assert "citation_correction" not in load(run / "config.json")["findings"]
    assert not (attempt / "offline-observations.resume.json").exists()
    assert not list(attempt.glob("*correction*"))


@pytest.mark.parametrize("changes", [{"sha256": "0" * 64}, {"revision": "b" * 40}],
                         ids=["wrong-hash", "wrong-revision"])
def test_installed_an_undescribable_refusal_is_not_offered(tmp_path, installed, wheel_cli,
                                                           changes):
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, **changes),
        correction=lambda p: fixed(p), succeeds=False)
    outcome = load(attempt / "result.json")
    assert outcome["status"] == "failed"
    assert outcome["citation_correction"] == {"offered": False, "defects": 0, "outcome": None,
                                              "exit_code": None}
    assert not (attempt / "offline-observations.resume.json").exists()
    assert not (attempt / "citation-correction.json").exists()
    assert Path(attempt).is_dir()


def test_installed_a_correction_with_no_valid_answer_keeps_the_original_refusal(
        tmp_path, installed, wheel_cli):
    # Off the constraint, a schema-invalid answer reaches the turn limit. Nare's
    # tool-free summary request has no scripted reply here; the original refusal stands.
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli, answer=lambda p: first_answer(p, end_line=2),
        correction=lambda p: {"corrections": []}, succeeds=False)
    outcome = load(attempt / "result.json")
    assert (outcome["status"], outcome["reason"]) == ("failed", "citation_validation")
    assert outcome["citation_correction"] == {"offered": True, "defects": 1,
                                              "outcome": "no_answer", "exit_code": 1}
    terminal = json.loads((attempt / "correction.stdout.jsonl").read_text().splitlines()[-1])
    assert (terminal["status"], terminal["stop_reason"]) == ("error", "max_turns")
    calls = resumed(attempt)["calls"]
    assert len(calls) == 2 and all(call["tools"] == [] for call in calls)
    assert calls[-1]["messages"][-1]["content"][-1]["text"].startswith("Turn limit reached.")
    assert load(attempt / "correction-session.json")["turns"] == (
        load(attempt / "session.json")["turns"] + 1)
    assert outcome["usage"]["total"] == terminal["usage"]["input"] + terminal["usage"]["output"]
    from scrutare.replay.audit import _citation_correction_issues
    assert _citation_correction_issues(run, load(run / "config.json")) == ()


@pytest.mark.parametrize("policy", ["citation_correction", "protocol_correction"])
def test_installed_turn_limit_summary_is_accounted_but_cannot_correct_the_refusal(
        tmp_path, installed, wheel_cli, policy):
    from test_installed_protocol_correction import placeholder

    citation = policy == "citation_correction"
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli,
        answer=(lambda p: first_answer(p, end_line=2)) if citation else placeholder,
        correction=lambda p: {"wrong": []},
        correction_summary=fixed if citation else first_answer,
        policy=policy, succeeds=False)
    outcome = load(attempt / "result.json")
    assert outcome[policy]["outcome"] == "no_answer"
    assert outcome["status"] == "failed" and outcome["findings"] == []
    assert not (run / "verdict.json").exists()
    original = load(attempt / "session.json")
    summary = load(attempt / "correction-session.json")
    assert (summary["status"], summary["stop_reason"]) == ("error", "max_turns")
    assert summary["turns"] == original["turns"] + 2
    assert summary["output"] != original["output"]
    assert outcome["usage"]["total"] == 75
    assert outcome["usage"]["input"] == summary["usage"]["input"] == 50
    assert outcome["usage"]["output"] == summary["usage"]["output"] == 25
    calls = resumed(attempt)["calls"]
    assert len(calls) == 2 and all(call["tools"] == [] for call in calls)
    assert calls[-1]["messages"][-1]["content"][-1]["text"].startswith("Turn limit reached.")
    from scrutare.replay.audit import _citation_correction_issues, _protocol_correction_issues

    audit = _citation_correction_issues if citation else _protocol_correction_issues
    assert audit(run, load(run / "config.json")) == ()


@pytest.mark.parametrize("policy", ["citation_correction", "protocol_correction"])
@pytest.mark.parametrize("limit", [59, 60])
def test_installed_exhausted_correction_budget_prevents_the_summary_request(
        tmp_path, installed, wheel_cli, policy, limit):
    from test_installed_protocol_correction import placeholder

    citation = policy == "citation_correction"
    _, run, attempt, _, _ = review(
        tmp_path, installed, wheel_cli,
        answer=(lambda p: first_answer(p, end_line=2)) if citation else placeholder,
        correction=lambda p: {"wrong": []},
        correction_summary=fixed if citation else first_answer,
        policy=policy, succeeds=False, limit=limit)
    outcome = load(attempt / "result.json")
    assert outcome[policy]["outcome"] == "no_answer"
    assert outcome["status"] == "failed" and outcome["findings"] == []
    assert not (run / "verdict.json").exists()
    terminal = load(attempt / "correction-session.json")
    assert (terminal["status"], terminal["stop_reason"]) == ("error", "budget")
    assert terminal["turns"] == load(attempt / "session.json")["turns"] + 1
    assert outcome["usage"]["total"] == 60
    assert len(resumed(attempt)["calls"]) == 1
    from scrutare.replay.audit import _citation_correction_issues, _protocol_correction_issues

    audit = _citation_correction_issues if citation else _protocol_correction_issues
    assert audit(run, load(run / "config.json")) == ()
