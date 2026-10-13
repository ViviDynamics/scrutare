"""Scoped GitHub App credentials and isolated calls to the production review CLI."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import jwt

from scrutare.config import ConfigError, parse_config
from scrutare.interfaces.app import (
    MAX_BODY,
    REVIEWED_ACTIONS,
    AppError,
    AppSettings,
    JobStore,
    WorkerStopping,
)

RequestFn = Callable[[str, str, str, dict[str, Any] | None], dict[str, Any]]


class NoRedirect(HTTPRedirectHandler):
    """Refuse redirects before an Authorization header could cross an origin."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        raise AppError("GitHub App API redirects are refused")


def github_request(method: str, path: str, token: str, body: dict[str, Any] | None,
                   *, opener: Any = None) -> dict[str, Any]:
    """Fixed GitHub HTTPS origin; never surface tokens or remote error bodies."""
    request = Request("https://api.github.com" + path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28",
                               "Content-Type": "application/json"})
    try:
        transport = opener if opener is not None else build_opener(NoRedirect()).open
        with transport(request, timeout=60) as response:
            if urlsplit(response.url).netloc != "api.github.com":
                raise AppError("GitHub App response redirected outside the API")
            raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise AppError("GitHub App response is too large")
        result = json.loads(raw) if raw.strip() else {}
        if isinstance(result, list):
            return {"items": result}
        if not isinstance(result, dict):
            raise ValueError
        return result
    except AppError:
        raise
    except HTTPError as error:
        if error.code == 404:
            raise AppError("GitHub App resource was not found", 404) from None
        raise AppError("GitHub App request failed; check installation permissions") from None
    except (OSError, ValueError):
        raise AppError("GitHub App request failed; check installation permissions") from None


class GitHubAppClient:
    """Sign short-lived App JWTs, then narrow tokens to one permitted repository."""

    def __init__(self, settings: AppSettings, *, request: RequestFn = github_request):
        self.settings = settings
        self.request = request

    def _jwt(self) -> str:
        try:
            now = int(time.time())
            return jwt.encode({"iat": now - 60, "exp": now + 480,
                               "iss": str(self.settings.app_id)},
                              self.settings.private_key.read_bytes(), algorithm="RS256")
        except (OSError, ValueError, jwt.PyJWTError):
            raise AppError("cannot sign GitHub App identity; check private key") from None

    def installation_token(self, repository: str, installation: int) -> str:
        self.settings.authorize(repository, 1, installation)
        result = self.request("POST", f"/app/installations/{installation}/access_tokens",
                              self._jwt(),
                              {"repositories": [repository.split("/")[1]], "permissions": {
                                  "contents": "read", "pull_requests": "write", "issues": "read"}})
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise AppError("GitHub App returned no installation token")
        return token

    def head(self, repository: str, number: int, token: str) -> str:
        """Read the pull request head a review started now would capture."""
        result = self.request("GET", f"/repos/{repository}/pulls/{number}", token, None)
        head = result.get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if (result.get("number") != number or not isinstance(sha, str)
                or re.fullmatch(r"[0-9a-f]{40}", sha) is None):
            raise AppError("GitHub App returned invalid pull request identity")
        return sha

    def configuration(self, repository: str, token: str) -> bytes:
        """Honor maintained repository config, never attacker changes on the PR head."""
        metadata = self.request("GET", f"/repos/{repository}", token, None)
        branch = metadata.get("default_branch")
        if metadata.get("full_name") != repository or not isinstance(branch, str) or not branch:
            raise AppError("GitHub App returned invalid repository identity")
        try:
            result = self.request("GET", f"/repos/{repository}/contents/scrutare.yaml?ref="
                                  + quote(branch, safe=""), token, None)
        except AppError as error:
            if error.status != 404:
                raise
            result = {}
        if result.get("type") != "file" or result.get("encoding") != "base64":
            raise AppError("repository requires a scrutare.yaml file on its default branch")
        try:
            content = base64.b64decode("".join(result["content"].split()), validate=True)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AppError("invalid repository configuration encoding") from None
        if len(content) > MAX_BODY:
            raise AppError("repository configuration is too large")
        return content

    def failed_deliveries(self, now: float | None = None) -> list[tuple[int, str]]:
        """List recent reviewable deliveries GitHub could not hand to this service.

        GitHub never redelivers on its own. Only transport failures and 5xx answers
        count: a 4xx is this service's own deliberate refusal.
        """
        result = self.request("GET", "/app/hook/deliveries?per_page=100", self._jwt(), None)
        current = time.time() if now is None else now
        latest: dict[str, int] = {}
        delivered: set[str] = set()
        failed: set[str] = set()
        try:
            for item in result["items"]:
                guid, identity, code = item["guid"], item["id"], item["status_code"]
                if (not isinstance(guid, str) or not guid or type(identity) is not int
                        or type(code) is not int):
                    raise ValueError
                age = current - datetime.fromisoformat(
                    item["delivered_at"].replace("Z", "+00:00")).timestamp()
                if (item["event"] != "pull_request" or item["action"] not in REVIEWED_ACTIONS
                        or item["installation_id"] not in self.settings.installations.values()
                        or age > 86400):
                    continue
                latest[guid] = max(identity, latest.get(guid, 0))
                if 200 <= code < 300:
                    delivered.add(guid)
                elif not 400 <= code < 500:
                    failed.add(guid)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AppError("GitHub App returned invalid delivery records") from None
        return sorted((latest[guid], guid) for guid in failed - delivered)

    def redeliver(self, delivery: int) -> None:
        self.request("POST", f"/app/hook/deliveries/{delivery}/attempts", self._jwt(), None)


