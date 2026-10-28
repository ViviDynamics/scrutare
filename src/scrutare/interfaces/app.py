"""Authenticated GitHub App intake and a durable, bounded single-worker queue."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrutare.engine.github import GitHubError, resolve_pr

MAX_BODY = 1024 * 1024
REVIEWED_ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review"})
REPLY_EVENT = "pull_request_review_comment"
REPLY_ACTIONS = frozenset({"created", "edited"})
REPLY_DELIVERY = "webhook:reply:"
_ORIGIN = re.compile(r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?")
_SHA = re.compile(r"[0-9a-f]{40}")


class AppError(ValueError):
    """A safe HTTP/worker diagnostic without credentials or remote bodies."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class WorkerStopping(Exception):
    """Shutdown began before a claimed job started its review."""


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
    # Installation tokens last one hour; stop a review before its credential expires.
    job_timeout: int = 3300
    whole_review_retry: bool = False

    def __post_init__(self) -> None:
        if (type(self.app_id) is not int or self.app_id <= 0
                or not self.webhook_secret or not self.api_token
                or type(self.max_pending) is not int or self.max_pending <= 0
                or type(self.job_timeout) is not int or not 0 < self.job_timeout <= 3300
                or type(self.whole_review_retry) is not bool
                or not self.installations
                or not isinstance(self.model_origins, tuple) or not self.model_origins
                or any(not isinstance(origin, str) or _ORIGIN.fullmatch(origin) is None
                       for origin in self.model_origins)):
            raise AppError("App settings require identity, secrets, repositories, "
                           "HTTPS model origins and limits")
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
    """SQLite admissions and leases; running work is never replayed after restart.

    Statuses: queued, running, then posted, unchanged, failed, interrupted or superseded.
    """

    def __init__(self, path: Path, max_pending: int = 100):
        self.path = path
        self.max_pending = max_pending
        if type(max_pending) is not int or max_pending <= 0:
            raise AppError("invalid queue limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, delivery TEXT UNIQUE NOT NULL,
                repository TEXT NOT NULL, number INTEGER NOT NULL,
                installation INTEGER NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, result TEXT, head TEXT, reviewed TEXT,
                claimed REAL)""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, created)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_target ON jobs (repository, number)")
            db.execute("""CREATE TABLE IF NOT EXISTS redeliveries (
                guid TEXT PRIMARY KEY, attempts INTEGER NOT NULL, requested REAL NOT NULL)""")

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
                installation: int, head: str | None = None) -> dict[str, Any]:
        if not isinstance(delivery, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", delivery):
            raise AppError("invalid delivery identity")
        webhook = delivery.startswith("webhook:")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM jobs WHERE delivery = ?", (delivery,)).fetchone()
            if prior is not None and (
                    prior["repository"], prior["number"], prior["installation"]) != (
                        repository, number, installation):
                raise AppError("delivery identity conflicts with prior target", 409)
            # A head that was superseded before any review is admitted again when it
            # returns, for example after a force-push back to it.
            revive = (prior is not None and webhook and prior["status"] == "superseded"
                      and head is not None and db.execute(
                          "SELECT 1 FROM jobs WHERE repository = ? AND number = ? "
                          "AND reviewed = ?", (repository, number, head)).fetchone() is None)
            if prior is not None and not revive:
                return self._job(prior)
            identity = prior["id"] if prior is not None else uuid.uuid4().hex
            if delivery.startswith(REPLY_DELIVERY):
                # A queued job of this pull request will capture the reply. A reply never
                # displaces a queued push: its own job may end without reviewing anything.
                covering = db.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' AND repository = ? "
                    "AND number = ? AND delivery LIKE 'webhook:%' ORDER BY created, rowid "
                    "LIMIT 1", (repository, number)).fetchone()
                if covering is not None:
                    return self._job(covering)
            elif webhook:
                # One queued push per pull request. The review that runs captures the
                # head current then, so older queued pushes announce heads that are gone,
                # and one author cannot fill the queue for every other pull request.
                db.execute(
                    "UPDATE jobs SET status = 'superseded', result = ? WHERE status = 'queued' "
                    "AND repository = ? AND number = ? AND delivery LIKE 'webhook:%'",
                    (json.dumps({"superseded_by": identity}), repository, number))
            count = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= self.max_pending:
                # Leaving the transaction by exception also undoes the superseding above.
                raise AppError("review queue is full; retry later", 503)
            if prior is not None:
                db.execute("UPDATE jobs SET status = 'queued', result = NULL, created = ? "
                           "WHERE id = ?", (time.time(), identity))
            else:
                db.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, ?, NULL, NULL)",
                    (identity, delivery, repository, number, installation, time.time(), head))
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (identity,)).fetchone()
            return self._job(row)

    def get(self, identity: str) -> dict[str, Any]:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (identity,)).fetchone()
        if row is None:
            raise AppError("job not found", 404)
        return self._job(row)

    def claim(self) -> dict[str, Any] | None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created, rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status = 'running', claimed = ? WHERE id = ?",
                       (time.time(), row["id"]))
            return self._job(db.execute("SELECT * FROM jobs WHERE id = ?",
                                        (row["id"],)).fetchone())

    def running(self) -> list[dict[str, Any]]:
        """Jobs holding the lease. With one worker, any it is not executing are orphans."""
        with closing(self._connect()) as db, db:
            return [self._job(row) for row in db.execute(
                "SELECT * FROM jobs WHERE status = 'running' ORDER BY created, rowid")]

    def finish(self, identity: str, status: str, result: dict[str, Any],
               reviewed: str | None = None) -> None:
        """Record a terminal state and the head whose review was or may have been sent."""
        if status not in {"posted", "unchanged", "failed", "interrupted", "superseded"}:
            raise AppError("invalid terminal job status")
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE jobs SET status = ?, result = ?, reviewed = ? "
                                "WHERE id = ? AND status = 'running'",
                                (status, json.dumps(result, allow_nan=False), reviewed, identity))
            if cursor.rowcount != 1:
                raise AppError("job does not own a running lease", 409)

    def release(self, identity: str) -> None:
        """Return a claimed job that never started its review to the queue."""
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE jobs SET status = 'queued', claimed = NULL "
                                "WHERE id = ? AND status = 'running'", (identity,))
            if cursor.rowcount != 1:
                raise AppError("job does not own a running lease", 409)

    def reviewed(self, repository: str, number: int, head: str) -> str | None:
        """Name a job that delivered, or may have delivered, a review of exactly this head."""
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT id FROM jobs WHERE repository = ? AND number = ? "
                             "AND reviewed = ? ORDER BY created LIMIT 1",
                             (repository, number, head)).fetchone()
        return None if row is None else str(row["id"])

    def redelivery_due(self, guid: str, limit: int = 3) -> bool:
        """Whether a failed GitHub delivery still has redelivery requests left."""
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT attempts FROM redeliveries WHERE guid = ?",
                             (guid,)).fetchone()
        return row is None or int(row["attempts"]) < limit

    def redelivery_requested(self, guid: str, now: float | None = None) -> None:
        """Count one accepted redelivery request and drop budgets past the window."""
        current = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM redeliveries WHERE requested < ?", (current - 2 * 86400,))
            db.execute("INSERT INTO redeliveries VALUES (?, 1, ?) ON CONFLICT (guid) "
                       "DO UPDATE SET attempts = attempts + 1, requested = excluded.requested",
                       (guid, current))


