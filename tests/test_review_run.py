"""Full service composition with real capture, panel, verdict and posting code."""

import asyncio
import importlib
import importlib.util
import json
import sys
from dataclasses import FrozenInstanceError, asdict
from hashlib import sha256
from pathlib import Path

import pytest
from test_panel import finding, install
from test_post_review import REF, SHA, CaptureClient, FakePoster, metadata

from scrutare import __version__
from scrutare.config import parse_config
from scrutare.engine.session_models import NareRuntime
from scrutare.poster import PostingRejected, PostingUncertain
from scrutare.replay import replay_run

RAW = b"# retain me\r\nmodels: {default: {model: default-model}}\r\npersonas: [security]\r\n"


def service():
    assert importlib.util.find_spec("scrutare.engine.review"), "full review service is missing"
    return importlib.import_module("scrutare.engine.review")


def invoke(tmp_path, *, raw=RAW, client=None, runtime=None):
    return asyncio.run(service().review_pr(
        REF, parse_config(raw), config_bytes=raw, runs_root=tmp_path,
        runtime=runtime or NareRuntime(Path(sys.executable)),
        capture_client=CaptureClient(), review_client=client or FakePoster(),
    ))


@pytest.mark.parametrize("category,mode,status,want,event", [
    (None, "review", "complete", "approve", "APPROVE"),
    ("security", "review", "complete", "changes_requested", "REQUEST_CHANGES"),
    ("security", "comment", "complete", "changes_requested", "COMMENT"),
    (None, "review", "partial", "approve", "APPROVE"),
    ("security", "review", "partial", "changes_requested", "REQUEST_CHANGES"),
])
def test_full_delivery_persists_exact_result_manifest_and_replays(
        tmp_path, monkeypatch, category, mode, status, want, event):
    service()
    install(monkeypatch, {"security": () if category is None else (
        finding(file="example.py", category=category),)}, initial_status=status)
    raw = RAW + f"github: {{post_mode: {mode}}}\n".encode()
    poster = FakePoster()
    result = invoke(tmp_path, raw=raw, client=poster)
    data = result.to_dict()
    assert data == {
        "schema_version": 1, "scrutare_version": __version__, "status": "posted",
        "run_dir": str(result.run_dir), "head_sha": SHA, "verdict": want,
        "rule": "no_blocking_findings" if category is None else "any_blocking_finding",
        "panel_status": status, "usage": {"input": 10, "output": 0, "cache_read": 0,
                                          "cache_write": 0, "total": 10},
        "accounting_complete": True, "review": asdict(poster.receipt),
    }
    assert poster.creates[0].event == event
    run = result.run_dir
    assert (run / "result.json").read_bytes() == result.to_bytes()
    assert (run / "config.yaml").read_bytes() == raw
    manifest = json.loads((run / "artifacts.json").read_bytes())
    entries = {entry["path"]: entry for entry in manifest["artifacts"]}
    assert "result.json" in entries and "posting.json" in entries
    for name, entry in entries.items():
        content = (run / name).read_bytes()
        assert entry["sha256"] == sha256(content).hexdigest()
        assert entry["size_bytes"] == len(content)
    assert replay_run(run).exit_code == 0
    data["review"]["body"] = "mutated"
    assert result.to_dict()["review"]["body"] == poster.receipt.body
    with pytest.raises(FrozenInstanceError):
        result.run_dir = tmp_path


@pytest.mark.parametrize("status,available,confident", [
    ("failed", True, True), ("partial", False, True), ("complete", False, True),
    ("complete", True, False),
])
def test_no_valid_initial_output_never_posts_and_keeps_failure_evidence(
        tmp_path, monkeypatch, status, available, confident):
    module = service()
    install(monkeypatch, {}, initial_status=status, initial_available=available,
            confident=confident)
    poster = FakePoster()
    with pytest.raises(module.ReviewRunError) as error:
        invoke(tmp_path, client=poster)
    run = error.value.run_dir
    assert run.is_dir() and not error.value.confirmed_posting
    assert poster.creates == []
    assert not (run / "verdict.json").exists()
    assert not (run / "result.json").exists()
    assert (run / "artifacts.json").is_file()


