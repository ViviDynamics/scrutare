"""Durable orchestration for one complete captured-head GitHub review."""

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from time import sleep, time
from typing import Any
from uuid import uuid4

from scrutare.config import ConfigError, ReviewConfig, parse_config
from scrutare.engine.github import GitHubError, PullRequestRef, assert_pr_open, resolve_pr
from scrutare.findings import Verdict, VerdictArtifactError, write_verdict
from scrutare.poster.client import PostedReview, ReviewClient, _receipt
from scrutare.poster.errors import (
    PostingError,
    PostingRateLimited,
    PostingRejected,
    PostingUncertain,
)
from scrutare.poster.journal import atomic_write, canonical, read_json, run_lock
from scrutare.poster.payload import ReviewPayload, build_review_payload

_UNCERTAIN = "Review delivery is uncertain; reconcile the saved run before posting again."


def _now() -> float:
    return time()


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _capture(run_dir: Path, verdict: Verdict) -> tuple[PullRequestRef, str, ReviewConfig, bytes]:
    try:
        metadata = read_json(run_dir / "metadata.json")
        if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int:
            raise ValueError
        if metadata["schema_version"] != 1 or type(metadata["pr_number"]) is not int:
            raise ValueError
        repository = metadata["repository"]
        if not isinstance(repository, str) or not repository.strip():
            raise ValueError
        ref = resolve_pr(str(metadata["pr_number"]), repository=repository)
        nested = metadata["pull_request"]
        assert_pr_open(nested)
        head = metadata["head_sha"]
        if (nested["number"] != ref.number or nested["head"]["sha"] != head
                or nested["base"]["repo"]["full_name"].lower()
                != f"{ref.owner}/{ref.repo}".lower()):
            raise ValueError
        config = parse_config((run_dir / "config.yaml").read_bytes())
        if (canonical(read_json(run_dir / "config.json")) != canonical(config.to_dict())
                or config.verdict != verdict.config):
            raise ValueError
        return ref, head, config, (run_dir / "diff.patch").read_bytes()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ConfigError, GitHubError):
        raise PostingError(
            "Captured metadata, configuration or verdict disagree; inspect the saved run."
        ) from None


def _evidence(run_dir: Path, verdict: Verdict) -> None:
    verdict_path = run_dir / "verdict.json"
    findings_path = run_dir / "findings.json"
    try:
        if verdict_path.exists():
            if verdict_path.read_bytes() != verdict.to_bytes():
                raise PostingError("Saved verdict differs from supplied evidence.")
        else:
            write_verdict(run_dir, verdict)
        findings = [finding.to_dict() for finding in verdict.findings]
        if findings_path.exists():
            if canonical(read_json(findings_path)) != canonical(findings):
                raise PostingError("Saved findings differ from supplied evidence.")
        else:
            atomic_write(findings_path, canonical(findings))
    except (OSError, ValueError, UnicodeError, VerdictArtifactError):
        raise PostingError(
            "Cannot preserve verdict and findings artifacts; inspect the run."
        ) from None


def _saved_receipt(value: Any, ref: PullRequestRef, payload: ReviewPayload) -> PostedReview:
    if not isinstance(value, dict) or set(value) != set(PostedReview.__dataclass_fields__):
        raise PostingError("Saved review receipt is invalid; inspect the posting journal.")
    try:
        return _receipt({"id": value["review_id"], "html_url": value["html_url"],
                         "commit_id": value["commit_id"], "body": value["body"],
                         "state": value["state"], "user": {"login": value["login"]}}, ref, payload)
    except PostingError:
        raise PostingError(
            "Saved review receipt is invalid; inspect the posting journal."
        ) from None


