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
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import jwt

from scrutare.config import ConfigError, parse_config
from scrutare.interfaces.app import MAX_BODY, AppError, AppSettings, JobStore

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
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError):
        raise AppError("GitHub App request failed; check installation permissions") from None


class GitHubAppClient:
    """Sign short-lived App JWTs, then narrow tokens to one permitted repository."""

    def __init__(self, settings: AppSettings, *, request: RequestFn = github_request):
        self.settings = settings
        self.request = request

    def installation_token(self, repository: str, installation: int) -> str:
        self.settings.authorize(repository, 1, installation)
        try:
            now = int(time.time())
            app_jwt = jwt.encode({"iat": now - 60, "exp": now + 480,
                                  "iss": str(self.settings.app_id)},
                                 self.settings.private_key.read_bytes(), algorithm="RS256")
        except (OSError, ValueError, jwt.PyJWTError):
            raise AppError("cannot sign GitHub App identity; check private key") from None
        result = self.request("POST", f"/app/installations/{installation}/access_tokens", app_jwt,
                              {"repositories": [repository.split("/")[1]], "permissions": {
                                  "contents": "read", "pull_requests": "write", "issues": "read"}})
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise AppError("GitHub App returned no installation token")
        return token

    def configuration(self, repository: str, token: str) -> bytes:
        """Honor maintained repository config, never attacker changes on the PR head."""
        metadata = self.request("GET", f"/repos/{repository}", token, None)
        branch = metadata.get("default_branch")
        if metadata.get("full_name") != repository or not isinstance(branch, str) or not branch:
            raise AppError("GitHub App returned invalid repository identity")
        result = self.request("GET", f"/repos/{repository}/contents/scrutare.yaml?ref="
                              + quote(branch, safe=""), token, None)
        if result.get("type") != "file" or result.get("encoding") != "base64":
            raise AppError("repository requires a scrutare.yaml file on its default branch")
        try:
            content = base64.b64decode("".join(result["content"].split()), validate=True)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise AppError("invalid repository configuration encoding") from None
        if len(content) > MAX_BODY:
            raise AppError("repository configuration is too large")
        return content


def _config_origins(raw: bytes, settings: AppSettings) -> None:
    config = parse_config(raw)
    rails = [config.models.default, *(rail for _, rail in config.models.overrides)]
    for rail in rails:
        url = rail.base_url or {"anthropic": "https://api.anthropic.com",
                                "openai": "https://api.openai.com"}[rail.provider]
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme != "https" or origin not in settings.model_origins:
            raise AppError("repository model origin is not authorized by the App operator")


def worker_environment(token: str) -> dict[str, str]:
    environment = {key: os.environ[key] for key in (
        "PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY") if key in os.environ}
    environment.update(GH_TOKEN=token, GH_HOST="github.com", PYTHONUNBUFFERED="1")
    return environment


def run_job(settings: AppSettings, store: JobStore, job: dict[str, Any], *,
            client: Any = None, runner: Any = subprocess.run) -> None:
    """Run the same installed engine; retain every job directory on success or failure."""
    root = (settings.data_dir / "work" / job["id"]).absolute()
    repository_root = (settings.data_dir / "repositories" / hashlib.sha256(
        job["repository"].encode()).hexdigest()).absolute()
    try:
        settings.authorize(job["repository"], job["number"], job["installation"])
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        repository_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        api = client if client is not None else GitHubAppClient(settings)
        token = api.installation_token(job["repository"], job["installation"])
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
        try:
            completed = runner(argv, cwd=repository_root, env=environment,
                               capture_output=True, shell=False, timeout=settings.job_timeout)
        except subprocess.TimeoutExpired as expired:
            (root / "stdout.json").write_bytes(expired.stdout or b"")
            (root / "stderr.log").write_bytes(expired.stderr or b"")
            store.finish(job["id"], "interrupted", {
                "error": "review exceeded the job deadline; delivery may have occurred; "
                         "inspect job artifacts", "work_dir": str(root)})
            return
        # CLI diagnostics exclude credentials. Save both streams for failed-run inspection.
        (root / "stdout.json").write_bytes(completed.stdout)
        (root / "stderr.log").write_bytes(completed.stderr)
        if completed.returncode != 0:
            # Exit 130 and signal deaths can follow a delivered review; 1 and 2 are refusals
            # or failures whose own diagnostic says whether delivery was confirmed.
            stopped = completed.returncode == 130 or completed.returncode < 0
            store.finish(job["id"], "interrupted" if stopped else "failed", {
                "error": ("review CLI was interrupted; delivery may have occurred; "
                          "inspect job artifacts" if stopped
                          else "review CLI failed; inspect job artifacts"),
                "exit_code": completed.returncode, "work_dir": str(root)})
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
                    or not run_dir.resolve().is_relative_to(
                        (repository_root / ".scrutare" / "runs").resolve())
                    or (run_dir / "result.json").read_bytes() != completed.stdout):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise AppError("review CLI succeeded without its installed result; delivery may "
                           "have occurred; inspect job artifacts") from None
        store.finish(job["id"], "posted", result)
    except (AppError, ConfigError) as error:
        store.finish(job["id"], "failed", {"error": str(error), "work_dir": str(root)})
    except (OSError, ValueError, KeyError, TypeError):
        store.finish(job["id"], "failed", {"error": "App worker failed; inspect job artifacts",
                                           "work_dir": str(root)})