@pytest.mark.parametrize("failure", ["rejected", "uncertain", "closed", "merged"])
def test_delivery_failure_keeps_safe_error_path_and_manifest(tmp_path, monkeypatch, failure):
    module = service()
    install(monkeypatch, {})
    poster = FakePoster()
    if failure == "rejected":
        poster.outcomes.append(PostingRejected("Review rejected.", status=422))
    elif failure == "uncertain":
        def uncertain(*args):
            raise PostingUncertain("secret raw transport content")
        poster.create_review = uncertain
    else:
        poster.current = metadata(state="closed" if failure == "closed" else "open",
                                  merged=failure == "merged")
    with pytest.raises(module.ReviewRunError) as error:
        invoke(tmp_path, client=poster)
    run = error.value.run_dir
    assert run.is_dir() and not error.value.confirmed_posting
    assert "secret" not in str(error.value)
    assert not (run / "result.json").exists()
    assert (run / "artifacts.json").is_file()


@pytest.mark.parametrize("destination", ["result", "manifest"])
def test_persistence_failure_after_confirmed_post_is_honest(tmp_path, monkeypatch, destination):
    module = service()
    install(monkeypatch, {})
    def broken(*args, **kwargs):
        raise OSError("secret disk detail")
    monkeypatch.setattr(module, "write_owned_bytes" if destination == "result"
                        else "write_artifact_manifest", broken)
    poster = FakePoster()
    with pytest.raises(module.ReviewRunError) as error:
        invoke(tmp_path, client=poster)
    assert len(poster.creates) == 1
    assert error.value.confirmed_posting and error.value.run_dir.is_dir()
    assert "confirmed" in str(error.value).lower() and "secret" not in str(error.value)
    assert (error.value.run_dir / "result.json").exists() == (destination == "manifest")
    assert (error.value.run_dir / "artifacts.json").exists() == (destination == "result")


def test_secondary_manifest_failure_preserves_primary_failure(tmp_path, monkeypatch):
    module = service()
    install(monkeypatch, {}, initial_available=False)
    def broken(*args):
        raise OSError("secret filesystem detail")
    monkeypatch.setattr(module, "write_artifact_manifest", broken)
    with pytest.raises(module.ReviewRunError, match="verdict") as error:
        invoke(tmp_path)
    assert error.value.run_dir.is_dir() and not error.value.confirmed_posting
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("strategy", ["iterative", "debate"])
def test_unsupported_strategy_precedes_capture(tmp_path, strategy):
    module = service()
    with pytest.raises(module.ReviewRunError, match="not yet implemented") as error:
        invoke(tmp_path, raw=RAW + f"strategy: {strategy}\n".encode())
    assert error.value.run_dir is None
    assert list(tmp_path.iterdir()) == []


def test_missing_executable_precedes_capture(tmp_path):
    module = service()
    with pytest.raises(module.ReviewRunError, match="executable") as error:
        invoke(tmp_path, runtime=NareRuntime(tmp_path / "absent-nare"))
    assert error.value.run_dir is None and list(tmp_path.iterdir()) == []


def test_engine_retains_authority_to_reject_present_incompatible_runtime(tmp_path, monkeypatch):
    module = service()
    from scrutare.engine import fanout
    from scrutare.engine.nare_session import SessionRuntimeError
    async def incompatible(runtime):
        raise SessionRuntimeError("contract: incompatible runtime")
    monkeypatch.setattr(fanout, "inspect_nare_runtime", incompatible)
    poster = FakePoster()
    with pytest.raises(module.ReviewRunError, match="contract") as error:
        invoke(tmp_path, client=poster)
    assert error.value.run_dir.is_dir() and poster.creates == []
    assert (error.value.run_dir / "artifacts.json").is_file()


