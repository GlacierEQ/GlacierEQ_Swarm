#!/usr/bin/env python3
"""Network smoke for the containerized persistent Swarm service."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def request(base: str, path: str, *, token: str | None = None, method: str = "GET", body=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def await_ready(base: str, attempts: int = 40) -> dict:
    last = None
    for _ in range(attempts):
        try:
            _, health = request(base, "/health")
            status, ready = request(base, "/ready")
            if health.get("status") == "HEALTHY" and status == 200 and ready.get("ready") is True:
                return ready
            last = ready
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last = {"error": str(exc)}
        time.sleep(0.25)
    raise RuntimeError(f"service_not_ready:{last}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--mode", choices=["submit", "verify-existing"], default="submit")
    parser.add_argument("--task-id", default="container-smoke-task")
    args = parser.parse_args()
    token = os.environ.get("GLACIEREQ_SWARM_TOKEN")
    if not token:
        raise RuntimeError("GLACIEREQ_SWARM_TOKEN missing")
    ready = await_ready(args.base)

    try:
        request(args.base, "/v1/status")
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
    else:
        raise RuntimeError("unauthenticated status endpoint was accepted")

    if args.mode == "submit":
        status, submitted = request(
            args.base,
            "/v1/tasks",
            token=token,
            method="POST",
            body={
                "task_id": args.task_id,
                "required_capabilities": ["echo", "json"],
                "payload": {"purpose": "prove container execution and durable restart"},
                "priority": 10,
                "max_attempts": 2
            },
        )
        if status != 201 or submitted.get("status") != "QUEUED":
            raise RuntimeError(f"submit_failed:{submitted}")
        _, run = request(args.base, "/v1/run-until-idle", token=token, method="POST", body={"max_cycles": 10})
        if run.get("status") != "IDLE":
            raise RuntimeError(f"execution_not_idle:{run}")

    _, task = request(args.base, f"/v1/tasks/{args.task_id}", token=token)
    if task.get("task", {}).get("status") != "SUCCEEDED":
        raise RuntimeError(f"task_not_succeeded:{task}")
    result = task.get("result", {}).get("result", {})
    if result.get("worker") != "echo" or result.get("payload", {}).get("purpose") != "prove container execution and durable restart":
        raise RuntimeError(f"unexpected_worker_result:{result}")
    _, status_doc = request(args.base, "/v1/status", token=token)
    if status_doc.get("store_integrity", {}).get("status") != "PASS":
        raise RuntimeError(f"store_integrity_failed:{status_doc}")
    print(json.dumps({
        "status": "PASS",
        "mode": args.mode,
        "task_id": args.task_id,
        "ready": ready,
        "store_revision": status_doc.get("store_revision"),
        "result_digest": task.get("result", {}).get("result_digest")
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
