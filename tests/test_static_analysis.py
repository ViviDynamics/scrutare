"""Captured tool diagnostics are bounded evidence, never automatic findings."""

import json
from hashlib import sha256

import pytest
from test_repository_context import contextual
from test_review_inputs import capture as capture
from test_review_inputs import read_json, save_json

from scrutare.config import ConfigError, parse_config


def enabled(capture):
    config, _ = contextual(capture)
    config = parse_config(json.dumps(config.to_dict() | {"analysis": {"enabled": True}}))
    for name in ("config.yaml", "config.json"):
        save_json(capture / name, config.to_dict())
    return config


def document(**changes):
    return (
        dict(
            schema_version=1,
            revision="a" * 40,
            tool={"name": "ruff", "version": "0.16.10"},
            status="complete",
            duration_seconds=0.25,
            source_root="/captured/repo",
            diagnostics=[
                dict(
                    code="F821",
                    message="Undefined name `call_changed`",
                    filename="/captured/repo/src/caller.py",
                    location={"row": 1, "column": 1},
                    end_location={"row": 1, "column": 13},
                )
            ],
        )
        | changes
    )


def produce(capture, tmp_path, value):
    from scrutare.engine.static_analysis import capture_static_analysis

    config = enabled(capture)
    source = tmp_path / "trusted-ci.json"
    source.write_text(json.dumps(value))
    capture_static_analysis(capture, config, source)
    return config, source


def test_analysis_is_explicit_and_requires_context():
    baseline = parse_config("models: {default: {model: test}}")
    assert "analysis" not in baseline.to_dict()
    with pytest.raises(ConfigError):
        parse_config("models: {default: {model: test}}\nanalysis: {enabled: true}")
    with pytest.raises(ConfigError):
        parse_config("models: {default: {model: test}}\nanalysis: {enabled: 1}")


def test_captured_diagnostic_is_bound_to_head_bytes(capture, tmp_path):
    from scrutare.engine.review_inputs import prepare_review_inputs

    config, source = produce(capture, tmp_path, document())
    inputs = prepare_review_inputs(capture, config)
    result = read_json(inputs.root / "static-analysis.json")
    assert result["status"] == "complete"
    assert result["tool_runtime_seconds"] == 0.25
    assert result["source_sha256"] == sha256(source.read_bytes()).hexdigest()
    diagnostic = result["diagnostics"][0]
    assert diagnostic["path"] == "src/caller.py"
    assert diagnostic["revision"] == "a" * 40
    assert diagnostic["sha256"] == sha256(b"call_changed()\n").hexdigest()
    assert diagnostic["rule"] == "F821"
    assert not (inputs.root / "source.json").exists()
    assert (capture / "static-analysis/source.json").read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "change",
    [
        {"revision": "b" * 40},
        {"tool": {"name": "ruff", "version": "other"}},
        {"schema_version": True},
        {"status": "success"},
        {"duration_seconds": -1},
        {"duration_seconds": float("nan")},
        {"command": "run arbitrary PR code"},
    ],
)
def test_invalid_capture_is_refused(capture, tmp_path, change):
    with pytest.raises(ValueError):
        produce(capture, tmp_path, document(**change))


@pytest.mark.parametrize("path", ["/outside/secret.py", "../secret.py", "src\\caller.py"])
def test_diagnostic_paths_cannot_escape(capture, tmp_path, path):
    data = document()
    data["diagnostics"][0]["filename"] = path
    with pytest.raises(ValueError):
        produce(capture, tmp_path, data)


@pytest.mark.parametrize("status", ["failed", "truncated", "unavailable"])
def test_noncomplete_results_are_retained_without_proving_correctness(capture, tmp_path, status):
    from scrutare.engine.review_inputs import prepare_review_inputs

    config, _ = produce(capture, tmp_path, document(status=status))
    result = read_json(prepare_review_inputs(capture, config).root / "static-analysis.json")
    assert result["status"] == status
    assert result["claim_truth"] == "not_assessed"


def test_omitted_context_diagnostics_are_not_exposed(capture, tmp_path):
    from scrutare.engine.review_inputs import prepare_review_inputs

    data = document()
    data["diagnostics"][0]["filename"] = "/captured/repo/.env"
    data["diagnostics"][0]["message"] = "PRIVATE_RAW_SENTINEL"
    config, _ = produce(capture, tmp_path, data)
    inputs = prepare_review_inputs(capture, config)
    result = read_json(inputs.root / "static-analysis.json")
    assert not result["diagnostics"]
    assert result["omitted"]["not_captured"] == 1
    assert all(b"PRIVATE_RAW_SENTINEL" not in p.read_bytes() for p in inputs.root.iterdir())