def test_interruption_snapshots_only_after_engine_cleanup(tmp_path, monkeypatch):
    module = service()
    from scrutare.engine import fanout
    install(monkeypatch, {})
    async def cancelled(descriptor, rail, lease, *, ledger, artifact_directory, **kwargs):
        try:
            raise asyncio.CancelledError
        finally:
            await asyncio.sleep(0)
            (artifact_directory / "cleanup.json").write_text("{}")
    monkeypatch.setattr(fanout, "run_persona_session", cancelled)
    with pytest.raises(module.ReviewInterruptedError) as error:
        invoke(tmp_path)
    manifest = json.loads((error.value.run_dir / "artifacts.json").read_bytes())
    assert any(entry["path"].endswith("cleanup.json") for entry in manifest["artifacts"])
    assert not (error.value.run_dir / "result.json").exists()


@pytest.mark.parametrize("targets", [[], ["alice"]])
@pytest.mark.parametrize("failure", [None, "request", "confirmation"])
def test_actual_exhaustion_routes_both_delivery_stages_and_preserves_confirmation(
        tmp_path, monkeypatch, targets, failure):
    module = service()
    from scrutare.engine.panel import PanelResult
    from scrutare.engine.session_models import FanOutResult, TokenUsage
    from scrutare.findings import Exhaustion, derive_verdict
    from scrutare.poster import ReviewerRequestReceipt, posting
    # Use YAML for the appended mapping, retaining the raw snapshot throughout capture.
    raw = RAW + f"github: {{human_reviewers: {json.dumps(targets)}}}\n".encode()
    config = parse_config(raw)
    verdict = derive_verdict([], config.verdict, exhaustion=Exhaustion("panel", 3, 3))
    async def exhausted(run, config, *, runtime):
        initial = FanOutResult((), False, False, TokenUsage(), False, 0)
        return PanelResult("complete", verdict, initial, (), None, TokenUsage(), True, run)
    monkeypatch.setattr(module, "run_review", exhausted)
    class Poster(FakePoster):
        def request_reviewers(self, ref, reviewers):
            assert self.receipt.state == "COMMENTED"
            if failure == "request":
                raise PostingRejected("Reviewer request rejected.", status=422)
            return ReviewerRequestReceipt(reviewers, "post_response")
    if failure == "confirmation":
        original = posting.atomic_write
        def fail_receipt(path, data):
            if path.name == "posting.json" and json.loads(data)["status"] == "posted":
                raise OSError("SECRET persistence error")
            original(path, data)
        monkeypatch.setattr(posting, "atomic_write", fail_receipt)
    poster = Poster()
    if failure == "confirmation" or failure == "request" and targets:
        with pytest.raises(module.ReviewRunError) as error:
            invoke(tmp_path, raw=raw, client=poster)
        assert error.value.confirmed_posting
        assert "confirmed" in str(error.value) and "SECRET" not in str(error.value)
        run = error.value.run_dir
        assert not (run / "result.json").exists()
    else:
        result = invoke(tmp_path, raw=raw, client=poster)
        data = result.to_dict()
        assert data["verdict"] == "escalated"
        assert data["rule"] == "rounds_exhausted_without_convergence"
        assert data["reviewer_request"] == {
            "reviewers": targets, "provenance": "post_response" if targets else "no_targets"}
        run = result.run_dir
        assert (run / "result.json").read_bytes() == result.to_bytes()
    assert len(poster.creates) == 1 and poster.creates[0].event == "COMMENT"
    assert (run / "verdict.json").read_bytes() == verdict.to_bytes()
    assert (run / "artifacts.json").is_file()


