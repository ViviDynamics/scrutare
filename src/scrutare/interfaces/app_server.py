"""GitHub App HTTP front door; TLS and request-rate limits belong to the ingress."""

from __future__ import annotations

import fcntl
import hmac
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from scrutare.interfaces.app import (
    MAX_BODY,
    AppError,
    AppSettings,
    JobStore,
    WorkerStopping,
    admit_webhook,
)
from scrutare.interfaces.app_worker import GitHubAppClient, reconcile, work_once

REQUEST_DEADLINE = 10.0
MAX_CONNECTIONS = 32


def load_settings() -> AppSettings:
    try:
        origins = json.loads(os.environ["SCRUTARE_APP_MODEL_ORIGINS"])
        if not isinstance(origins, list):
            raise ValueError
        settings = AppSettings(
            int(os.environ["SCRUTARE_APP_ID"]),
            Path(os.environ["SCRUTARE_APP_PRIVATE_KEY"]),
            os.environ["SCRUTARE_APP_WEBHOOK_SECRET"], os.environ["SCRUTARE_APP_API_TOKEN"],
            json.loads(os.environ["SCRUTARE_APP_INSTALLATIONS"]),
            Path(os.environ.get("SCRUTARE_APP_DATA_DIR", "/data")),
            tuple(origins),
            int(os.environ.get("SCRUTARE_APP_MAX_PENDING", "100")),
            int(os.environ.get("SCRUTARE_APP_JOB_TIMEOUT", "3300")),
        )
        if not settings.private_key.is_file():
            raise AppError("App private key file is unavailable")
        return settings
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AppError("invalid App environment; see docs/app.md") from None