def test_enabled_without_capture_is_explicitly_unavailable(capture):
    from scrutare.engine.review_inputs import prepare_review_inputs

    config = enabled(capture)
    result = read_json(prepare_review_inputs(capture, config).root / "static-analysis.json")
    assert result["status"] == "unavailable"
    assert result["source_sha256"] is None
    assert result["tool_runtime_seconds"] is None


def test_source_tamper_invalidates_prepared_inputs(capture, tmp_path):
    from scrutare.engine.review_inputs import ReviewInputError, prepare_review_inputs

    config, _ = produce(capture, tmp_path, document())
    prepare_review_inputs(capture, config)
    (capture / "static-analysis/source.json").write_text(json.dumps(document(status="failed")))
    with pytest.raises(ReviewInputError):
        prepare_review_inputs(capture, config)


def test_projection_preserves_tools_and_invalidates_carried_evidence(capture, tmp_path):
    from scrutare.engine.iterative import _project
    from scrutare.engine.review_inputs import prepare_review_inputs
    from scrutare.engine.static_analysis import analysis_dependencies

    config, _ = produce(capture, tmp_path, document())
    inputs = prepare_review_inputs(capture, config)
    from scrutare.findings import parse_diff_sections

    patch = next(
        s.data
        for s in parse_diff_sections((capture / "diff.patch").read_bytes())
        if s.file == "src/app.py"
    )
    child = _project(capture, config, {"src/app.py": patch})
    copied = prepare_review_inputs(child, config)
    assert read_json(copied.root / "static-analysis.json") == read_json(
        inputs.root / "static-analysis.json"
    )
    dependencies = analysis_dependencies(copied.root)
    assert dependencies[0]["side"] == "head"
    assert (
        dependencies[0]["sha256"]
        == sha256((copied.root / "static-analysis.json").read_bytes()).hexdigest()
    )


def test_ingestion_accepts_explicit_capture_before_preparing(capture, tmp_path):
    from unittest.mock import Mock

    from test_repository_context import Objects
    from test_review_inputs import CONFIG

    from scrutare.engine.github import PullRequestRef
    from scrutare.engine.ingestion import ingest_pr

    raw = (
        CONFIG
        + b"context: {enabled: true, related_paths: [src/caller.py]}\nanalysis: {enabled: true}\n"
    )
    config = parse_config(raw)
    pr = read_json(capture / "metadata.json")["pull_request"]
    pr["changed_files"] = 1
    pr["head"] = {"sha": "a" * 40, "repo": {"full_name": "fork/repo"}}
    pr["base"]["sha"] = "b" * 40
    client = Mock()
    objects = Objects()
    for name in ("get_commit", "get_tree", "get_blob"):
        setattr(client, name, getattr(objects, name))
    client.get_pr.return_value = pr
    client.get_diff.return_value = (
        "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    client.get_files.return_value = [{"filename": "src/app.py", "status": "modified"}]
    client.get_reviews.return_value = []
    client.get_comments.return_value = []
    client.get_review_comments.return_value = []
    source = tmp_path / "ci.json"
    source.write_text(json.dumps(document()))
    run = ingest_pr(
        client,
        PullRequestRef("owner", "repo", 12),
        tmp_path / "runs",
        config_bytes=raw,
        config_data=config.to_dict(),
        review_config=config,
        analysis_capture=source,
    )
    assert read_json(run / "review-inputs/static-analysis.json")["diagnostics"]


def test_cli_explicit_capture_option_is_forwarded(tmp_path, monkeypatch):
    from scrutare.interfaces import cli

    config = tmp_path / "settings.yaml"
    config.write_text(
        "models: {default: {model: test}}\ncontext: {enabled: true}\nanalysis: {enabled: true}\n"
    )
    source = tmp_path / "ci.json"
    observed = {}
    monkeypatch.setattr(cli, "preflight_review", lambda *args: None)
    monkeypatch.setattr(cli, "resolve_pr", lambda pr: object())

    async def review(*args, **kwargs):
        observed.update(kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "review_pr", review)
    assert (
        cli.main(
            ["review", "--pr", "12", "--config", str(config), "--analysis-capture", str(source)]
        )
        == 130
    )
    assert observed["analysis_capture"] == source


def test_corpus_analysis_is_declared_and_frozen_before_runtime(tmp_path, monkeypatch):
    import asyncio
    from pathlib import Path

    from test_evaluation_context import complete, context_config, declared

    from scrutare.engine.session_models import NareRuntime
    from scrutare.evaluation.corpus import load_corpus
    from scrutare.evaluation.runner import run_experiment

    root = tmp_path / "corpus"
    declared(root)
    source = root / "captured-ci.json"
    value = document(source_root="/captured")
    value["diagnostics"][0]["filename"] = "/captured/caller.py"
    source.write_text(json.dumps(value))
    manifest = read_json(root / "manifest.json")
    manifest["cases"][0]["analysis_capture"] = "captured-ci.json"
    save_json(root / "manifest.json", manifest)
    cases = load_corpus(root)
    assert cases[0].analysis_capture == source
    config = parse_config(json.dumps(context_config().to_dict() | {"analysis": {"enabled": True}}))
    original = source.read_bytes()

    async def inspect(runtime):
        from types import SimpleNamespace

        source.write_text(json.dumps(document(status="failed")))
        return SimpleNamespace(version="2026.10.4", contract=1)

    async def execute(run_dir, conf, *, runtime):
        assert (run_dir / "static-analysis/source.json").read_bytes() == original
        result = read_json(run_dir / "review-inputs/static-analysis.json")
        assert result["status"] == "complete" and result["diagnostics"]
        return complete()

    monkeypatch.setattr("scrutare.evaluation.runner.inspect_nare_runtime", inspect)
    monkeypatch.setattr("scrutare.evaluation.runner.run_review", execute)
    result = asyncio.run(
        run_experiment(
            cases,
            config,
            tmp_path / "out",
            runtime=NareRuntime(Path("/unused")),
            evidence_kind="offline",
        )
    )
    assert all(run["status"] == "complete" for run in result["runs"])


@pytest.mark.parametrize("declaration", ["../outside", "/absolute", "labels/a.json"])
def test_corpus_analysis_cannot_escape_or_read_labels(tmp_path, declaration):
    from test_evaluation_context import declared

    from scrutare.evaluation.corpus import load_corpus

    declared(tmp_path)
    manifest = read_json(tmp_path / "manifest.json")
    manifest["cases"][0]["analysis_capture"] = declaration
    save_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError):
        load_corpus(tmp_path)


