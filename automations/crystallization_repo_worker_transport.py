#!/usr/bin/env python3
"""Transport adapter for `crystallization_repo_worker.py`.

The repository worker reports repository truth with statuses such as
CRYSTALLIZED, INCOMPLETE, or BROKEN.  Those are *work-unit outcomes*, not
process-transport failures.  Swarm retries only when the worker protocol itself
fails (invalid/missing JSON, exception, timeout at the outer adapter, etc.).

This wrapper therefore:
1. forwards the task JSON to the repository worker;
2. validates exactly one JSON result object;
3. accepts truthful repository outcome statuses as a successful transport;
4. exits non-zero only when the worker did not produce a valid work-unit result.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

VALID_WORK_UNIT_STATUSES = {
    "UNKNOWN",
    "DISCOVERED",
    "UNDERSTOOD",
    "BROKEN",
    "INCOMPLETE",
    "FUNCTIONAL",
    "COMPLETE",
    "DEPLOYED",
    "CRYSTALLIZED",
    "CANONICALIZED_SUCCESSOR",
    "INTENTIONALLY_ARCHIVED",
}


def _worker() -> Path:
    return Path(__file__).resolve().with_name("crystallization_repo_worker.py")


def _parse_single_object(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("repository_worker_stdout_empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("repository_worker_stdout_not_single_json") from exc
    if not isinstance(value, dict):
        raise ValueError("repository_worker_result_not_object")
    status = value.get("status")
    if status not in VALID_WORK_UNIT_STATUSES:
        raise ValueError(f"repository_worker_status_invalid:{status}")
    repository = value.get("repository")
    if not isinstance(repository, str) or "/" not in repository:
        raise ValueError("repository_worker_repository_missing")
    return value


def main() -> int:
    task_bytes = sys.stdin.buffer.read()
    if not task_bytes.strip():
        print(
            json.dumps({"status": "ERROR", "reason": "task_payload_empty"}),
            file=sys.stderr,
        )
        return 3
    try:
        # Validate transport input before forwarding it. The child performs the
        # domain-level schema validation.
        incoming = json.loads(task_bytes.decode("utf-8"))
        if not isinstance(incoming, dict):
            raise ValueError("task_payload_must_be_object")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}), file=sys.stderr)
        return 3

    proc = subprocess.run(
        [sys.executable, str(_worker())],
        input=task_bytes,
        capture_output=True,
        check=False,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    try:
        result = _parse_single_object(stdout)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "reason": str(exc),
                    "child_returncode": proc.returncode,
                    "child_stdout_tail": stdout[-2000:],
                    "child_stderr_tail": stderr[-2000:],
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4

    # Preserve child transport diagnostics without changing repository truth.
    result = {
        **result,
        "worker_transport": {
            "child_returncode": proc.returncode,
            "child_stderr_tail": stderr[-2000:],
            "protocol": "glaciereq.crystallization-work-unit.v1",
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
