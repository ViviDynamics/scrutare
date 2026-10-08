"""Authenticated GitHub App intake and a durable, bounded single-worker queue."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrutare.engine.github import GitHubError, resolve_pr

MAX_BODY = 1024 * 1024


class AppError(ValueError):
    """A safe HTTP/worker diagnostic without credentials or remote bodies."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AppSettings:
    app_id: int
    private_key: Path
    webhook_secret: str
    api_token: str
    installations: dict[str, int]
    data_dir: Path
    model_origins: tuple[str, ...] = ()
    max_pending: int = 100

    def __post_init__(self) -> None:
        if (type(self.app_id) is not int or self.app_id <= 0
                or not self.webhook_secret or not self.api_token
                or type(self.max_pending) is not int or self.max_pending <= 0
                or not self.installations):
            raise AppError("App settings require identity, secrets, repositories and queue limit")
        for repo, installation in self.installations.items():
            self.authorize(repo, 1, installation)

    def authorize(self, repository: object, number: object, installation: object) -> None:
        if (not isinstance(repository, str) or type(number) is not int or number <= 0
                or type(installation) is not int or installation <= 0):
            raise AppError("invalid review target")
        try:
            resolve_pr(str(number), repository)
        except GitHubError:
            raise AppError("invalid review target") from None
        if self.installations.get(repository) != installation:
            raise AppError("repository installation is not authorized", 403)


class JobStore:
    """SQLite admissions and leases; running work is never replayed after restart."""

    def __init__(self, path: Path, max_pending: int = 100):
        self.path = path
        self.max_pending = max_pending
        if type(max_pending) is not int or max_pending <= 0:
            raise AppError("invalid queue limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, delivery TEXT UNIQUE NOT NULL,
                repository TEXT NOT NULL, number INTEGER NOT NULL,
                installation INTEGER NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, result TEXT)""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def enqueue(self, delivery: str, repository: str, number: int,
                installation: int) -> dict[str, Any]:
        if not isinstance(delivery, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", delivery):
            raise AppError("invalid delivery identity")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM jobs WHERE delivery = ?", (delivery,)).fetchone()
            if prior is not None:
                if (prior["repository"], prior["number"], prior["installation"]) != (
                        repository, number, installation):
                    raise AppError("delivery identity conflicts with prior target", 409)
                return self._job(prior)
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.max_pending:
                raise AppError("review queue is full; retry later", 503)
            identity = uuid.uuid4().hex
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL)",
                       (identity, delivery, repository, number, installation, time.time()))
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (identity,)).fetchone()
            return self._job(row)

    def get(self, identity: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (identity,)).fetchone()
        if row is None:
            raise AppError("job not found", 404)
        return self._job(row)

    def claim(self) -> dict[str, Any] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (row["id"],))
            job = self._job(row)
            job["status"] = "running"
            return job

    def finish(self, identity: str, status: str, result: dict[str, Any]) -> None:
        if status not in {"posted", "failed", "interrupted"}:
            raise AppError("invalid terminal job status")
        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET status = ?, result = ? "
                                "WHERE id = ? AND status = 'running'",
                                (status, json.dumps(result, allow_nan=False), identity))
            if cursor.rowcount != 1:
                raise AppError("job does not own a running lease", 409)

    def recover_interrupted(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE jobs SET status = 'interrupted', result = ? "
                       "WHERE status = 'running'", (json.dumps({"error":
                       "worker restarted; delivery may have occurred; inspect saved evidence"}),))


def admit_webhook(settings: AppSettings, store: JobStore, event: str, delivery: str,
                  signature: str, body: bytes) -> dict[str, Any]:
    """Authenticate exact bytes before parsing an installed-repository PR event."""
    expected = "sha256=" + hmac.new(settings.webhook_secret.encode(), body,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AppError("invalid webhook signature", 401)
    if len(body) > MAX_BODY:
        raise AppError("request body is too large", 413)
    try:
        document = json.loads(body)
        if not isinstance(document, dict):
            raise ValueError
        if event != "pull_request" or document.get("action") not in {
                "opened", "synchronize", "reopened", "ready_for_review"}:
            return {"status": "ignored"}
        repository = document["repository"]["full_name"]
        number = document["number"]
        installation = document["installation"]["id"]
        settings.authorize(repository, number, installation)
        pr = document["pull_request"]
        if (type(pr["number"]) is not int or pr["number"] != number
                or pr["base"]["repo"]["full_name"] != repository):
            raise ValueError
        if pr["state"] != "open":
            return {"status": "ignored"}
        return store.enqueue("webhook:" + delivery, repository, number, installation)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, AppError):
            raise
        raise AppError("invalid pull request event") from None
