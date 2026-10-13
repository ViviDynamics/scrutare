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


@pytest.mark.parametrize(
    "identifier,filename,contract",
    [
        ("sealed-h02", "src/app.py", None),
        ("sealed-h06", "src/app.py", "built-in integer coordinates"),
        ("sealed-h22", "src/app.py", "built-in list fixture"),
        ("sealed-h31", "src/concurrency/worker.py", "built-in list queue"),
        ("sealed-h32", "src/app.py", "finite numeric monotonic times"),
    ],
)
def test_production_context_projection_excludes_holdout_labels_and_controls(
    tmp_path, monkeypatch, identifier, filename, contract
):
    import asyncio

    from scrutare.config import parse_config
    from scrutare.engine.session_models import NareRuntime, TokenUsage
    from scrutare.evaluation.runner import run_experiment

    cases = tuple(c for c in load_corpus(ROOT, "holdout") if c.id == identifier)
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
        } == ({filename, "src/contracts.py"} if identifier == "sealed-h02" else {filename})
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
        if contract:
            visible = [
                entry
                for entry in manifest["entries"]
                if entry["path"] == filename and entry["status"] == "captured"
            ]
            assert {entry["side"] for entry in visible} == {"base", "head"}
            assert all(contract in (prepared / entry["artifact"]).read_text() for entry in visible)
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


@pytest.mark.parametrize(
    "identifier,filename,function,args,error",
    [
        ("sealed-h06", "src/app.py", "partition", (1.0, 10), ValueError),
        ("sealed-h22", "src/app.py", "check_empty", ((),), TypeError),
        ("sealed-h31", "src/concurrency/worker.py", "drain", ({1: 2},), TypeError),
        ("sealed-h32", "src/app.py", "expired", (float("nan"), 10), ValueError),
        ("sealed-h32", "src/app.py", "expired", (10, float("inf")), ValueError),
    ],
)
def test_public_clean_contract_rejects_out_of_scope_inputs_on_both_revisions(
    identifier, filename, function, args, error
):
    for side in ("base", "head"):
        source = ROOT / "sources" / identifier / side / filename
        namespace = {}
        # Only our checked-in original trusted fixtures execute here, never an external corpus.
        exec(compile(source.read_bytes(), str(source), "exec"), namespace)
        with pytest.raises(error):
            namespace[function](*args)
    # Diff-only reviewers see the same restrictions; no need to expose private gold labels.
    patch = (ROOT / "captures" / identifier / "diff.patch").read_text()
    assert "raise " + error.__name__ in patch
