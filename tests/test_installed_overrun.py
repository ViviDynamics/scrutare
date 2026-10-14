"""Installed wheel and native nare: an opt-in phase overrun falls back, posts and replays."""

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

# One reply far past any grant in these budgets: native nare measures it after the turn.
OVER = {"input": 900, "output": 5}


def review(tmp_path, installed, wheel_cli, *, yaml, scenarios, category="correctness"):
    """Run `scrutare review` once against offline GitHub and native offline nare."""
    from scrutare.engine.repository_context import _artifact

    _, _, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    provider = Objects()
    objects = {}
    for repository, revision in (("owner/repo", "b" * 40), ("fork/repo", "a" * 40)):
        objects[f"repos/{repository}/git/commits/{revision}"] = {
            "sha": revision, "tree": {"sha": "c" * 40}}
        for sha in ("c" * 40, "e" * 40, "f" * 40, "d" * 40):
            objects[f"repos/{repository}/git/trees/{sha}"] = {
                "sha": sha, "tree": provider.get_tree(repository, sha), "truncated": False}
        for data in provider.content.values():
            sha = blob_id(data)
            objects[f"repos/{repository}/git/blobs/{sha}"] = {
                "sha": sha, "size": len(data), "encoding": "base64",
                "content": base64.b64encode(data).decode()}
    finding = json.loads(json.dumps(FINDING))
    finding["findings"][0]["category"] = category
    finding["findings"][0]["evidence"] = dict(
        version=2, trigger="changed call", preconditions=["caller runs"],
        expected="result", observed="failure", impact="lost result",
        citations=[dict(side="head", revision="a" * 40, path="src/caller.py",
                        start_line=1, end_line=1,
                        sha256=sha256(provider.content["src/caller.py"]).hexdigest())])
    replies = [tool(args={"path": "repository-context.json"}),
               tool(args={"path": _artifact("head", "src/caller.py")}, call_id="caller"),
               text(finding)]
    (tmp_path / "offline-spec.json").write_text(json.dumps(
        {"default": {"replies": replies}, "scenarios": scenarios}))
    working = tmp_path / "outside-checkout"
    working.mkdir()
    (working / "scrutare.yaml").write_text(
        "personas: [senior-dev]\n"
        "models: {default: {provider: openai, model: offline-model, "
        'base_url: "https://offline.invalid/v1"}}\n'
        "budgets: {per_persona_tokens: 400, review_max_tokens: 1200}\n"
        "context: {enabled: true, related_paths: [src/caller.py, tests/test_caller.py]}\n"
        + yaml)
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
        cwd=working, env=env, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode()
    envelope = json.loads(result.stdout)
    run = Path(envelope["run_dir"])
    replay = subprocess.run([str(console), "replay", str(run)], cwd=working, env=env,
                            capture_output=True, timeout=30)
    assert replay.returncode == 0, replay.stderr.decode()
    assert json.loads(replay.stdout)["saved_verdict"]["byte_identical"] is True
    observations = list(run.rglob("offline-observations.json"))
    assert observations
    for path in observations:
        observed = json.loads(path.read_bytes())
        assert observed["guard_violations"] == observed["credential_names"] == []
        assert observed["version"] == "2026.10.4"
    payload = json.loads((run / "review-payload.json").read_bytes())
    return envelope, run, payload


def load(path):
    return json.loads(path.read_bytes())


@pytest.mark.parametrize("strategy", ["panel", "debate"])
@pytest.mark.parametrize("category,want,event", [
    ("correctness", "changes_requested", "REQUEST_CHANGES"),
    ("style", "escalated", "COMMENT"),
])
def test_installed_assessor_overrun_keeps_anchor_validated_findings(
        tmp_path, installed, wheel_cli, strategy, category, want, event):
    envelope, run, payload = review(
        tmp_path, installed, wheel_cli, category=category,
        yaml=(f"strategy: {strategy}\nrounds: {{max: 1}}\nfindings: {{evidence: v2, "
              "assessment: {enabled: true, tokens: 400, overrun: fallback}}\n"),
        scenarios=[{"purpose": "review", "attempt": "attempt-0001",
                    "prompt_prefix": "Independently assess",
                    "scenario": {"replies": [{"assess_candidates": "refuted", "usage": OVER}]}}])
    assert envelope["verdict"] == want
    assert envelope["panel_status"] == "partial"
    assert envelope["accounting_complete"] is True
    verdict = load(run / "verdict.json")
    degraded = verdict["degraded"]
    assert (degraded["phase"], degraded["fallback_stage"]) == ("assessment", "anchor_validated")
    assert degraded["allowance_tokens"] == degraded["grant_tokens"] == 400
    assert degraded["usage_tokens"] > 400 and degraded["overrun_tokens"] > 0
    # The overrun assessor's refutation is never applied: the finding survives.
    assert len(verdict["findings"]) == 1
    assessment = load(run / "assessment.json")
    assert (assessment["outcome"]["status"], assessment["outcome"]["reason"]) == (
        "partial", "budget")
    assert payload["event"] == event
    assert "Degraded: the assessment phase overran its grant" in payload["body"]


