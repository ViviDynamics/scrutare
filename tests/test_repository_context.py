"""Immutable bounded repository evidence and its public read-root contract."""

import base64
import json
import subprocess
from dataclasses import replace
from hashlib import sha1
from unittest.mock import Mock

import pytest
from test_review_inputs import CONFIG, read_json, save_json
from test_review_inputs import capture as capture

from scrutare.config import ConfigError, parse_config
from scrutare.engine.github import GitHubClient, GitHubError


def test_context_defaults_and_explicit_rules():
    config = parse_config(CONFIG)
    assert not config.context.enabled
    assert "context" not in config.to_dict()
    enabled = parse_config(CONFIG + b"context: {enabled: true, related_paths: [src/caller.py]}\n")
    assert enabled.context.related_paths == ("src/caller.py",)
    assert enabled.to_dict()["context"]["enabled"]
    assert parse_config(json.dumps(enabled.to_dict())) == enabled


@pytest.mark.parametrize(
    "settings",
    [
        "{enabled: 1}",
        "{max_file_bytes: 0}",
        "{related_paths: ['../escape']}",
        "{include: ['/absolute']}",
        "{unknown: true}",
    ],
)
def test_context_settings_refuse_invalid_configuration(settings):
    with pytest.raises(ConfigError):
        parse_config(CONFIG + f"context: {settings}\n".encode())


def blob_id(data):
    return sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def test_git_blob_transport_uses_exact_object_identity():
    data = b"caller()\r\n"
    sha = blob_id(data)
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "sha": sha,
                    "size": len(data),
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode(),
                }
            ).encode(),
            b"",
        )
    )
    client = GitHubClient(runner)
    assert client.get_blob("fork/repo", sha, max_bytes=100) == data
    assert runner.call_args.args[0][2] == f"repos/fork/repo/git/blobs/{sha}"
    with pytest.raises(GitHubError):
        client.get_blob("fork/repo", "main", max_bytes=100)
    assert runner.call_count == 1
    runner.return_value.stdout = json.dumps(
        {"sha": sha, "size": len(data), "encoding": "base64", "content": "eA=="}
    ).encode()
    with pytest.raises(GitHubError):
        client.get_blob("fork/repo", sha, max_bytes=100)


class Objects:
    def __init__(self, *, changed=b"new\n", caller=b"call_changed()\n"):
        self.calls = []
        self.content = {
            "src/app.py": changed,
            "src/caller.py": caller,
            "tests/test_caller.py": b"assert caller()\n",
            "image.bin": b"\0binary",
            "runner": b"run()\n",
            "docs/secret.md": b"documentation\n",
            ".env": b"TOKEN=secret\n",
            "large.py": b"x" * 100,
        }

    def get_commit(self, repository, sha):
        self.calls.append(("commit", repository, sha))
        return "c" * 40

    def get_tree(self, repository, sha):
        self.calls.append(("tree", repository, sha))
        if sha == "c" * 40:
            names = {path.split("/")[0] for path in self.content}
            return tuple(
                {
                    "path": path,
                    "mode": "040000" if path in ("src", "tests", "docs") else "100644",
                    "type": "tree" if path in ("src", "tests", "docs") else "blob",
                    "sha": (
                        {"src": "e", "tests": "f", "docs": "d"}[path] * 40
                        if path in ("src", "tests", "docs")
                        else blob_id(self.content[path])
                    ),
                    "size": len(self.content.get(path, b"")),
                }
                for path in sorted(names)
            ) + ({"path": "link", "mode": "120000", "type": "blob", "sha": "d" * 40, "size": 9},)
        directory = {"e" * 40: "src", "f" * 40: "tests", "d" * 40: "docs"}[sha]
        return tuple(
            {
                "path": path.split("/")[1],
                "mode": "100644",
                "type": "blob",
                "sha": blob_id(data),
                "size": len(data),
            }
            for path, data in sorted(self.content.items())
            if path.startswith(directory + "/")
        )

    def get_blob(self, repository, sha, *, max_bytes):
        self.calls.append(("blob", repository, sha))
        return next(data for data in self.content.values() if blob_id(data) == sha)


def contextual(capture, *, caller=b"call_changed()\n", **limits):
    from scrutare.engine.repository_context import capture_repository_context

    config = parse_config(
        CONFIG + b"context: {enabled: true, related_paths: [src/caller.py, "
        b"tests/test_caller.py, .env, large.py, missing.py, link], "
        b'exclude: ["docs/**"]}\n'
    )
    config = replace(config, context=replace(config.context, **limits))
    metadata = read_json(capture / "metadata.json")
    metadata["head_sha"] = metadata["pull_request"]["head"]["sha"] = "a" * 40
    metadata["pull_request"]["head"]["repo"] = {"full_name": "fork/repo"}
    metadata["pull_request"]["base"]["sha"] = "b" * 40
    save_json(capture / "metadata.json", metadata)
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    client = Objects(caller=caller)
    capture_repository_context(client, capture, config)
    return config, client


