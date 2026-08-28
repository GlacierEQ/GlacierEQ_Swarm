"""Authenticated HTTP service for the persistent GlacierEQ Swarm runtime.

The API intentionally exposes orchestration, not arbitrary shell execution.
Workers and their fixed executable adapters are operator-configured at service
startup. API clients may submit JSON task payloads and required capabilities;
they cannot replace the worker command.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from swarm_runtime import SwarmRuntime

MAX_REQUEST_BYTES = 2 * 1024 * 1024


class SwarmHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address, handler, *, runtime: SwarmRuntime, bearer_token: str
    ) -> None:
        super().__init__(address, handler)
        self.runtime = runtime
        self.bearer_token = bearer_token


class Handler(BaseHTTPRequestHandler):
    server: SwarmHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("swarm-api " + (format % args) + "\n")

    def _json(self, status: int, value: Any) -> None:
        data = (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        expected = "Bearer " + self.server.bearer_token
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, expected)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("content_length_required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("content_length_invalid") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request_body_size_invalid")
        data = self.rfile.read(length)
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request_body_must_be_object")
        return payload

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._json(HTTPStatus.OK, {"status": "HEALTHY"})
                return
            if path == "/ready":
                readiness = self.server.runtime.readiness()
                self._json(
                    HTTPStatus.OK
                    if readiness["ready"]
                    else HTTPStatus.SERVICE_UNAVAILABLE,
                    readiness,
                )
                return
            if not self._require_auth():
                return
            if path == "/v1/status":
                self._json(HTTPStatus.OK, self.server.runtime.status())
                return
            if path.startswith("/v1/tasks/"):
                task_id = path.removeprefix("/v1/tasks/")
                if not task_id or "/" in task_id:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                self._json(HTTPStatus.OK, self.server.runtime.task(task_id))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except KeyError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{type(exc).__name__}:{exc}"},
            )

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._require_auth():
            return
        try:
            body = self._body()
            if path == "/v1/tasks":
                task_id = body.get("task_id")
                required = body.get("required_capabilities")
                if not isinstance(required, list):
                    raise ValueError("required_capabilities_must_be_list")
                result = self.server.runtime.submit_task(
                    task_id,
                    required,
                    body.get("payload"),
                    priority=int(body.get("priority", 0)),
                    max_attempts=int(body.get("max_attempts", 3)),
                )
                self._json(HTTPStatus.CREATED, result)
                return
            if path == "/v1/run":
                limit = body.get("limit")
                result = self.server.runtime.run_once(
                    limit=None if limit is None else int(limit)
                )
                self._json(HTTPStatus.OK, result)
                return
            if path == "/v1/run-until-idle":
                result = self.server.runtime.run_until_idle(
                    max_cycles=int(body.get("max_cycles", 1000))
                )
                self._json(HTTPStatus.OK, result)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except KeyError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"{type(exc).__name__}:{exc}"},
            )


def _load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("swarm_config_must_be_object")
    return payload


def serve(
    config_path: str | Path, *, host: str | None = None, port: int | None = None
) -> None:
    path = Path(config_path).resolve()
    config = _load_config(path)
    token_env = config.get("bearer_token_env", "GLACIEREQ_SWARM_TOKEN")
    if not isinstance(token_env, str) or not token_env:
        raise ValueError("bearer_token_env_invalid")
    token = os.environ.get(token_env)
    if not token or len(token) < 24:
        raise RuntimeError(f"required bearer token missing/too short in {token_env}")
    runtime = SwarmRuntime.from_config(config, config_base=path.parent)
    bind_host = host if host is not None else str(config.get("host", "127.0.0.1"))
    bind_port = port if port is not None else int(config.get("port", 8787))
    server = SwarmHTTPServer(
        (bind_host, bind_port), Handler, runtime=runtime, bearer_token=token
    )
    shutting_down = threading.Event()

    def shutdown(_signum, _frame) -> None:
        if shutting_down.is_set():
            return
        shutting_down.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the persistent GlacierEQ Swarm API"
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    try:
        serve(args.config, host=args.host, port=args.port)
        return 0
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        sys.stderr.write(
            json.dumps({"status": "ERROR", "reason": str(exc)}, sort_keys=True) + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