def admit_webhook(settings: AppSettings, store: JobStore, event: str, delivery: str,
                  signature: str, body: bytes) -> dict[str, Any]:
    """Authenticate exact bytes before parsing an installed-repository PR event."""
    expected = "sha256=" + hmac.new(settings.webhook_secret.encode(), body,
                                    hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise AppError("invalid webhook signature", 401)
    if len(body) > MAX_BODY:
        raise AppError("request body is too large", 413)
    if not delivery:
        raise AppError("delivery identity is required")
    try:
        document = json.loads(body)
        if not isinstance(document, dict):
            raise ValueError
        reply = event == REPLY_EVENT and document.get("action") in REPLY_ACTIONS
        if not reply and (event != "pull_request"
                          or document.get("action") not in REVIEWED_ACTIONS):
            return {"status": "ignored"}
        repository = document["repository"]["full_name"]
        # A review comment event carries its pull request number only in the nested object.
        number = document["pull_request"]["number"] if reply else document["number"]
        installation = document["installation"]["id"]
        settings.authorize(repository, number, installation)
        pr = document["pull_request"]
        if (type(pr["number"]) is not int or pr["number"] != number
                or pr["base"]["repo"]["full_name"] != repository):
            raise ValueError
        head = pr["head"]["sha"]
        if not isinstance(head, str) or _SHA.fullmatch(head) is None:
            raise ValueError
        if pr["state"] != "open":
            return {"status": "ignored"}
        if reply:
            return _admit_reply(store, document, repository, number, installation, head)
        # The delivery header is not covered by the signature. Key admission on signed
        # content, so a replayed body under a fresh delivery ID is the same job and each
        # announced head of a pull request is admitted once.
        signed = hashlib.sha256(f"{repository}\0{number}\0{head}".encode()).hexdigest()
        return store.enqueue("webhook:" + signed, repository, number, installation, head)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, AppError):
            raise
        raise AppError("invalid pull request event") from None


def _admit_reply(store: JobStore, document: dict[str, Any], repository: str, number: int,
                 installation: int, head: str) -> dict[str, Any]:
    """Queue a reassessment for a person's reply on an inline review thread.

    Admission cannot know the repository's trust policy or whose thread this is. It
    drops what can never contest: new threads, bots and Scrutare's own responses. The
    engine applies the policy to what it captures.
    """
    comment = document["comment"]
    identity, parent, body = comment["id"], comment.get("in_reply_to_id"), comment["body"]
    accounts = (comment["user"], document["sender"])
    if (type(identity) is not int or identity <= 0 or not isinstance(body, str)
            or not (parent is None or (type(parent) is int and parent > 0))
            or any(not isinstance(account["login"], str) or not isinstance(account["type"], str)
                   for account in accounts)):
        raise ValueError
    if parent is None or any(account["type"] != "User" or account["login"].endswith("[bot]")
                             for account in accounts):
        return {"status": "ignored"}
    # Keyed on signed content: one job per text of a comment. A redelivery or an edit
    # that leaves the text alone is the same job; a changed text is fresh evidence once.
    text = hashlib.sha256(body.encode()).hexdigest()
    signed = hashlib.sha256(f"{repository}\0{number}\0{identity}\0{text}".encode()).hexdigest()
    return store.enqueue(REPLY_DELIVERY + signed, repository, number, installation, head)
