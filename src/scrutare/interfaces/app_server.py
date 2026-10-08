"""GitHub App HTTP front door; TLS and request-rate limits belong to the ingress."""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from scrutare.interfaces.app import MAX_BODY, AppError, AppSettings, JobStore, admit_webhook
from scrutare.interfaces.app_worker import run_job


def load_settings() -> AppSettings:
    try:
        settings = AppSettings(
            int(os.environ["SCRUTARE_APP_ID"]),
            Path(os.environ["SCRUTARE_APP_PRIVATE_KEY"]),
            os.environ["SCRUTARE_APP_WEBHOOK_SECRET"], os.environ["SCRUTARE_APP_API_TOKEN"],
            json.loads(os.environ["SCRUTARE_APP_INSTALLATIONS"]),
            Path(os.environ.get("SCRUTARE_APP_DATA_DIR", "/data")),
            tuple(json.loads(os.environ["SCRUTARE_APP_MODEL_ORIGINS"])),
            int(os.environ.get("SCRUTARE_APP_MAX_PENDING", "100")),
        )
        if not settings.private_key.is_file():
            raise AppError("App private key file is unavailable")
        return settings
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AppError("invalid App environment; see docs/app.md") from None


def make_server(settings: AppSettings, store: JobStore, host: str, port: int) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        # No raw path, headers, credential, or payload access logs.
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def respond(self, status: int, document: dict[str, Any]) -> None:
            body = json.dumps(document, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authenticate(self) -> None:
            expected = ("Bearer " + settings.api_token).encode()
            if not hmac.compare_digest(expected, self.headers.get("Authorization", "").encode()):
                raise AppError("invalid API credential", 401)

        def body(self) -> bytes:
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise AppError("chunked requests are unsupported")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1:
                    raise AppError("one Content-Length header is required")
                size = int(lengths[0])
                if size < 0 or size > MAX_BODY:
                    raise AppError("request body is too large", 413)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise AppError("incomplete request body")
                return raw
            except (OSError, ValueError):
                raise AppError("invalid request body") from None

        def do_GET(self) -> None:
            try:
                if self.path == "/healthz":
                    self.respond(200, {"status": "ok"})
                elif self.path.startswith("/api/jobs/"):
                    self.authenticate()
                    self.respond(200, store.get(self.path.removeprefix("/api/jobs/")))
                else:
                    raise AppError("route not found", 404)
            except AppError as error:
                self.respond(error.status, {"error": str(error)})

        def do_POST(self) -> None:
            try:
                if self.path == "/webhook":
                    result = admit_webhook(settings, store,
                                           self.headers.get("X-GitHub-Event", ""),
                                           self.headers.get("X-GitHub-Delivery", ""),
                                           self.headers.get("X-Hub-Signature-256", ""), self.body())
                elif self.path == "/api/reviews":
                    self.authenticate()
                    document = json.loads(self.body())
                    if not isinstance(document, dict) or document.keys() - {
                            "repository", "number", "installation", "request_id"}:
                        raise AppError("invalid review request")
                    settings.authorize(document.get("repository"), document.get("number"),
                                       document.get("installation"))
                    request_id = document.get("request_id")
                    if not isinstance(request_id, str):
                        raise AppError("request_id is required for idempotency")
                    result = store.enqueue("api:" + request_id, document["repository"],
                                           document["number"], document["installation"])
                else:
                    raise AppError("route not found", 404)
                self.respond(202, result)
            except AppError as error:
                self.respond(error.status, {"error": str(error)})
            except (ValueError, TypeError, KeyError):
                self.respond(400, {"error": "invalid review request"})
            except (OSError, sqlite3.Error):
                self.respond(503, {"error": "App storage is unavailable"})

    return HTTPServer((host, port), Handler)


class WorkerProcess:
    """Forward shutdown to the CLI so its owned nare groups can clean up."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[bytes] | None = None
        self.stopping = False

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        kwargs.pop("capture_output")
        with self.lock:
            if self.stopping:
                raise OSError("worker is stopping")
            self.process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            start_new_session=True, **kwargs)
            process = self.process
        try:
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        finally:
            with self.lock:
                self.process = None

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
            process = self.process
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
        if process is not None:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def main() -> int:
    """Run one serialized worker alongside authenticated HTTP intake."""
    try:
        os.umask(0o077)
        settings = load_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (settings.data_dir / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            store = JobStore(settings.data_dir / "jobs.sqlite", settings.max_pending)
            store.recover_interrupted()
            stopping = threading.Event()
            runner = WorkerProcess()
            def worker() -> None:
                while not stopping.is_set():
                    job = store.claim()
                    if job is None:
                        stopping.wait(1)
                    else:
                        run_job(settings, store, job, runner=runner)
            thread = threading.Thread(target=worker, daemon=True)
            server = make_server(settings, store, os.environ.get("SCRUTARE_APP_HOST", "127.0.0.1"),
                                 int(os.environ.get("SCRUTARE_APP_PORT", "8080")))
            def terminate(signum: int, frame: Any) -> None:
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, terminate)
            thread.start()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stopping.set()
                runner.stop()
                thread.join(35)
                server.server_close()
        return 0
    except (AppError, OSError, ValueError):
        print("scrutare-app: startup failed; check App settings, port and exclusive data lock",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
