"""Real worker execution adapters for GlacierEQ Swarm.

Adapters are configured by the service operator, not by task submitters.  The
subprocess adapter executes a fixed argv without a shell and passes task payload
as JSON on stdin.  This prevents a remote task from silently replacing the
worker command while still allowing arbitrary purpose-specific worker programs
behind an explicit capability grant.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _argv(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not value:
        raise ValueError("adapter_argv_invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError("adapter_argv_invalid")
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class WorkerExecutionReceipt:
    worker_id: str
    adapter_kind: str
    status: str
    returncode: int
    input_digest: str
    output_digest: str | None
    stdout_sha256: str
    stderr_sha256: str
    stdout_tail: str
    stderr_tail: str
    result: Any | None
    receipt_digest: str

    def as_dict(self, *, include_result: bool = True) -> dict[str, Any]:
        value = {
            "worker_id": self.worker_id,
            "adapter_kind": self.adapter_kind,
            "status": self.status,
            "returncode": self.returncode,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "receipt_digest": self.receipt_digest,
        }
        if include_result:
            value["result"] = self.result
        return value


class WorkerAdapter(Protocol):
    worker_id: str

    def execute(self, payload: Any) -> WorkerExecutionReceipt:
        ...


class SubprocessWorkerAdapter:
    """Execute one fixed operator-configured worker command per task."""

    kind = "subprocess-json-stdin"

    def __init__(
        self,
        *,
        worker_id: str,
        argv: Sequence[str],
        cwd: str | Path,
        timeout_seconds: int = 1800,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("adapter_worker_id_missing")
        self.worker_id = worker_id.strip()
        self.argv = _argv(argv)
        self.cwd = Path(cwd).resolve()
        if not self.cwd.is_dir():
            raise ValueError("adapter_cwd_missing")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("adapter_timeout_invalid")
        if not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
            raise ValueError("adapter_output_limit_invalid")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.env = dict(env or {})
        if not all(isinstance(k, str) and k and isinstance(v, str) for k, v in self.env.items()):
            raise ValueError("adapter_env_invalid")

    def execute(self, payload: Any) -> WorkerExecutionReceipt:
        input_bytes = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        input_digest = _sha(input_bytes)
        merged_env = os.environ.copy()
        merged_env.update(self.env)
        try:
            proc = subprocess.run(
                list(self.argv),
                cwd=str(self.cwd),
                env=merged_env,
                input=input_bytes,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
            returncode = int(proc.returncode)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            if isinstance(stdout, str):
                stdout = stdout.encode("utf-8", errors="replace")
            if isinstance(stderr, str):
                stderr = stderr.encode("utf-8", errors="replace")
            returncode = 124
        if len(stdout) > self.max_output_bytes or len(stderr) > self.max_output_bytes:
            returncode = 125
            stderr = (stderr[: self.max_output_bytes] + b"\nworker_output_limit_exceeded\n")[: self.max_output_bytes]
            stdout = stdout[: self.max_output_bytes]

        result: Any | None = None
        output_digest: str | None = None
        status = "FAIL"
        if returncode == 0:
            try:
                text = stdout.decode("utf-8")
                result = json.loads(text)
                output_digest = _digest(result)
                status = "PASS"
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                returncode = 126
                stderr = (stderr + b"\nworker_stdout_not_single_json_value\n")[: self.max_output_bytes]

        core = {
            "worker_id": self.worker_id,
            "adapter_kind": self.kind,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "status": status,
            "returncode": returncode,
            "input_digest": input_digest,
            "output_digest": output_digest,
            "stdout_sha256": _sha(stdout),
            "stderr_sha256": _sha(stderr),
        }
        return WorkerExecutionReceipt(
            worker_id=self.worker_id,
            adapter_kind=self.kind,
            status=status,
            returncode=returncode,
            input_digest=input_digest,
            output_digest=output_digest,
            stdout_sha256=core["stdout_sha256"],
            stderr_sha256=core["stderr_sha256"],
            stdout_tail=stdout.decode("utf-8", errors="replace")[-4000:],
            stderr_tail=stderr.decode("utf-8", errors="replace")[-4000:],
            result=result,
            receipt_digest=_digest(core),
        )
