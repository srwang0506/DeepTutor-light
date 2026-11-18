"""Commit acknowledged API changes, completed turns and background work to Neon."""

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import signal
import socketserver
import subprocess
import tempfile
import threading
import time

from deployment.hosted.neon_store import CloudStore, FencedWriter

log = logging.getLogger(__name__)
SOCKET_ENV = "DEEPTUTOR_CLOUD_COMMIT_SOCKET"
BOUNDARIES = {"session", "session_meta", "result", "wait_for_input", "done", "command_ack"}


def read_settings():
    path = os.environ.pop("DEEPTUTOR_NEON_CONFIG_FILE", "")
    raw = os.environ.pop("DEEPTUTOR_NEON_CONFIG", "")
    if path and raw:
        raise ValueError("Set a single Neon workspace configuration")
    if path:
        raw = Path(path).read_text()
    return json.loads(raw) if raw else None


class CommitRequest(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        mode = self.rfile.readline(16)
        if mode not in {b"write\n", b"read\n"}:
            return
        try:
            self.server.store.save(
                self.server.home, acknowledge=mode == b"write\n", observe=mode == b"read\n"
            )
            reply = b"ok\n"
        except Exception as exc:
            # Database exceptions can include connection details.
            log.error("Workspace commit unavailable (%s)", type(exc).__name__)
            reply = b"unavailable\n"
        with suppress(OSError):
            self.wfile.write(reply)


class CommitServer(socketserver.UnixStreamServer):
    request_queue_size = 128

    def __init__(self, path, store, home):
        super().__init__(path, CommitRequest)
        self.store, self.home = store, home
        self.next_scan = time.monotonic() + 15
        self.next_prune = time.monotonic() + 90

    def service_actions(self):
        if time.monotonic() < self.next_scan:
            return
        try:
            self.store.save(self.home)
            if time.monotonic() >= self.next_prune:
                self.store.prune_files()
                self.next_prune = time.monotonic() + 3600
        except FencedWriter:
            self.next_prune = float("inf")
        except Exception as exc:
            log.error("Workspace background commit unavailable (%s)", type(exc).__name__)
        self.next_scan = time.monotonic() + 15


def run(command, home, settings, initialize):
    store = CloudStore(settings)
    home.mkdir(parents=True, exist_ok=True)
    store.claim(home)
    initialize()
    store.save(home, acknowledge=True)
    with tempfile.TemporaryDirectory(prefix="dt-cloud-") as private:
        socket_path = str(Path(private) / "commit.sock")
        with CommitServer(socket_path, store, home) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            process = None
            stopping = False

            def stop(signum, _frame):
                nonlocal stopping
                stopping = True
                if process is not None and process.poll() is None:
                    process.send_signal(signum)

            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, stop)
            try:
                process = subprocess.Popen(command, env={**os.environ, SOCKET_ENV: socket_path})
                if stopping:
                    process.terminate()
                code = process.wait()
            finally:
                server.shutdown()
                worker.join()
            if not store.fenced:
                try:
                    store.save(home, acknowledge=True)
                except FencedWriter:
                    pass
            raise SystemExit(code)


async def commit(socket_path, mode):
    async with asyncio.timeout(120):
        reader, writer = await asyncio.open_unix_connection(socket_path)
        try:
            writer.write(mode + b"\n")
            await writer.drain()
            if await reader.readline() != b"ok\n":
                raise OSError("Workspace storage unavailable")
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


class CommitMiddleware:
    """Withhold success until the revision is durable. Token deltas can stream."""

    def __init__(self, app, socket_path):
        self.app, self.socket_path = app, socket_path

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in {"http", "websocket"} or scope.get("path") in {
            "/_hosted/ready",
            "/health/ready",
            "/health/live",
            "/api/auth/profile",
        }:
            return await self.app(scope, receive, send)
        mode = b"read" if scope.get("method") in {"GET", "HEAD", "OPTIONS"} else b"write"
        failed = False
        streaming = False

        async def before_send(message):
            nonlocal failed, streaming
            if failed:
                return
            event = message["type"]
            check = False
            if event == "http.response.start":
                check = 200 <= message["status"] < 400
            elif event == "http.response.body":
                check = streaming and not message.get("more_body")
                streaming = bool(message.get("more_body"))
            elif event == "websocket.send" and message.get("text"):
                data = json.loads(message["text"])
                check = data.get("type") in BOUNDARIES
            if check:
                try:
                    await commit(self.socket_path, mode)
                except (OSError, TimeoutError):
                    failed = True
                    if kind == "websocket":
                        await send({"type": "websocket.close", "code": 1013})
                    elif event == "http.response.start":
                        await send(
                            {
                                "type": event,
                                "status": 503,
                                "headers": [
                                    (b"content-type", b"application/json"),
                                    (b"retry-after", b"3"),
                                    (b"cache-control", b"no-store"),
                                ],
                            }
                        )
                        await send(
                            {
                                "type": "http.response.body",
                                "body": b'{"detail":"Workspace storage unavailable"}',
                            }
                        )
                    else:
                        raise
                    return
            await send(message)

        await self.app(scope, receive, before_send)