def test_installed_position_overrun_keeps_the_pre_round_assessment(tmp_path, installed,
                                                                   wheel_cli):
    envelope, run, payload = review(
        tmp_path, installed, wheel_cli,
        yaml=("strategy: debate\nrounds: {max: 1}\nfindings: {evidence: v2, "
              "assessment: {enabled: true, tokens: 400}, "
              "discussion: {enabled: true, overrun: fallback}}\n"),
        scenarios=[
            {"purpose": "review", "attempt": "attempt-0001",
             "prompt_prefix": "Independently assess",
             "scenario": {"replies": [{"assess_candidates": "unresolved"}]}},
            {"purpose": "review", "attempt": "attempt-0003",
             "prompt_prefix": "Focused evidence discussion",
             "scenario": {"replies": [{"propose_candidates": "retain", "usage": OVER}]}},
        ])
    # An unresolved potentially blocking claim still escalates under the existing rule.
    assert (envelope["verdict"], envelope["rule"]) == (
        "escalated", "unresolved_semantic_assessment")
    verdict = load(run / "verdict.json")
    assert verdict["degraded"]["phase"] == "discussion"
    assert verdict["degraded"]["round"] == 1
    assert verdict["degraded"]["overrun_tokens"] > 0
    trace = load(run / "debate.json")
    assert (trace["status"], trace["reason"]) == ("partial", "discussion_overrun_fallback")
    assert trace["rounds"][0]["positions"][0]["reason"] == "budget"
    assert payload["event"] == "COMMENT"
    assert "Degraded: the discussion phase overran its grant in round 1" in payload["body"]


def test_installed_discussion_allowance_admits_positions_separately(tmp_path, installed,
                                                                    wheel_cli):
    envelope, run, _ = review(
        tmp_path, installed, wheel_cli,
        yaml=("strategy: debate\nrounds: {max: 1}\nfindings: {evidence: v2, "
              "assessment: {enabled: true, tokens: 300}, "
              "discussion: {enabled: true, tokens: 300}}\n"),
        scenarios=[
            {"purpose": "review", "attempt": "attempt-0001",
             "prompt_prefix": "Independently assess",
             "scenario": {"replies": [{"assess_candidates": "unresolved"}]}},
            {"purpose": "review", "attempt": "attempt-0003",
             "prompt_prefix": "Focused evidence discussion",
             "scenario": {"replies": [{"propose_candidates": "retain"}]}},
            {"purpose": "review", "attempt": "attempt-0002",
             "prompt_prefix": "Independently assess",
             "scenario": {"replies": [{"assess_candidates": "supported"}]}},
        ])
    assert (envelope["verdict"], envelope["panel_status"]) == ("changes_requested", "complete")
    ledger = load(run / "debate.json")["ledger"]
    assert ledger["allocations"] == {"senior-dev": 400, "evidence-assessor": 300}
    assert ledger["configured"]["phase_allowances"] == {"discussion": 300}
    phase = ledger["phases"][0]
    position = load(run / "sessions/senior-dev/attempt-0003/result.json")
    reassessment = load(run / "assessment-round-0001.json")["outcome"]
    assert position["allocated_tokens"] == 300
    assert reassessment["allocated_tokens"] == 300 - position["usage"]["total"]
    assert phase["usage"]["total"] == (position["usage"]["total"]
                                       + reassessment["usage"]["total"])
    assert ledger["usage"]["total"] == envelope["usage"]["total"]