class BoundedServer(ThreadingHTTPServer):
    """One thread per connection, a hard connection cap and a whole-request deadline."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], *,
                 deadline: float, connections: int) -> None:
        super().__init__(address, handler)
        self.deadline = deadline
        self.slots = threading.BoundedSemaphore(connections)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # A dropped or expired connection is not worth a traceback in the pod log.


class _Handler(BaseHTTPRequestHandler):
    server: BoundedServer

    # No raw path, headers, credential, or payload access logs.
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def setup(self) -> None:
        super().setup()
        # The socket timeout bounds one read. This bounds the request, however slowly
        # a client trickles its headers or body.
        self.expiry = threading.Timer(self.server.deadline, self.expire)
        self.expiry.daemon = True
        self.expiry.start()

    def expire(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self) -> None:
        self.expiry.cancel()
        try:
            super().finish()
        except OSError:
            pass

    def respond(self, status: int, document: dict[str, Any]) -> None:
        body = json.dumps(document, allow_nan=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # The client left or its deadline closed the connection.


def make_health_server(host: str, port: int,
                       healthy: Callable[[], bool] = lambda: True) -> BoundedServer:
    """Liveness on its own listener, so intake load cannot fail the pod's probes."""
    class Handler(_Handler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.respond(404, {"error": "route not found"})
            elif healthy():
                self.respond(200, {"status": "ok"})
            else:
                self.respond(503, {"error": "App worker is unavailable"})

    return BoundedServer((host, port), Handler, deadline=REQUEST_DEADLINE, connections=8)


def make_server(settings: AppSettings, store: JobStore, host: str, port: int, *,
                healthy: Callable[[], bool] = lambda: True,
                deadline: float = REQUEST_DEADLINE,
                connections: int = MAX_CONNECTIONS) -> BoundedServer:
    class Handler(_Handler):
        def authenticate(self) -> None:
            expected = ("Bearer " + settings.api_token).encode()
            if not hmac.compare_digest(expected, self.headers.get("Authorization", "").encode()):
                raise AppError("invalid API credential", 401)

        def body(self) -> bytes:
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise AppError("chunked requests are unsupported")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or re.fullmatch(r"[0-9]{1,12}", lengths[0]) is None:
                    raise AppError("one decimal Content-Length header is required")
                size = int(lengths[0])
                if size > MAX_BODY:
                    raise AppError("request body is too large", 413)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise AppError("incomplete request body")
                return raw
            except AppError:
                raise
            except (OSError, ValueError):
                raise AppError("invalid request body") from None

        def do_GET(self) -> None:
            try:
                if self.path == "/healthz":
                    if not healthy():
                        raise AppError("App worker is unavailable", 503)
                    self.respond(200, {"status": "ok"})
                elif self.path.startswith("/api/jobs/"):
                    self.authenticate()
                    self.respond(200, store.get(self.path.removeprefix("/api/jobs/")))
                else:
                    raise AppError("route not found", 404)
            except AppError as error:
                self.respond(error.status, {"error": str(error)})
            except (OSError, sqlite3.Error):
                self.respond(503, {"error": "App storage is unavailable"})
            except Exception:
                self.respond(500, {"error": "App request failed"})

        def do_POST(self) -> None:
            try:
                if self.path == "/webhook":
                    result = admit_webhook(settings, store,
                                           self.headers.get("X-GitHub-Event", ""),
                                           self.headers.get("X-GitHub-Delivery", ""),
                                           self.headers.get("X-Hub-Signature-256", ""), self.body())
                    # Job records are for authenticated performers only. A signed body
                    # can be replayed, and GitHub keeps this response in its delivery log.
                    result = {key: result[key] for key in ("status", "id") if key in result}
                elif self.path == "/api/reviews":
                    self.authenticate()
                    document = json.loads(self.body())
                    if not isinstance(document, dict) or document.keys() - {
                            "repository", "number", "installation", "request_id"}:
                        raise AppError("invalid review request")
                    settings.authorize(document.get("repository"), document.get("number"),
                                       document.get("installation"))
                    request_id = document.get("request_id")
                    if not isinstance(request_id, str) or not request_id:
                        raise AppError("request_id is required for idempotency")
                    result = store.enqueue("api:" + request_id, document["repository"],
                                           document["number"], document["installation"])
                else:
                    raise AppError("route not found", 404)
                self.respond(202, result)
            except AppError as error:
                self.respond(error.status, {"error": str(error)})
            except (ValueError, TypeError, KeyError, RecursionError):
                self.respond(400, {"error": "invalid review request"})
            except (OSError, sqlite3.Error):
                self.respond(503, {"error": "App storage is unavailable"})
            except Exception:
                self.respond(500, {"error": "App request failed"})

    return BoundedServer((host, port), Handler, deadline=deadline, connections=connections)


class WorkerProcess:
    """Bound each CLI run and forward shutdown so its owned nare groups can clean up."""

    def __init__(self, grace: float = 30) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[bytes] | None = None
        self.stopping = False
        self.grace = grace

    def __call__(self, argv: list[str], *, timeout: float | None = None,
                 **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        kwargs.pop("capture_output")
        with self.lock:
            if self.stopping:
                raise WorkerStopping
            self.process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            start_new_session=True, **kwargs)
            process = self.process
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Interrupt first so the CLI can stop nare and install its diagnostics.
                self._signal(process.pid, signal.SIGINT, group=True)
                try:
                    stdout, stderr = process.communicate(timeout=self.grace)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                    stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(argv, timeout or 0, stdout, stderr) from None
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        finally:
            with self.lock:
                self.process = None

    @staticmethod
    def _signal(pid: int, signum: int, *, group: bool = False) -> None:
        try:
            if group:
                os.killpg(pid, signum)
            else:
                os.kill(pid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _descendants(root: int) -> list[int]:
        """Every live descendant, read from /proc before any of them is orphaned."""
        children: dict[int, list[int]] = {}
        try:
            entries = [entry.name for entry in os.scandir("/proc") if entry.name.isdigit()]
        except OSError:
            return []
        for name in entries:
            try:
                fields = Path("/proc", name, "stat").read_text().rsplit(")", 1)[1].split()
                children.setdefault(int(fields[1]), []).append(int(name))
            except (OSError, ValueError, IndexError):
                continue
        found, pending = [], [root]
        while pending:
            for child in children.get(pending.pop(), []):
                found.append(child)
                pending.append(child)
        return found

    def _kill(self, process: subprocess.Popen[bytes]) -> None:
        # nare sessions lead their own process groups, so the CLI's group alone would
        # leave them running with the provider credential.
        descendants = self._descendants(process.pid)
        self._signal(process.pid, signal.SIGKILL, group=True)
        for pid in descendants:
            self._signal(pid, signal.SIGKILL)

    def stop(self) -> None:
        with self.lock:
            self.stopping = True
            process = self.process
            if process is not None and process.poll() is None:
                self._signal(process.pid, signal.SIGINT, group=True)
        if process is not None:
            try:
                process.wait(timeout=self.grace)
            except subprocess.TimeoutExpired:
                self._kill(process)
                process.wait()


def report(message: str) -> None:
    """One credential-free operational line; never request, job or remote content."""
    print(f"scrutare-app: {message}", file=sys.stderr, flush=True)


def main() -> int:
    """Run one serialized worker alongside authenticated HTTP intake."""
    try:
        os.umask(0o077)
        settings = load_settings()
        host = os.environ.get("SCRUTARE_APP_HOST", "127.0.0.1")
        interval = int(os.environ.get("SCRUTARE_APP_RECONCILE_SECONDS", "600"))
        if interval < 0:
            raise ValueError
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (settings.data_dir / "worker.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            store = JobStore(settings.data_dir / "jobs.sqlite", settings.max_pending)
            stopping = threading.Event()
            runner = WorkerProcess()
            def worker() -> None:
                while not stopping.is_set():
                    try:
                        if not work_once(settings, store, runner=runner):
                            stopping.wait(1)
                    except Exception as error:
                        # Keep the only worker alive. The lease it held is recovered, with
                        # delivery read from its run, on the next pass.
                        report(f"worker step failed ({type(error).__name__}); retrying")
                        stopping.wait(5)
            def reconciler() -> None:
                # GitHub never redelivers on its own; ask for what this service missed
                # while it was down, full or unreachable.
                client = GitHubAppClient(settings)
                while not stopping.is_set():
                    try:
                        requested = reconcile(store, client)
                        if requested:
                            report(f"requested redelivery of {requested} missed webhooks")
                    except (AppError, OSError, sqlite3.Error) as error:
                        detail = str(error) if isinstance(error, AppError) else "storage error"
                        report(f"webhook redelivery check failed: {detail}")
                    stopping.wait(interval)
            thread = threading.Thread(target=worker, daemon=True)
            server = make_server(settings, store, host,
                                 int(os.environ.get("SCRUTARE_APP_PORT", "8080")),
                                 healthy=thread.is_alive)
            health = make_health_server(
                host, int(os.environ.get("SCRUTARE_APP_HEALTH_PORT", "8081")), thread.is_alive)
            def terminate(signum: int, frame: Any) -> None:
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, terminate)
            report(f"listening on port {server.server_port}, health on port {health.server_port}")
            thread.start()
            threading.Thread(target=health.serve_forever, daemon=True).start()
            if interval:
                threading.Thread(target=reconciler, daemon=True).start()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stopping.set()
                runner.stop()
                thread.join(runner.grace + 5)
                server.server_close()
                health.shutdown()
                health.server_close()
        return 0
    except (AppError, OSError, ValueError):
        report("startup failed; check App settings, ports and exclusive data lock")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
