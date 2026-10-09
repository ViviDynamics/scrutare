"""Installed wheel and external nare trace immutable related source outside a checkout."""

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


@pytest.mark.parametrize("strategy", ["panel", "debate", "iterative"])
@pytest.mark.parametrize("evidence_mode", ["legacy", "v2", "invalid"])
def test_installed_context_all_strategies(
    tmp_path, installed, wheel_cli, strategy, evidence_mode, assessment_status=None,
):
    _, _, console, _ = wheel_cli
    runtime = offline_runtime(tmp_path, installed)
    provider = Objects()
    objects = {}
    for repository, revision in (("owner/repo", "b" * 40), ("fork/repo", "a" * 40)):
        objects[f"repos/{repository}/git/commits/{revision}"] = {
            "sha": revision,
            "tree": {"sha": "c" * 40},
        }
        for sha in ("c" * 40, "e" * 40, "f" * 40, "d" * 40):
            objects[f"repos/{repository}/git/trees/{sha}"] = {
                "sha": sha,
                "tree": provider.get_tree(repository, sha),
                "truncated": False,
            }
        for data in provider.content.values():
            sha = blob_id(data)
            objects[f"repos/{repository}/git/blobs/{sha}"] = {
                "sha": sha,
                "size": len(data),
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
            }
    from scrutare.engine.repository_context import _artifact

    finding = json.loads(json.dumps(FINDING))
    if evidence_mode != "legacy":
        finding["findings"][0]["evidence"] = dict(
            version=2, trigger="changed call", preconditions=["caller runs"],
            expected="result", observed="failure", impact="lost result",
            citations=[dict(side="head", revision="a" * 40, path="src/caller.py",
                            start_line=1, end_line=1,
                            sha256=sha256(provider.content["src/caller.py"]).hexdigest())])
        if evidence_mode == "invalid":
            finding["findings"][0]["evidence"]["citations"][0]["end_line"] = 999
    if evidence_mode == "v2":
        # Equal assertions are distinct caller-owned candidate occurrences.
        finding["findings"].append(json.loads(json.dumps(finding["findings"][0])))
    replies = [
        tool(args={"path": "repository-context.json"}),
        tool(args={"path": _artifact("head", "src/caller.py")}, call_id="caller"),
        tool(args={"path": _artifact("head", "tests/test_caller.py")}, call_id="test"),
        text(finding),
    ]
    (tmp_path / "offline-spec.json").write_text(
        json.dumps(
            {
                "default": {"replies": replies},
                "scenarios": ([{
                    "purpose": "review", "attempt": "attempt-0001",
                    "prompt_prefix": "Independently assess",
                    "scenario": {"replies": [{"assess_candidates": assessment_status}]},
                }] if assessment_status is not None else []) + [
                    {"purpose": "review", "attempt": "attempt-0003",
                     "prompt_prefix": "Reconsider your position",
                     "scenario": {"replies": [{"select_pool": True}]
                                  if evidence_mode != "legacy" else [text(FINDING)]}},
                    {
                        "purpose": "review",
                        "attempt": "attempt-0001",
                        "prompt_prefix": "Arbitrate as senior",
                        "scenario": {"replies": [
                            {"select_pool": True} if evidence_mode != "legacy"
                            else text(FINDING | {"converged": True})]},
                    }
                ],
            }
        )
    )
    working = tmp_path / "outside-checkout"
    working.mkdir()
    (working / "scrutare.yaml").write_text(
        f"strategy: {strategy}\nrounds: {{max: 2}}\npersonas: [senior-dev]\n"
        "models: {default: {provider: openai, model: offline-model, "
        'base_url: "https://offline.invalid/v1"}}\n'
        "budgets: {per_persona_tokens: 400, review_max_tokens: 1200}\n"
        "context: {enabled: true, related_paths: [src/caller.py, tests/test_caller.py]}\n"
    )
    if evidence_mode != "legacy":
        with (working / "scrutare.yaml").open("a") as stream:
            stream.write("findings: {evidence: v2" + (
                ", assessment: {enabled: true, tokens: 400}" if assessment_status else ""
            ) + "}\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_WORKER.read_text())
    gh.chmod(0o700)
    spec = tmp_path / "gh-spec.json"
    spec.write_text(
        json.dumps(
            {
                "case": "context",
                "objects": objects,
                "diff": SOURCE.decode(),
                "files": [{"filename": "src/app.py", "status": "modified"}],
                "metadata": {
                    "number": 12,
                    "state": "open",
                    "merged": False,
                    "changed_files": 1,
                    "head": {"sha": "a" * 40, "repo": {"full_name": "fork/repo"}},
                    "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
                },
            }
        )
    )
    env = {
        "PATH": f"{binary}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "SCRUTARE_GH_SPEC": str(spec),
        "SCRUTARE_GH_LOG": str(tmp_path / "gh.jsonl"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable)],
        cwd=working,
        env=env,
        capture_output=True,
        timeout=60,
    )
    if evidence_mode == "invalid":
        assert result.returncode != 0
        assert not result.stdout
        assert not list(working.rglob("verdict.json"))
        validations = list(working.rglob("citation-validation.json"))
        assert validations
        assert all(json.loads(p.read_bytes())["reason"] == "line_range_outside_capture"
                   for p in validations)
        assert list(working.rglob("stdout.jsonl"))
        return
    assert result.returncode == 0, result.stderr.decode()
    run = Path(json.loads(result.stdout)["run_dir"])
    if assessment_status is not None:
        verdict = json.loads((run / "verdict.json").read_bytes())
        assessment = json.loads((run / "assessment.json").read_bytes())
        assert assessment["status"] == "complete"
        assert len(assessment["candidates"]) == 2
        assert {row["status"] for row in assessment["assessments"]} == {assessment_status}
        assert verdict["verdict"] == {"supported": "changes_requested",
                                      "refuted": "approve",
                                      "unresolved": "escalated"}[assessment_status]
    if assessment_status == "unresolved":
        payload = json.loads((run / "review-payload.json").read_bytes())
        assert payload["event"] == "COMMENT"
        assert len(verdict["findings"][0]["sources"]) == 2
        assert "unresolved_semantic_assessment" in payload["body"]
        assert "Unresolved candidate IDs" in payload["body"]
    if evidence_mode == "v2" and assessment_status not in ("refuted", "unresolved"):
        verdict = json.loads((run / "verdict.json").read_bytes())
        assert verdict["schema_version"] == 2
        sources = verdict["findings"][0]["sources"]
        assert len(sources) == 2
        identifiers = {source["candidate_id"] for source in sources}
        assert len(identifiers) == 2
        assert sources[0]["candidate_id"]
        assert sources[0]["evidence"]["citations"][0]["validation"] == "valid"
        result_sources = [source for path in run.rglob("result.json")
                          for source in json.loads(path.read_bytes()).get("findings", [])]
        assert all(s["candidate_id"] in identifiers for s in result_sources)
    roots = list(run.rglob("review-inputs/repository-context.json"))
    assert roots
    observations = list(run.rglob("offline-observations.json"))
    serialized = "\n".join(path.read_text() for path in observations)
    assert "call_changed()" in serialized and "assert caller()" in serialized
    assert all(json.loads(path.read_bytes())["guard_violations"] == [] for path in observations)
    assert all(json.loads(path.read_bytes())["credential_names"] == [] for path in observations)
    replay = subprocess.run(
        [str(console), "replay", str(run)], cwd=working, env=env, capture_output=True, timeout=30
    )
    assert replay.returncode == 0, replay.stderr.decode()

    if evidence_mode == "v2" and strategy == "iterative" and assessment_status is None:
        repeat = subprocess.run(
            [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable)],
            cwd=working, env=env, capture_output=True, timeout=60)
        assert repeat.returncode == 0, repeat.stderr.decode()
        repeated_run = Path(json.loads(repeat.stdout)["run_dir"])
        repeated_verdict = json.loads((repeated_run / "verdict.json").read_bytes())
        assert repeated_verdict["findings"] == verdict["findings"]
        assert not list(repeated_run.rglob("stdout.jsonl"))
        repeated_replay = subprocess.run([str(console), "replay", str(repeated_run)],
            cwd=working, env=env, capture_output=True, timeout=30)
        assert repeated_replay.returncode == 0, repeated_replay.stderr.decode()

        # A new commit with identical retained bytes still needs fresh commit citations.
        gh_spec = json.loads(spec.read_bytes())
        gh_spec["metadata"]["head"]["sha"] = "9" * 40
        commit = dict(objects["repos/fork/repo/git/commits/" + "a" * 40])
        commit["sha"] = "9" * 40
        gh_spec["objects"]["repos/fork/repo/git/commits/" + "9" * 40] = commit
        spec.write_text(json.dumps(gh_spec))
        provider_spec_path = tmp_path / "offline-spec.json"
        provider_spec = json.loads(provider_spec_path.read_bytes())
        for source in finding["findings"]:
            source["evidence"]["citations"][0]["revision"] = "9" * 40
        provider_spec["default"]["replies"][-1] = text(finding)
        provider_spec_path.write_text(json.dumps(provider_spec))
        changed = subprocess.run(
            [str(console), "review", "--pr", "12", "--nare-executable", str(runtime.executable)],
            cwd=working, env=env, capture_output=True, timeout=60)
        assert changed.returncode == 0, changed.stderr.decode()
        changed_run = Path(json.loads(changed.stdout)["run_dir"])
        changed_verdict = json.loads((changed_run / "verdict.json").read_bytes())
        changed_sources = changed_verdict["findings"][0]["sources"]
        assert len(changed_sources) == 2
        assert all(source["candidate_id"] not in identifiers for source in changed_sources)
        assert all(source["evidence"]["citations"][0]["revision"] == "9" * 40
                   for source in changed_sources)
        history = json.loads((changed_run / "iterative.json").read_bytes())
        assert len(history["pool"]) == 4
        assert {entry["finding"]["candidate_id"] for entry in history["pool"]
                if entry["disposition"] == "withdrawn"} == identifiers
        changed_replay = subprocess.run([str(console), "replay", str(changed_run)],
            cwd=working, env=env, capture_output=True, timeout=30)
        assert changed_replay.returncode == 0, changed_replay.stderr.decode()


@pytest.mark.parametrize("strategy", ["panel", "debate", "iterative"])
@pytest.mark.parametrize("status", ["supported", "refuted", "unresolved"])
def test_installed_independent_assessment_all_strategies(tmp_path, installed, wheel_cli,
                                                        strategy, status):
    test_installed_context_all_strategies(tmp_path, installed, wheel_cli, strategy, "v2", status)