def reconcile(store: JobStore, client: Any) -> int:
    """Ask GitHub to resend missed deliveries; they return through signed admission."""
    requested = 0
    for delivery, guid in client.failed_deliveries():
        if store.redelivery_due(guid):
            client.redeliver(delivery)
            requested += 1
    return requested


_DEFAULT_ORIGINS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com"}


def _config_origins(raw: bytes, settings: AppSettings) -> None:
    config = parse_config(raw)
    rails = [config.models.default, *(rail for _, rail in config.models.overrides)]
    for rail in rails:
        parsed = urlsplit(rail.base_url or _DEFAULT_ORIGINS[rail.provider])
        origin = f"{parsed.scheme}://{parsed.netloc}"
        # A vendor's own origin only ever receives that vendor's credential.
        foreign = any(origin == default and provider != rail.provider
                      for provider, default in _DEFAULT_ORIGINS.items())
        if parsed.scheme != "https" or origin not in settings.model_origins or foreign:
            raise AppError("repository model origin is not authorized by the App operator")


def worker_environment(token: str) -> dict[str, str]:
    environment = {key: os.environ[key] for key in (
        "PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY") if key in os.environ}
    environment.update(GH_TOKEN=token, GH_HOST="github.com", PYTHONUNBUFFERED="1")
    return environment


