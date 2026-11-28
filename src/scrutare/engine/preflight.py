"""Opt-in checks that each configured model route answers, before anything is claimed.

A review, an App job and an evaluation campaign each claim something before their first
model call: a run directory, a review budget, a one-run output directory. A route that
cannot answer is found only after that claim, by spending it. These checks run first.

``route`` sends ``GET {base_url}/models`` on an openai rail and requires the configured
model in the answer. It is not a model request: no prompt, no tokens, no change to what
a review later sends. ``request`` sends one tiny schema-bound request through nare with
the rail's own options, so the same transport, retries and answer encoding apply.

A failure raises :class:`PreflightError` with a typed reason and the report. Nothing in
this module writes a file, and the credential travels only in a request header to the
rail's own origin, never on argv. Redirects are refused, so it never crosses an origin.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from scrutare.config import ModelRail, ReviewConfig
from scrutare.engine.nare_session import (
    SessionRuntimeError,
    _environment,
    _executable,
    _rail_argv,
    _rail_options,
    _rail_refusal,
    _start_process,
    _takes_value,
    _wait_capture,
    inspect_nare_runtime,
    modern_contract,
)
from scrutare.engine.session_models import NareCapability, NareRuntime

PreflightReason = Literal[
    "origin_refused", "credential_missing", "route_unreachable", "gateway_error",
    "credential_refused", "route_refused", "route_invalid", "model_missing",
    "runtime_refused", "request_failed", "request_timeout", "answer_invalid",
    "encoding_refused",
]
REASONS: tuple[str, ...] = get_args(PreflightReason)

# nare's own defaults for a rail without base_url; a check asks the same origin.
OPENAI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_BASE_URLS = {"openai": OPENAI_BASE_URL, "anthropic": "https://api.anthropic.com"}
_MAX_BODY = 8 * 1024 * 1024
# The tiny request: a constant prompt and a one-field schema, with tools off so every turn
# carries the rail's answer encoding (nare constrains every turn of a tool-free run).
REQUEST_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"], "additionalProperties": False}
REQUEST_PROMPT = 'Reply with the JSON object {"ok": true} and nothing else.'
REQUEST_SYSTEM = "You answer with one JSON object that matches the required schema."
REQUEST_BUDGET_TOKENS = 16000


class PreflightError(Exception):
    """A route that did not answer; ``report`` says which check and why."""

    def __init__(self, report: PreflightReport) -> None:
        assert report.reason is not None
        super().__init__(f"preflight: {report.reason}; the review was not started")
        self.reason = report.reason
        self.report = report


@dataclass(frozen=True)
class PreflightReport:
    """Each check that ran, in order; ``reason`` is the first failure's, or None."""

    checks: tuple[dict[str, Any], ...]

    @property
    def reason(self) -> str | None:
        return next((check["reason"] for check in self.checks
                     if check["status"] == "failed"), None)

    @property
    def passed(self) -> bool:
        return self.reason is None

    def to_dict(self) -> dict[str, Any]:
        return {"status": "passed" if self.passed else "failed", "reason": self.reason,
                "checks": [dict(check) for check in self.checks]}