def _deadline_valid(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _validate_state(state: dict[str, Any], intent: dict[str, Any]) -> None:
    fields = {"status", "attempts", "receipt", "failure", "retry_at", "http_status"}
    status = state.get("status")
    attempts = state.get("attempts")
    failure = state.get("failure")
    deadline = state.get("retry_at")
    http_status = state.get("http_status")
    valid = (
        set(state) == set(intent) | fields
        and type(state.get("schema_version")) is int
        and type(state.get("pr_number")) is int
        and all(state.get(key) == value for key, value in intent.items())
        and status in ("prepared", "sending", "unknown", "rejected", "posted")
        and isinstance(attempts, int) and not isinstance(attempts, bool) and 0 <= attempts <= 3
    )
    if status == "rejected":
        valid = (valid and isinstance(attempts, int) and attempts >= 1
                 and state.get("receipt") is None)
        if failure == "throttle":
            valid = (valid and type(http_status) is int and http_status in (403, 429)
                     and _deadline_valid(deadline))
        elif failure == "unsent":
            valid = valid and http_status is None and deadline is None
        elif failure == "permanent":
            valid = (valid and deadline is None and (http_status is None
                     or (type(http_status) is int and 400 <= http_status < 500)))
        else:
            valid = False
    else:
        valid = valid and failure is None and deadline is None and http_status is None
        if status == "prepared":
            valid = valid and attempts == 0 and state.get("receipt") is None
        else:
            valid = valid and type(attempts) is int and attempts >= 1
            if status != "posted":
                valid = valid and state.get("receipt") is None
    if not valid:
        raise PostingError("Posting journal is invalid or differs from evidence; inspect the run.")


def _confirm(
    path: Path, state: dict[str, Any], receipt: PostedReview,
    ref: PullRequestRef, payload: ReviewPayload,
) -> PostedReview:
    validated = _saved_receipt(asdict(receipt), ref, payload)
    updated = state | {"status": "posted", "receipt": asdict(validated), "failure": None,
                       "retry_at": None, "http_status": None}
    atomic_write(path, canonical(updated))
    return validated


def _reconcile(
    transport: ReviewClient, ref: PullRequestRef, payload: ReviewPayload,
    path: Path, state: dict[str, Any],
) -> PostedReview:
    state.update(status="unknown", receipt=None, failure=None, retry_at=None, http_status=None)
    atomic_write(path, canonical(state))
    try:
        reviews = transport.get_reviews(ref)
        login = transport.get_login()
        marker = f"<!-- scrutare-run:{state['run_id']} -->"
        if not isinstance(reviews, list) or not isinstance(login, str):
            raise PostingUncertain(_UNCERTAIN)
        matches = []
        for review in reviews:
            if not isinstance(review, dict):
                raise PostingUncertain(_UNCERTAIN)
            body = review.get("body")
            if isinstance(body, str) and marker in body:
                # Every marker candidate must be valid. Never select a good candidate
                # from an ambiguous group that also contains a malformed one.
                receipt = _receipt(review, ref, payload)
                if receipt.login.lower() != login.lower():
                    raise PostingUncertain(_UNCERTAIN)
                matches.append(receipt)
        if len(matches) != 1:
            raise PostingUncertain(_UNCERTAIN)
    except Exception:
        raise PostingUncertain(_UNCERTAIN) from None
    return _confirm(path, state, matches[0], ref, payload)


def _wait_retry(state: dict[str, Any], sleeper: Callable[[float], None]) -> None:
    if state["status"] != "rejected":
        return
    if state["failure"] == "permanent":
        raise PostingRejected("Review was rejected; inspect the saved run.",
                              status=state["http_status"])
    if state["attempts"] >= 3:
        raise PostingRejected("Review attempt budget is exhausted; inspect the saved run.")
    if state["failure"] == "throttle":
        remaining = max(0.0, state["retry_at"] - _now())
        if remaining > 60:
            raise PostingRateLimited(status=state["http_status"], retry_after=remaining)
        if remaining:
            sleeper(remaining)
        remaining = max(0.0, state["retry_at"] - _now())
        if remaining:
            raise PostingRateLimited(status=state["http_status"], retry_after=remaining)


def post_review(
    run_dir: Path, verdict: Verdict, *, client: ReviewClient | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> PostedReview:
    """Post once from captured inputs, or reconcile and return a confirmed receipt."""
    if not isinstance(verdict, Verdict):
        raise PostingError("verdict: expected a code-derived Verdict")
    try:
        verdict_bytes = verdict.to_bytes()
    except (TypeError, ValueError):
        raise PostingError("Verdict evidence must be encodable as UTF-8 JSON.") from None
    with run_lock(run_dir):
        ref, head, config, diff = _capture(run_dir, verdict)
        path = run_dir / "posting.json"
        existing = path.exists()
        saved = read_json(path) if existing else {}
        if not isinstance(saved, dict):
            raise PostingError("Invalid posting journal; inspect the saved run.")
        state: dict[str, Any] = saved
        run_id = state.get("run_id") if existing else uuid4().hex
        if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
            raise PostingError("Invalid posting run identity; inspect the saved run.")
        payload = build_review_payload(verdict, diff, head_sha=head, strategy=config.strategy,
                                       post_mode=config.github.post_mode, run_id=run_id)
        intent = {"schema_version": 1, "repository": f"{ref.owner}/{ref.repo}",
                  "pr_number": ref.number, "head_sha": head, "event": payload.event,
                  "run_id": run_id, "payload_sha256": _hash(payload.to_bytes()),
                  "verdict_sha256": _hash(verdict_bytes), "diff_sha256": _hash(diff),
                  "config_sha256": _hash(canonical(config.to_dict()))}
        if existing:
            _validate_state(state, intent)
            try:
                if (run_dir / "review-payload.json").read_bytes() != payload.to_bytes():
                    raise PostingError("Saved review payload differs from captured evidence.")
            except OSError:
                raise PostingError("Saved review payload is missing; inspect the run.") from None
        _evidence(run_dir, verdict)
        if not existing:
            if (run_dir / "review-payload.json").exists():
                raise PostingError("Review payload has no journal; inspect the run before posting.")
            state = intent | {"status": "prepared", "attempts": 0, "receipt": None,
                              "failure": None, "retry_at": None, "http_status": None}
            atomic_write(path, canonical(state))
            atomic_write(run_dir / "review-payload.json", payload.to_bytes())
        if state["status"] == "posted":
            return _saved_receipt(state["receipt"], ref, payload)
        transport = client if client is not None else ReviewClient(sleeper=sleeper)
        if state["status"] in ("sending", "unknown"):
            return _reconcile(transport, ref, payload, path, state)
        wait = sleeper if sleeper is not None else sleep
        while True:
            _wait_retry(state, wait)
            assert_pr_open(transport.get_pr(ref))
            state.update(status="sending", attempts=state["attempts"] + 1,
                         failure=None, retry_at=None, http_status=None)
            atomic_write(path, canonical(state))
            try:
                receipt = transport.create_review(ref, payload)
                if not isinstance(receipt, PostedReview):
                    raise PostingUncertain(_UNCERTAIN)
                _saved_receipt(asdict(receipt), ref, payload)
            except PostingRateLimited as error:
                deadline = _now() + error.retry_after
                if not math.isfinite(deadline):
                    return _reconcile(transport, ref, payload, path, state)
                state.update(status="rejected", failure="throttle", retry_at=deadline,
                             http_status=error.status)
                atomic_write(path, canonical(state))
                continue
            except PostingRejected as error:
                state.update(status="rejected", failure="unsent" if error.unsent else "permanent",
                             http_status=error.status)
                atomic_write(path, canonical(state))
                raise
            except Exception:
                return _reconcile(transport, ref, payload, path, state)
            return _confirm(path, state, receipt, ref, payload)