def test_normalized_evidence_has_a_separate_byte_bound(capture, tmp_path, monkeypatch):
    from scrutare.engine import static_analysis
    from scrutare.engine.review_inputs import prepare_review_inputs

    monkeypatch.setattr(static_analysis, "MAX_PREPARED_BYTES", 700, raising=False)
    value = document()
    value["diagnostics"] *= 5
    config, _ = produce(capture, tmp_path, value)
    data = (prepare_review_inputs(capture, config).root / "static-analysis.json").read_bytes()
    assert len(data) <= 700
    result = json.loads(data)
    assert result["status"] == "truncated"
    assert result["omitted"]["byte_limit"] > 0


@pytest.mark.parametrize("problem", ["duplicate", "oversized", "count", "range", "fix"])
def test_capture_adapter_rejects_ambiguous_or_unbounded_inputs(capture, tmp_path, problem):
    from scrutare.engine.static_analysis import capture_static_analysis

    config = enabled(capture)
    value = document()
    if problem == "count":
        value["diagnostics"] *= 1001
    elif problem == "range":
        value["diagnostics"][0]["end_location"]["row"] = 900
    elif problem == "fix":
        value["diagnostics"][0]["fix"] = {"command": "untrusted executable"}
    raw = json.dumps(value)
    if problem == "duplicate":
        raw = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    elif problem == "oversized":
        raw += " " * 1048576
    source = tmp_path / "ci.json"
    source.write_text(raw)
    with pytest.raises(ValueError):
        capture_static_analysis(capture, config, source)
    assert not (capture / "static-analysis").exists()


def test_known_ruff_optional_fields_remain_private(capture, tmp_path):
    from scrutare.engine.review_inputs import prepare_review_inputs

    value = document()
    value["diagnostics"][0].update(
        url="https://docs.astral.sh/ruff/rules/undefined-name/",
        cell=None,
        noqa_row=1,
        fix={
            "applicability": "unsafe",
            "message": "Possible correction",
            "edits": [
                {
                    "content": "PRIVATE_FIX_SENTINEL",
                    "location": {"row": 1, "column": 1},
                    "end_location": {"row": 1, "column": 13},
                }
            ],
        },
    )
    config, _ = produce(capture, tmp_path, value)
    inputs = prepare_review_inputs(capture, config)
    assert b"PRIVATE_FIX_SENTINEL" not in (inputs.root / "static-analysis.json").read_bytes()