def test_context_capture_cross_file_and_explicit_omissions(capture):
    config, client = contextual(capture, max_file_bytes=50)
    from scrutare.engine.review_inputs import prepare_review_inputs

    inputs = prepare_review_inputs(capture, config)
    manifest = read_json(inputs.root / "repository-context.json")
    entries = manifest["entries"]
    for side in ("base", "head"):
        paths = {entry["path"]: entry for entry in entries if entry["side"] == side}
        assert paths["src/caller.py"]["reason"] == "explicit_related"
        assert paths["tests/test_caller.py"]["status"] == "captured"
        assert paths["docs/secret.md"]["status"] == "excluded"
        assert paths[".env"]["status"] == "sensitive"
        assert paths["large.py"]["status"] == "oversized"
        assert paths["missing.py"]["status"] == "missing"
        assert paths["image.bin"]["status"] == "binary"
        assert paths["link"]["status"] == "symlink"
        assert (
            inputs.root / paths["src/caller.py"]["artifact"]
        ).read_bytes() == b"call_changed()\n"
        assert not paths["src/caller.py"]["eligible"]
    assert set(inputs.effective_files) == {"src/app.py", "image.bin", "runner"}
    assert any(call[:2] == ("commit", "fork/repo") for call in client.calls)
    assert all(call[2] not in ("main", "abc123", "base123") for call in client.calls)
    assert all(b"TOKEN=secret" not in path.read_bytes() for path in inputs.root.iterdir())


def test_context_limits_and_integrity(capture):
    config, _ = contextual(capture, max_files=2, max_total_bytes=10)
    from scrutare.engine.review_inputs import ReviewInputError, prepare_review_inputs

    inputs = prepare_review_inputs(capture, config)
    manifest = read_json(inputs.root / "repository-context.json")
    retained = [e for e in manifest["entries"] if e["status"] == "captured"]
    assert len(retained) <= 2
    assert sum(e["retained_bytes"] for e in retained) <= 10
    assert any(e["status"] == "total_limit" for e in manifest["entries"])
    entry = retained[0]
    (inputs.root / entry["artifact"]).write_bytes(b"changed evidence")
    with pytest.raises(ReviewInputError):
        prepare_review_inputs(capture, config)


def test_context_include_can_expose_changed_file_without_anchor_eligibility(capture):
    config, _ = contextual(capture)
    from scrutare.engine.review_inputs import prepare_review_inputs

    manifest = read_json(prepare_review_inputs(capture, config).root / "repository-context.json")
    assert all(not e["eligible"] for e in manifest["entries"] if e["path"] == "docs/secret.md")


def test_ingestion_captures_context_at_fork_revisions(tmp_path):
    from test_review_inputs import SOURCE

    from scrutare.engine.github import PullRequestRef
    from scrutare.engine.ingestion import ingest_pr

    objects = Objects()
    metadata = {
        "number": 12,
        "state": "open",
        "merged": False,
        "changed_files": 1,
        "head": {"sha": "a" * 40, "repo": {"full_name": "fork/repo"}},
        "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
    }
    objects.get_pr = lambda ref: metadata
    objects.get_diff = lambda ref: SOURCE.decode()
    objects.get_files = lambda ref: [{"filename": "src/app.py", "status": "modified"}]
    objects.get_reviews = objects.get_comments = objects.get_review_comments = lambda ref: []
    raw = CONFIG + b"context: {enabled: true, related_paths: [src/caller.py]}\n"
    config = parse_config(raw)
    run = ingest_pr(
        objects,
        PullRequestRef("owner", "repo", 12),
        tmp_path,
        config_bytes=raw,
        config_data=config.to_dict(),
        review_config=config,
    )
    assert (run / "review-inputs/repository-context.json").is_file()
    assert ("commit", "fork/repo", "a" * 40) in objects.calls


def test_all_persona_descriptors_allow_context_but_preserve_anchor_selection(capture):
    from scrutare.engine.debate_inputs import DebateInput
    from scrutare.engine.persona_inputs import PersonaReviewInput
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.personas import resolve_personas

    config, _ = contextual(capture)
    inputs = prepare_review_inputs(capture, config)
    persona = resolve_personas(("senior-dev",))[0]
    for descriptor in (
        PersonaReviewInput(persona, inputs),
        DebateInput(persona, inputs, (), (), False, ()),
    ):
        assert "repository-context.json" in descriptor.prompt
        assert "selected diff hunks" in descriptor.prompt
        assert descriptor.nare_input_args()[1:4] == ("--tools", "read", "--root")