@pytest.mark.parametrize("bad_verdict", [None, {"verdict": "approve"}])
def test_only_a_real_engine_verdict_can_reach_posting(tmp_path, monkeypatch, bad_verdict):
    module = service()
    from scrutare.engine.panel import PanelResult
    from scrutare.engine.session_models import FanOutResult, TokenUsage
    async def malformed(run, config, *, runtime):
        initial = FanOutResult((), False, False, TokenUsage(), False, 0)
        return PanelResult("complete", bad_verdict, initial, (), None, TokenUsage(), True, run)
    monkeypatch.setattr(module, "run_review", malformed)
    poster = FakePoster()
    with pytest.raises(module.ReviewRunError, match="verdict") as error:
        invoke(tmp_path, client=poster)
    assert poster.creates == [] and not (error.value.run_dir / "posting.json").exists()


def test_result_destination_collision_preserves_existing_bytes(tmp_path, monkeypatch):
    module = service()
    install(monkeypatch, {})
    class Poster(FakePoster):
        def create_review(self, ref, payload):
            run = next(tmp_path.iterdir())
            (run / "result.json").write_bytes(b"prior evidence")
            return super().create_review(ref, payload)
    with pytest.raises(module.ReviewRunError) as error:
        invoke(tmp_path, client=Poster())
    assert error.value.confirmed_posting
    assert (error.value.run_dir / "result.json").read_bytes() == b"prior evidence"


def test_service_passes_same_normalized_config_to_capture_and_engine(tmp_path, monkeypatch):
    module = service()
    install(monkeypatch, {})
    config = parse_config(RAW)
    ingest = module.ingest_pr
    engine = module.run_review
    def captured(*args, **kwargs):
        assert kwargs["review_config"] is config
        assert kwargs["config_bytes"] is RAW
        return ingest(*args, **kwargs)
    async def reviewed(run, settings, **kwargs):
        assert settings is config
        return await engine(run, settings, **kwargs)
    monkeypatch.setattr(module, "ingest_pr", captured)
    monkeypatch.setattr(module, "run_review", reviewed)
    result = asyncio.run(module.review_pr(
        REF, config, config_bytes=RAW, runs_root=tmp_path,
        runtime=NareRuntime(Path(sys.executable)), capture_client=CaptureClient(),
        review_client=FakePoster()))
    assert result.verdict == "approve"


@pytest.mark.parametrize("phase", ["posting", "result", "manifest"])
def test_service_wraps_native_sigint_with_retained_context(tmp_path, monkeypatch, phase):
    import signal

    module = service()
    install(monkeypatch, {})
    receipt_returned = phase != "posting"
    if phase == "posting":
        class InterruptedPoster(FakePoster):
            def create_review(self, ref, payload):
                nonlocal receipt_returned
                receipt = super().create_review(ref, payload)
                signal.raise_signal(signal.SIGINT)
                receipt_returned = True
                return receipt
        poster = InterruptedPoster()
    else:
        poster = FakePoster()
        name = "write_owned_bytes" if phase == "result" else "write_artifact_manifest"
        original = getattr(module, name)
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            signal.raise_signal(signal.SIGINT)
        monkeypatch.setattr(module, name, interrupted)
    # Catch bare KeyboardInterrupt too so a regression cannot stop the test runner.
    try:
        invoke(tmp_path, client=poster)
    except (module.ReviewInterruptedError, KeyboardInterrupt) as error:
        assert isinstance(error, module.ReviewInterruptedError)
        assert error.confirmed_posting == receipt_returned
        assert error.run_dir.is_dir()
        assert (error.run_dir / "artifacts.json").is_file()
    else:
        pytest.fail("Native interruption was reported as success")


def test_relative_capture_directory_is_normalized_before_owned_persistence(tmp_path, monkeypatch):
    module = service()
    install(monkeypatch, {})
    monkeypatch.chdir(tmp_path)
    capture = module.ingest_pr
    def relative_capture(*args, **kwargs):
        run = capture(*args, **kwargs)
        return run.relative_to(tmp_path) if run.is_absolute() else run
    monkeypatch.setattr(module, "ingest_pr", relative_capture)
    result = invoke(Path("runs"))
    assert result.run_dir.is_absolute()
    assert (result.run_dir / "result.json").read_bytes() == result.to_bytes()
    assert (result.run_dir / "artifacts.json").is_file()
