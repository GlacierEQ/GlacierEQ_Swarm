"""Persistent execution runtime joining scheduler, adapters, and state store."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from mechanism import WorkerStatus
from swarm_store import SwarmStateStore
from worker_adapters import SubprocessWorkerAdapter, WorkerAdapter


@dataclass(frozen=True)
class ConfiguredWorker:
    worker_id: str
    capabilities: tuple[str, ...]
    max_concurrency: int
    adapter: WorkerAdapter


class SwarmRuntime:
    """Thread-safe runtime that executes scheduler assignments on real adapters.

    Scheduler transitions remain deterministic and serialized under a lock.
    Actual worker execution happens concurrently outside that lock. Every
    transition is persisted so restart resumes the queue rather than inventing a
    fresh swarm.
    """

    def __init__(
        self, store: SwarmStateStore, workers: Iterable[ConfiguredWorker]
    ) -> None:
        self.store = store
        self._lock = threading.RLock()
        self.orchestrator, self.revision = store.load()
        self.adapters: dict[str, WorkerAdapter] = {}
        self._configure_workers(list(workers))

    @classmethod
    def from_config(
        cls, config: Mapping[str, Any], *, config_base: str | Path = "."
    ) -> "SwarmRuntime":
        if not isinstance(config, Mapping):
            raise ValueError("swarm_config_invalid")
        base = Path(config_base).resolve()
        store_path = config.get("store_path")
        if not isinstance(store_path, str) or not store_path.strip():
            raise ValueError("swarm_store_path_missing")
        store_file = Path(store_path)
        if not store_file.is_absolute():
            store_file = base / store_file
        rows = config.get("workers")
        if not isinstance(rows, list) or not rows:
            raise ValueError("swarm_workers_missing")
        workers: list[ConfiguredWorker] = []
        seen: set[str] = set()
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("swarm_worker_config_invalid")
            worker_id = raw.get("worker_id")
            capabilities = raw.get("capabilities")
            max_concurrency = raw.get("max_concurrency", 1)
            adapter_raw = raw.get("adapter")
            if not isinstance(worker_id, str) or not worker_id.strip():
                raise ValueError("swarm_worker_id_missing")
            if worker_id in seen:
                raise ValueError("swarm_worker_duplicate")
            seen.add(worker_id)
            if (
                not isinstance(capabilities, list)
                or not capabilities
                or not all(
                    isinstance(item, str) and item.strip() for item in capabilities
                )
            ):
                raise ValueError("swarm_worker_capabilities_invalid")
            if (
                not isinstance(max_concurrency, int)
                or isinstance(max_concurrency, bool)
                or max_concurrency <= 0
            ):
                raise ValueError("swarm_worker_concurrency_invalid")
            if (
                not isinstance(adapter_raw, Mapping)
                or adapter_raw.get("kind") != "subprocess"
            ):
                raise ValueError("swarm_worker_adapter_invalid")
            argv = adapter_raw.get("argv")
            cwd_raw = adapter_raw.get("cwd", ".")
            if not isinstance(cwd_raw, str) or not cwd_raw:
                raise ValueError("swarm_worker_cwd_invalid")
            cwd = Path(cwd_raw)
            if not cwd.is_absolute():
                cwd = base / cwd
            adapter = SubprocessWorkerAdapter(
                worker_id=worker_id,
                argv=argv,
                cwd=cwd,
                timeout_seconds=int(adapter_raw.get("timeout_seconds", 1800)),
                env=adapter_raw.get("env") or {},
                max_output_bytes=int(
                    adapter_raw.get("max_output_bytes", 8 * 1024 * 1024)
                ),
            )
            workers.append(
                ConfiguredWorker(
                    worker_id=worker_id,
                    capabilities=tuple(capabilities),
                    max_concurrency=max_concurrency,
                    adapter=adapter,
                )
            )
        return cls(SwarmStateStore(store_file), workers)

    def _configure_workers(self, configured: list[ConfiguredWorker]) -> None:
        with self._lock:
            snapshot = self.orchestrator.snapshot()
            existing = {row["worker_id"]: row for row in snapshot["workers"]}
            configured_ids = {worker.worker_id for worker in configured}
            changed = False
            for worker in configured:
                current = existing.get(worker.worker_id)
                if current is None:
                    self.orchestrator.register_agent(
                        worker.worker_id,
                        worker.capabilities,
                        max_concurrency=worker.max_concurrency,
                        metadata={"adapter_kind": type(worker.adapter).__name__},
                    )
                    changed = True
                else:
                    if (
                        sorted(current["capabilities"]) != sorted(worker.capabilities)
                        or int(current["max_concurrency"]) != worker.max_concurrency
                    ):
                        raise ValueError(
                            f"configured_worker_contract_mismatch:{worker.worker_id}"
                        )
                    if current["status"] != WorkerStatus.ACTIVE.value:
                        self.orchestrator.set_worker_status(
                            worker.worker_id, WorkerStatus.ACTIVE
                        )
                        changed = True
                self.adapters[worker.worker_id] = worker.adapter

            for worker_id, current in existing.items():
                if (
                    worker_id not in configured_ids
                    and current["status"] != WorkerStatus.OFFLINE.value
                ):
                    self.orchestrator.set_worker_status(worker_id, WorkerStatus.OFFLINE)
                    changed = True
            if changed:
                self.revision = self.store.save(
                    self.orchestrator, expected_revision=self.revision
                )

    def submit_task(
        self,
        task_id: str,
        required_capabilities: Iterable[str],
        payload: Any,
        *,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        with self._lock:
            before = self.revision
            try:
                submission = self.orchestrator.submit_task(
                    task_id,
                    required_capabilities,
                    payload,
                    priority=priority,
                    max_attempts=max_attempts,
                )
                task = submission["task"]
                self.store.put_task_payload(task_id, payload, task["payload_digest"])
                self.revision = self.store.save(
                    self.orchestrator, expected_revision=before
                )
                return {**submission, "store_revision": self.revision}
            except Exception:
                self.orchestrator, self.revision = self.store.load()
                raise

    def _task_row(self, task_id: str) -> dict[str, Any]:
        for row in self.orchestrator.snapshot()["tasks"]:
            if row["task_id"] == task_id:
                return row
        raise KeyError(f"task_unknown:{task_id}")

    def run_once(self, *, limit: int | None = None) -> dict[str, Any]:
        with self._lock:
            dispatch = self.orchestrator.dispatch(limit=limit)
            assignments = list(dispatch["assignments"])
            if not assignments:
                return {
                    "status": "IDLE"
                    if dispatch["queued_remaining"] == 0
                    else "BLOCKED",
                    "assignments": [],
                    "queued_remaining": dispatch["queued_remaining"],
                    "store_revision": self.revision,
                }
            for assignment in assignments:
                self.orchestrator.start_task(
                    assignment["task_id"], assignment["worker_id"]
                )
            self.revision = self.store.save(
                self.orchestrator, expected_revision=self.revision
            )
            execution_inputs: list[tuple[dict[str, Any], Any, WorkerAdapter]] = []
            for assignment in assignments:
                row = self._task_row(assignment["task_id"])
                payload = self.store.get_task_payload(
                    assignment["task_id"], row["payload_digest"]
                )
                adapter = self.adapters.get(assignment["worker_id"])
                if adapter is None:
                    raise ValueError(
                        f"worker_adapter_missing:{assignment['worker_id']}"
                    )
                execution_inputs.append((assignment, payload, adapter))

        results: list[dict[str, Any]] = []
        max_workers = max(1, len(execution_inputs))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(adapter.execute, payload): assignment
                for assignment, payload, adapter in execution_inputs
            }
            for future in as_completed(futures):
                assignment = futures[future]
                try:
                    receipt = future.result()
                except Exception as exc:
                    receipt = None
                    execution_error = f"adapter_exception:{type(exc).__name__}:{exc}"
                with self._lock:
                    task_id = assignment["task_id"]
                    worker_id = assignment["worker_id"]
                    if receipt is not None and receipt.status == "PASS":
                        transition = self.orchestrator.complete_task(
                            task_id, worker_id, receipt.result
                        )
                        self.store.put_task_result(
                            task_id,
                            receipt.result,
                            receipt.as_dict(include_result=False),
                        )
                        outcome = {
                            "task_id": task_id,
                            "worker_id": worker_id,
                            "status": "SUCCEEDED",
                            "scheduler": transition,
                            "execution": receipt.as_dict(),
                        }
                    else:
                        if receipt is None:
                            error = execution_error
                            execution_doc = None
                        else:
                            error = f"worker_exit:{receipt.returncode}:{receipt.stderr_tail[-500:]}"
                            execution_doc = receipt.as_dict()
                        transition = self.orchestrator.fail_task(
                            task_id, worker_id, error
                        )
                        outcome = {
                            "task_id": task_id,
                            "worker_id": worker_id,
                            "status": transition["status"],
                            "scheduler": transition,
                            "execution": execution_doc,
                        }
                    self.revision = self.store.save(
                        self.orchestrator, expected_revision=self.revision
                    )
                    outcome["store_revision"] = self.revision
                    results.append(outcome)
        results.sort(key=lambda row: row["task_id"])
        status = self.orchestrator.get_status()
        return {
            "status": "EXECUTED",
            "results": results,
            "queued_remaining": status["task_status"]["QUEUED"],
            "active_assignments": status["active_assignments"],
            "store_revision": self.revision,
        }

    def run_until_idle(self, *, max_cycles: int = 1000) -> dict[str, Any]:
        if (
            not isinstance(max_cycles, int)
            or isinstance(max_cycles, bool)
            or max_cycles <= 0
        ):
            raise ValueError("max_cycles_invalid")
        cycles: list[dict[str, Any]] = []
        for _ in range(max_cycles):
            cycle = self.run_once()
            cycles.append(cycle)
            if cycle["status"] == "IDLE":
                break
            if cycle["status"] == "BLOCKED":
                break
        else:
            raise RuntimeError("swarm_max_cycles_exceeded")
        status = self.status()
        return {
            "status": cycle["status"],
            "cycles": cycles,
            "swarm": status,
            "store_integrity": self.store.integrity_report(),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self.orchestrator.get_status(),
                "store_revision": self.revision,
                "store_integrity": self.store.integrity_report(),
                "configured_adapters": sorted(self.adapters),
            }

    def task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._task_row(task_id)
            value = {"task": row}
            if row["status"] == "SUCCEEDED":
                value["result"] = self.store.get_task_result(task_id)
            return value

    def readiness(self) -> dict[str, Any]:
        status = self.status()
        reasons: list[str] = []
        if status["store_integrity"]["status"] != "PASS":
            reasons.append("store_integrity_failed")
        if not self.adapters:
            reasons.append("no_worker_adapters")
        if status["worker_status"]["ACTIVE"] == 0:
            reasons.append("no_active_workers")
        return {
            "ready": not reasons,
            "reasons": reasons,
            "store_revision": self.revision,
            "active_workers": status["worker_status"]["ACTIVE"],
            "configured_adapters": len(self.adapters),
        }