class _Redirected(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    """Refuse a redirect before the Authorization header could follow it."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        raise _Redirected


Opener = Callable[[Request, float], Any]


def _open(request: Request, timeout: float) -> Any:
    # No proxy from the environment: nare's sessions run without one, so neither does this.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def rail_origin(rail: ModelRail) -> str:
    return origin(rail.base_url or _DEFAULT_BASE_URLS[rail.provider])


def models_url(rail: ModelRail) -> str:
    """Where nare's openai transport is rooted, with ``/models`` as it adds ``/chat/...``."""
    return (rail.base_url or OPENAI_BASE_URL).rstrip("/") + "/models"


def _route(rail: ModelRail, seconds: float, origins: tuple[str, ...] | None,
           opener: Opener) -> dict[str, Any]:
    url = models_url(rail)
    record: dict[str, Any] = {"check": "route", "provider": rail.provider, "model": rail.model,
                              "url": url, "status": "failed", "reason": None}
    started = time.monotonic()

    def failed(reason: PreflightReason, **detail: Any) -> dict[str, Any]:
        record.update(reason=reason, seconds=round(time.monotonic() - started, 3), **detail)
        return record

    # The App's allowlist decides where a credential may go; a route outside it is not asked.
    if origins is not None and origin(url) not in origins:
        return failed("origin_refused")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return failed("credential_missing")
    request = Request(url, method="GET", headers={"Authorization": "Bearer " + key,
                                                  "Accept": "application/json"})
    try:
        with opener(request, seconds) as response:
            if origin(response.url) != origin(url):
                return failed("route_refused")
            raw = response.read(_MAX_BODY + 1)
    except _Redirected:
        return failed("route_refused")
    except HTTPError as error:
        code = error.code
        error.close()
        reason: PreflightReason = (
            "credential_refused" if code in (401, 403)
            else "gateway_error" if code >= 500 else "route_refused")
        return failed(reason, http_status=code)
    except (OSError, ValueError):
        # URLError, a refused or reset connection, a TLS failure and a timeout alike.
        return failed("route_unreachable")
    try:
        if len(raw) > _MAX_BODY:
            raise ValueError
        document = json.loads(raw)
        listed = document["data"]
        if not isinstance(listed, list):
            raise ValueError
        ids = {item["id"] for item in listed}
        if not all(isinstance(identity, str) for identity in ids):
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        return failed("route_invalid", http_status=200)
    if rail.model not in ids:
        return failed("model_missing", http_status=200, listed=len(ids))
    record.update(status="passed", http_status=200, listed=len(ids),
                  seconds=round(time.monotonic() - started, 3))
    return record


def _result_line(stdout: bytes) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.strip():
            try:
                document = json.loads(line)
            except ValueError:
                return None
            return document if isinstance(document, dict) and document.get(
                "type") == "result" else None
    return None


def request_argv(executable: Path, rail: ModelRail, capability: NareCapability,
                 directory: Path) -> list[str]:
    """One tiny tool-free request with the rail's own options and Scrutare's pinned policy."""
    prompt_file = _takes_value(capability, "--prompt-file")
    return [str(executable), "run",
            *(("--prompt-file", str(directory / "prompt.txt")) if prompt_file
              else (REQUEST_PROMPT,)),
            "--system=" + REQUEST_SYSTEM, "--tools", "none", "--root", str(directory / "cwd"),
            *_rail_argv(rail, capability),
            "--jsonl", "--yes", "--contract", str(capability.contract),
            "--schema", str(directory / "preflight.schema.json"),
            "--budget-tokens", str(REQUEST_BUDGET_TOKENS),
            "--session", str(directory / "session.json"), "--max-turns", "2",
            *_rail_options(rail)]


async def _request(rail: ModelRail, runtime: NareRuntime, capability: NareCapability,
                   seconds: float) -> dict[str, Any]:
    record: dict[str, Any] = {"check": "request", "provider": rail.provider,
                              "model": rail.model, "structured_output": rail.structured_output,
                              "status": "failed", "reason": None}
    started = time.monotonic()

    def done(reason: PreflightReason | None, **detail: Any) -> dict[str, Any]:
        record.update(status="passed" if reason is None else "failed", reason=reason,
                      seconds=round(time.monotonic() - started, 3), **detail)
        return record

    if not modern_contract(capability.contract) or not _takes_value(capability, "--tools"):
        return done("runtime_refused")
    if _rail_refusal(rail, capability) is not None:
        return done("runtime_refused")
    try:
        executable = _executable(runtime)
    except SessionRuntimeError:
        return done("runtime_refused")
    with tempfile.TemporaryDirectory(prefix="scrutare-preflight-") as name:
        directory = Path(name)
        for child in ("cwd", "home", "tmp"):
            (directory / child).mkdir(mode=0o700)
        (directory / "preflight.schema.json").write_text(json.dumps(REQUEST_SCHEMA))
        (directory / "prompt.txt").write_text(REQUEST_PROMPT)
        argv = request_argv(executable, rail, capability, directory)
        process, cancelled = await _start_process(
            argv, _environment(directory / "home", directory / "tmp", rail.provider),
            directory / "cwd")
        capture = asyncio.create_task(process.communicate())
        try:
            stdout, _ = await _wait_capture(process, capture, seconds, cancelled=cancelled)
        except asyncio.TimeoutError:
            return done("request_timeout")
    result = _result_line(stdout)
    if result is None:
        # Exit 2 means nare never started the session: an invocation it refused.
        return done("runtime_refused" if process.returncode == 2 else "request_failed",
                    exit_code=process.returncode)
    usage = result.get("usage")
    detail = {"exit_code": process.returncode, "stop_reason": result.get("stop_reason"),
              "turns": result.get("turns"), "retries": result.get("retries"),
              "usage": {key: usage.get(key) for key in ("input", "output", "total")}
              if isinstance(usage, dict) else None}
    output = result.get("output")
    if (result.get("status") == "done" and process.returncode == 0
            and isinstance(output, dict) and type(output.get("ok")) is bool):
        return done(None, **detail)
    stop = result.get("stop_reason")
    if stop == "timeout":
        return done("request_timeout", **detail)
    if stop is not None:
        return done("answer_invalid", **detail)
    failure = _failure(result) if capability.provider_failure else None
    if failure is None:
        # A runtime without nare's failure record says how the request failed only in its
        # error text, which a caller must not parse.
        return done("request_failed", **detail)
    return done(_failure_reason(failure), failure=failure, **detail)


def _failure(result: dict[str, Any]) -> dict[str, Any] | None:
    """nare's failure record of the failed request, keeping only its typed fields."""
    record = result.get("failure")
    if not isinstance(record, dict):
        return None
    status, reason = record.get("status"), record.get("reason")
    if (status is not None and (type(status) is not int or not 400 <= status <= 599)) or (
            reason is not None and not isinstance(reason, str)):
        return None
    return {"reason": reason, "status": status,
            "transient": record.get("transient") is True,
            "constrained": record.get("constrained") is True,
            "attempts": record.get("attempts") if type(record.get("attempts")) is int else None}


def _failure_reason(failure: dict[str, Any]) -> PreflightReason:
    """The typed reason for a failed request, from nare's record (ViviDynamics/nare#314)."""
    status, reason = failure["status"], failure["reason"]
    if status in (400, 422) and failure["constrained"]:
        return "encoding_refused"
    if status in (401, 403):
        return "credential_refused"
    if (status is not None and status >= 500) or reason == "overloaded":
        return "gateway_error"
    if reason == "connection" and status is None:
        return "route_unreachable"
    if reason in ("timeout", "stream"):
        return "request_timeout" if reason == "timeout" else "request_failed"
    return "request_failed"


def _distinct(rails: tuple[ModelRail, ...]) -> tuple[ModelRail, ...]:
    seen: list[ModelRail] = []
    for rail in rails:
        if rail not in seen:
            seen.append(rail)
    return tuple(seen)


async def run_preflight(config: ReviewConfig, runtime: NareRuntime, *,
                        origins: tuple[str, ...] | None = None,
                        opener: Opener = _open) -> PreflightReport | None:
    """Run the configured checks, or return None when none is configured.

    Raises PreflightError on the first failure. ``origins``, when given, is the App's
    model-origin allowlist: a route outside it fails before any request is made.
    """
    settings = config.models.preflight
    if not settings.enabled:
        return None
    rails = _distinct(config.models.rails)
    checks: list[dict[str, Any]] = []

    def stop() -> PreflightReport:
        report = PreflightReport(tuple(checks))
        if not report.passed:
            raise PreflightError(report)
        return report

    for rail in rails if settings.route else ():
        if rail.provider != "openai":
            # The anthropic rail is the vendor's own API, never a self-hosted route, and
            # its model list is not where nare's anthropic transport is rooted.
            checks.append({"check": "route", "provider": rail.provider, "model": rail.model,
                           "status": "skipped", "reason": None,
                           "skipped": "the route check lists openai routes only"})
            continue
        if any(check.get("url") == models_url(rail) and check.get("model") == rail.model
               for check in checks):
            continue
        checks.append(await asyncio.to_thread(
            _route, rail, settings.seconds, origins, opener))
        stop()
    if settings.request:
        try:
            capability = await inspect_nare_runtime(runtime)
        except SessionRuntimeError:
            checks.append({"check": "request", "status": "failed",
                           "reason": "runtime_refused"})
            return stop()
        for rail in rails:
            if origins is not None and rail_origin(rail) not in origins:
                checks.append({"check": "request", "provider": rail.provider,
                               "model": rail.model, "status": "failed",
                               "reason": "origin_refused"})
                return stop()
            checks.append(await _request(rail, runtime, capability, settings.seconds))
            stop()
    return stop()


def not_started(report: PreflightReport, version: str) -> dict[str, Any]:
    """The document a command prints when its preflight failed: nothing was started."""
    return {"schema_version": 1, "scrutare_version": version, "status": "not_started",
            "reason": report.reason, "preflight": report.to_dict()}