def _runs(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")}


def _delivery(root: Path, before: set[Path]) -> str:
    """Read this job's posting journal: confirmed, not_sent, or uncertain."""
    try:
        states = set()
        for run in _runs(root) - before:
            journal = run / "posting.json"
            if not journal.exists():
                states.add("not_sent")
                continue
            status = json.loads(journal.read_bytes()).get("status")
            states.add({"posted": "confirmed", "prepared": "not_sent",
                        "rejected": "not_sent"}.get(status, "uncertain"))
        if not states:
            return "not_sent"
        return states.pop() if len(states) == 1 else "uncertain"
    except (OSError, ValueError, AttributeError, TypeError):
        return "uncertain"


def run_job(settings: AppSettings, store: JobStore, job: dict[str, Any], *,
            client: Any = None, runner: Any = subprocess.run) -> None:
    """Run the same installed engine; retain every job directory on success or failure."""
    root = (settings.data_dir / "work" / job["id"]).absolute()
    repository_root = (settings.data_dir / "repositories" / hashlib.sha256(
        job["repository"].encode()).hexdigest()).absolute()
    runs = repository_root / ".scrutare" / "runs"
    before: set[Path] | None = None
    code: int | None = None

    def stop(error: str, *, interrupted: bool = False) -> None:
        # Callers decide whether to resubmit from `delivery`, never from the status alone.
        delivery = "not_sent" if before is None else _delivery(runs, before)
        if code == 0 and delivery != "confirmed":
            delivery = "uncertain"
        result: dict[str, Any] = {"error": error, "delivery": delivery,
                                  "work_dir": str(root)}
        if code is not None:
            result["exit_code"] = code
        status = "interrupted" if interrupted or delivery == "uncertain" else "failed"
        store.finish(job["id"], status, result)

    try:
        settings.authorize(job["repository"], job["number"], job["installation"])
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        repository_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        api = client if client is not None else GitHubAppClient(settings)
        token = api.installation_token(job["repository"], job["installation"])
        if not job["delivery"].startswith("api:"):
            # A late or redelivered event must not re-review a head already reviewed.
            prior = store.reviewed(job["repository"], job["number"],
                                   api.head(job["repository"], job["number"], token))
            if prior is not None:
                store.finish(job["id"], "superseded", {"superseded_by": prior})
                return
        raw = api.configuration(job["repository"], token)
        _config_origins(raw, settings)
        config_path = root / "scrutare.yaml"
        config_path.write_bytes(raw)
        environment = worker_environment(token)
        environment["HOME"] = str(root)
        argv = [sys.executable, "-m", "scrutare", "review", "--pr",
                f"https://github.com/{job['repository']}/pull/{job['number']}",
                "--config", str(config_path), "--nare-executable",
                os.environ.get("SCRUTARE_APP_NARE_EXECUTABLE", "nare")]
        known = _runs(runs)
        try:
            completed = runner(argv, cwd=repository_root, env=environment,
                               capture_output=True, shell=False, timeout=settings.job_timeout)
        except WorkerStopping:
            store.release(job["id"])
            return
        except subprocess.TimeoutExpired as expired:
            before = known
            (root / "stdout.json").write_bytes(expired.stdout or b"")
            (root / "stderr.log").write_bytes(expired.stderr or b"")
            stop("review exceeded the job deadline; inspect job artifacts", interrupted=True)
            return
        before, code = known, completed.returncode
        # CLI diagnostics exclude credentials. Save both streams for failed-run inspection.
        (root / "stdout.json").write_bytes(completed.stdout)
        (root / "stderr.log").write_bytes(completed.stderr)
        if code != 0:
            # Exit 130 and signal deaths are interruptions. Exit 1 covers refusals, a
            # failure after confirmed delivery and an uncertain POST alike, so the
            # posting journal, not the exit code, says whether a review was delivered.
            stopped = code == 130 or code < 0
            stop("review CLI was interrupted; inspect job artifacts" if stopped
                 else "review CLI failed; inspect job artifacts", interrupted=stopped)
            return
        # Exit 0 means the CLI confirmed delivery. An unusable document is therefore an
        # uncertain outcome for callers, never grounds for an automatic second review.
        try:
            result = json.loads(completed.stdout)
            run_dir = Path(result["run_dir"])
            if (type(result["schema_version"]) is not int or result["schema_version"] != 1
                    or result["status"] != "posted"
                    or result["verdict"] not in ("approve", "changes_requested", "escalated")
                    or re.fullmatch(r"[0-9a-f]{40}", result["head_sha"]) is None
                    or not run_dir.resolve().is_relative_to(runs.resolve())
                    or (run_dir / "result.json").read_bytes() != completed.stdout):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise AppError("review CLI succeeded without its installed result; "
                           "inspect job artifacts") from None
        store.finish(job["id"], "posted", result)
    except (AppError, ConfigError) as error:
        stop(str(error))
    except (OSError, ValueError, KeyError, TypeError):
        stop("App worker failed; inspect job artifacts")
