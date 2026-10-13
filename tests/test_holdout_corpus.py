"""Sealed original holdout: trusted mechanical controls never enter model captures."""

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrutare.evaluation.corpus import load_corpus

ROOT = Path(__file__).parents[1] / "evaluations" / "holdout-v1"


def test_holdout_is_disjoint_complete_and_balanced():
    cases = load_corpus(ROOT, "holdout")
    original = load_corpus(ROOT.parent / "pilot", "all")
    assert len(cases) == 40
    assert all(re.fullmatch(r"sealed-h[0-9]{2}", case.id) for case in cases)
    assert not {c.id for c in cases} & {c.id for c in original}
    assert load_corpus(ROOT, "development") == ()
    assert Counter(c.domain for c in cases) == dict.fromkeys(
        ("correctness", "security", "tests", "operations", "consumer"), 8
    )
    counts = Counter()
    crossfile = 0
    for case in cases:
        gold = json.loads(case.labels.read_text())
        counts[case.domain, bool(gold["defects"])] += 1
        assert len(case.context_sources) == 2
        assert gold["counterexamples"]
        assert len(gold["defects"]) <= 1
        assert {p.name for p in case.capture.iterdir()} == {
            "diff.patch",
            "files.json",
            "metadata.json",
        }
        metadata = json.loads((case.capture / "metadata.json").read_text())
        for side in case.context_sources:
            assert metadata["pull_request"][side.side]["repo"]["full_name"] == side.repository
        crossfile += bool(gold["unchanged_supporting_paths"])
    assert all(
        counts[domain, True] == 5 and counts[domain, False] == 3
        for domain in {c.domain for c in cases}
    )
    assert crossfile >= 8


def test_trusted_gold_controls_and_frozen_inventory():
    spec = importlib.util.spec_from_file_location("holdout_verify", ROOT / "verify.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.verify(ROOT)
    assert report["cases"] == 40
    assert report["patches_verified"] == 40
    assert report["defects_verified"] == 25
    assert report["clean_controls_verified"] == 15
    assert report["failures"] == []
    assert report == json.loads((ROOT / "mechanical-report.json").read_text())


def test_verifier_refuses_an_untrusted_corpus_path(tmp_path):
    spec = importlib.util.spec_from_file_location("holdout_verify", ROOT / "verify.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="Only checked-in original"):
        module.verify(tmp_path)


def test_production_context_projection_excludes_holdout_labels_and_controls(tmp_path, monkeypatch):
    import asyncio

    from scrutare.config import parse_config
    from scrutare.engine.session_models import NareRuntime, TokenUsage
    from scrutare.evaluation.runner import run_experiment

    cases = tuple(c for c in load_corpus(ROOT, "holdout") if c.id == "sealed-h02")
    config = parse_config(
        "models: {default: {model: offline-proof}}\n"
        "context: {enabled: true, related_paths: [src/contracts.py]}\n"
    )

    async def inspect(runtime):
        return SimpleNamespace(version="offline-no-model-invocation", contract=1)

    async def projected(run, config, *, runtime):
        prepared = run / "review-inputs"
        manifest = json.loads((prepared / "repository-context.json").read_text())
        assert manifest["source"]["kind"] == "corpus_snapshot"
        assert {
            entry["path"] for entry in manifest["entries"] if entry["status"] == "captured"
        } == {"src/app.py", "src/contracts.py"}
        assert all(path.is_file() for path in prepared.iterdir())
        names = {path.name for path in prepared.iterdir()}
        assert not names & {
            "catalog.py",
            "verify.py",
            "build.py",
            "manifest.json",
            "mechanical-report.json",
            "freeze-sha256.json",
            "labels",
            "controls",
        }
        for entry in manifest["entries"]:
            if entry["path"] == "src/contracts.py" and entry["status"] == "captured":
                assert (prepared / entry["artifact"]).read_text() == "SIZE = 10\n"
        return SimpleNamespace(
            status="complete",
            accounting_complete=True,
            usage=TokenUsage(),
            verdict=SimpleNamespace(findings=()),
        )

    monkeypatch.setattr("scrutare.evaluation.runner.inspect_nare_runtime", inspect)
    monkeypatch.setattr("scrutare.evaluation.runner.run_review", projected)
    result = asyncio.run(
        run_experiment(
            cases,
            config,
            tmp_path / "projection",
            runtime=NareRuntime(Path("/unused")),
            evidence_kind="offline",
        )
    )
    assert len(result["runs"]) == 9
    assert all(run["status"] == "complete" for run in result["runs"])
