"""GlacierEQ Swarm — deterministic multi-agent task orchestration core.

The orchestrator manages worker discovery, capability-aware routing, bounded
concurrency, priority scheduling, failure recovery, and an integrity-chained
telemetry stream. It is intentionally transport-agnostic: a worker can be a
local process, remote agent, MCP service, container, or human-backed executor
as long as the adapter reports lifecycle events through this state machine.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}_missing")
    value = value.strip()
    if len(value) > 160:
        raise ValueError(f"{label}_too_long")
    return value


def _capabilities(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("capabilities_must_be_collection")
    result = frozenset(_id(value, "capability") for value in values)
    if not result:
        raise ValueError("capabilities_empty")
    return result


class WorkerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    UNHEALTHY = "UNHEALTHY"
    OFFLINE = "OFFLINE"


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD = "DEAD"


@dataclass
class Worker:
    worker_id: str
    capabilities: frozenset[str]
    max_concurrency: int
    status: WorkerStatus = WorkerStatus.ACTIVE
    active_tasks: set[str] = field(default_factory=set)
    completed_tasks: int = 0
    failed_tasks: int = 0
    heartbeat_sequence: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def available_slots(self) -> int:
        if self.status is not WorkerStatus.ACTIVE:
            return 0
        return max(0, self.max_concurrency - len(self.active_tasks))

    @property
    def load_ratio(self) -> float:
        return len(self.active_tasks) / self.max_concurrency

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "capabilities": sorted(self.capabilities),
            "max_concurrency": self.max_concurrency,
            "status": self.status.value,
            "active_tasks": sorted(self.active_tasks),
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "heartbeat_sequence": self.heartbeat_sequence,
            "metadata": self.metadata,
        }


@dataclass
class Task:
    task_id: str
    required_capabilities: frozenset[str]
    payload_digest: str
    priority: int
    max_attempts: int
    status: TaskStatus = TaskStatus.QUEUED
    assigned_worker_id: str | None = None
    attempts: int = 0
    failed_worker_ids: set[str] = field(default_factory=set)
    result_digest: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "required_capabilities": sorted(self.required_capabilities),
            "payload_digest": self.payload_digest,
            "priority": self.priority,
            "max_attempts": self.max_attempts,
            "status": self.status.value,
            "assigned_worker_id": self.assigned_worker_id,
            "attempts": self.attempts,
            "failed_worker_ids": sorted(self.failed_worker_ids),
            "result_digest": self.result_digest,
            "last_error": self.last_error,
        }


class SwarmOrchestrator:
    """Capability-aware scheduler with deterministic recovery semantics."""

    def __init__(self) -> None:
        self._workers: dict[str, Worker] = {}
        self._tasks: dict[str, Task] = {}
        self._events: list[dict[str, Any]] = []
        self._event_head = "0" * 64
        self._sequence = 0

    def _event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        self._sequence += 1
        core = {
            "sequence": self._sequence,
            "event_type": event_type,
            "fields": fields,
            "previous_digest": self._event_head,
        }
        digest = _digest(core)
        event = {**core, "digest": digest}
        self._events.append(event)
        self._event_head = digest
        return event

    def register_agent(
        self,
        worker_id: str,
        capabilities: Iterable[str],
        max_concurrency: int = 1,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        worker_id = _id(worker_id, "worker_id")
        caps = _capabilities(capabilities)
        if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or max_concurrency <= 0:
            raise ValueError("max_concurrency_invalid")
        if worker_id in self._workers:
            raise ValueError("worker_already_registered")
        worker = Worker(
            worker_id=worker_id,
            capabilities=caps,
            max_concurrency=max_concurrency,
            metadata=dict(metadata or {}),
        )
        self._workers[worker_id] = worker
        event = self._event(
            "WORKER_REGISTERED",
            worker_id=worker_id,
            capabilities=sorted(caps),
            max_concurrency=max_concurrency,
        )
        return {"status": "REGISTERED", "worker": worker.as_dict(), "event_digest": event["digest"]}

    register_worker = register_agent

    def heartbeat(self, worker_id: str, sequence: int) -> dict[str, Any]:
        worker = self._require_worker(worker_id)
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= worker.heartbeat_sequence:
            raise ValueError("heartbeat_sequence_not_monotonic")
        worker.heartbeat_sequence = sequence
        if worker.status is WorkerStatus.UNHEALTHY:
            worker.status = WorkerStatus.ACTIVE
        event = self._event("WORKER_HEARTBEAT", worker_id=worker.worker_id, heartbeat_sequence=sequence)
        return {"status": worker.status.value, "worker_id": worker.worker_id, "event_digest": event["digest"]}

    def set_worker_status(self, worker_id: str, status: WorkerStatus | str) -> dict[str, Any]:
        worker = self._require_worker(worker_id)
        try:
            new_status = status if isinstance(status, WorkerStatus) else WorkerStatus(status)
        except Exception as exc:
            raise ValueError("worker_status_invalid") from exc
        previous = worker.status
        worker.status = new_status
        recovered: list[str] = []
        if new_status in {WorkerStatus.UNHEALTHY, WorkerStatus.OFFLINE}:
            recovered = self._recover_worker_tasks(worker)
        event = self._event(
            "WORKER_STATUS_CHANGED",
            worker_id=worker.worker_id,
            previous=previous.value,
            current=new_status.value,
            requeued_tasks=recovered,
        )
        return {
            "status": new_status.value,
            "worker_id": worker.worker_id,
            "requeued_tasks": recovered,
            "event_digest": event["digest"],
        }

    def submit_task(
        self,
        task_id: str,
        required_capabilities: Iterable[str],
        payload: Any,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        task_id = _id(task_id, "task_id")
        caps = _capabilities(required_capabilities)
        if task_id in self._tasks:
            raise ValueError("task_already_exists")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValueError("priority_invalid")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts <= 0:
            raise ValueError("max_attempts_invalid")
        payload_digest = _digest(payload)
        task = Task(
            task_id=task_id,
            required_capabilities=caps,
            payload_digest=payload_digest,
            priority=priority,
            max_attempts=max_attempts,
        )
        self._tasks[task_id] = task
        event = self._event(
            "TASK_QUEUED",
            task_id=task_id,
            required_capabilities=sorted(caps),
            payload_digest=payload_digest,
            priority=priority,
            max_attempts=max_attempts,
        )
        return {"status": task.status.value, "task": task.as_dict(), "event_digest": event["digest"]}

    def _candidate_workers(self, task: Task) -> list[Worker]:
        capable = [
            worker
            for worker in self._workers.values()
            if worker.available_slots > 0
            and task.required_capabilities.issubset(worker.capabilities)
        ]
        fresh = [worker for worker in capable if worker.worker_id not in task.failed_worker_ids]
        candidates = fresh or capable
        return sorted(
            candidates,
            key=lambda worker: (
                worker.load_ratio,
                worker.failed_tasks,
                -worker.completed_tasks,
                worker.worker_id,
            ),
        )

    def dispatch(self, limit: int | None = None) -> dict[str, Any]:
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
            raise ValueError("dispatch_limit_invalid")
        queued = sorted(
            (task for task in self._tasks.values() if task.status is TaskStatus.QUEUED),
            key=lambda task: (-task.priority, task.attempts, task.task_id),
        )
        assignments: list[dict[str, Any]] = []
        unmatched: list[str] = []
        for task in queued:
            if limit is not None and len(assignments) >= limit:
                break
            candidates = self._candidate_workers(task)
            if not candidates:
                unmatched.append(task.task_id)
                continue
            worker = candidates[0]
            task.status = TaskStatus.ASSIGNED
            task.assigned_worker_id = worker.worker_id
            task.attempts += 1
            worker.active_tasks.add(task.task_id)
            event = self._event(
                "TASK_ASSIGNED",
                task_id=task.task_id,
                worker_id=worker.worker_id,
                attempt=task.attempts,
                worker_load_ratio=round(worker.load_ratio, 12),
            )
            assignments.append(
                {
                    "task_id": task.task_id,
                    "worker_id": worker.worker_id,
                    "attempt": task.attempts,
                    "event_digest": event["digest"],
                }
            )
        return {
            "status": "DISPATCHED",
            "assignments": assignments,
            "unmatched_task_ids": unmatched,
            "queued_remaining": sum(task.status is TaskStatus.QUEUED for task in self._tasks.values()),
        }

    def assign_task(
        self,
        task_id: str,
        required_capabilities: Iterable[str],
        payload: Any | None = None,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Convenience path: submit one task and immediately attempt dispatch."""
        self.submit_task(task_id, required_capabilities, payload, priority, max_attempts)
        dispatch = self.dispatch(limit=1)
        if dispatch["assignments"]:
            return {"status": "ASSIGNED", **dispatch["assignments"][0]}
        return {"status": "QUEUED", "task_id": task_id, "reason": "no_capable_worker_available"}

    def start_task(self, task_id: str, worker_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        worker = self._require_worker(worker_id)
        if task.status is not TaskStatus.ASSIGNED or task.assigned_worker_id != worker.worker_id:
            raise ValueError("task_assignment_mismatch")
        if task.task_id not in worker.active_tasks:
            raise ValueError("worker_assignment_state_corrupt")
        task.status = TaskStatus.RUNNING
        event = self._event("TASK_STARTED", task_id=task.task_id, worker_id=worker.worker_id, attempt=task.attempts)
        return {"status": task.status.value, "task_id": task.task_id, "worker_id": worker.worker_id, "event_digest": event["digest"]}

    def complete_task(self, task_id: str, worker_id: str, result: Any) -> dict[str, Any]:
        task = self._require_task(task_id)
        worker = self._require_worker(worker_id)
        self._require_owned_active_task(task, worker)
        result_digest = _digest(result)
        worker.active_tasks.remove(task.task_id)
        worker.completed_tasks += 1
        task.status = TaskStatus.SUCCEEDED
        task.assigned_worker_id = None
        task.result_digest = result_digest
        task.last_error = None
        event = self._event(
            "TASK_SUCCEEDED",
            task_id=task.task_id,
            worker_id=worker.worker_id,
            attempt=task.attempts,
            result_digest=result_digest,
        )
        return {"status": task.status.value, "task_id": task.task_id, "result_digest": result_digest, "event_digest": event["digest"]}

    def fail_task(self, task_id: str, worker_id: str, error: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        worker = self._require_worker(worker_id)
        self._require_owned_active_task(task, worker)
        error = _id(error, "error")
        worker.active_tasks.remove(task.task_id)
        worker.failed_tasks += 1
        task.failed_worker_ids.add(worker.worker_id)
        task.assigned_worker_id = None
        task.last_error = error
        terminal = task.attempts >= task.max_attempts
        task.status = TaskStatus.DEAD if terminal else TaskStatus.QUEUED
        event = self._event(
            "TASK_FAILED" if terminal else "TASK_REQUEUED",
            task_id=task.task_id,
            worker_id=worker.worker_id,
            attempt=task.attempts,
            error=error,
            next_status=task.status.value,
        )
        return {
            "status": task.status.value,
            "task_id": task.task_id,
            "attempts": task.attempts,
            "retry_available": not terminal,
            "event_digest": event["digest"],
        }

    def _recover_worker_tasks(self, worker: Worker) -> list[str]:
        recovered: list[str] = []
        for task_id in sorted(tuple(worker.active_tasks)):
            task = self._tasks[task_id]
            worker.active_tasks.remove(task_id)
            task.failed_worker_ids.add(worker.worker_id)
            task.assigned_worker_id = None
            task.last_error = "worker_unavailable"
            if task.attempts >= task.max_attempts:
                task.status = TaskStatus.DEAD
            else:
                task.status = TaskStatus.QUEUED
            recovered.append(task_id)
        return recovered

    def drain_worker(self, worker_id: str) -> dict[str, Any]:
        worker = self._require_worker(worker_id)
        worker.status = WorkerStatus.DRAINING
        event = self._event("WORKER_DRAINING", worker_id=worker.worker_id, active_tasks=sorted(worker.active_tasks))
        return {
            "status": worker.status.value,
            "worker_id": worker.worker_id,
            "active_tasks": sorted(worker.active_tasks),
            "event_digest": event["digest"],
        }

    def unregister_worker(self, worker_id: str) -> dict[str, Any]:
        worker = self._require_worker(worker_id)
        recovered = self._recover_worker_tasks(worker)
        worker.status = WorkerStatus.OFFLINE
        event = self._event("WORKER_UNREGISTERED", worker_id=worker.worker_id, requeued_tasks=recovered)
        return {
            "status": worker.status.value,
            "worker_id": worker.worker_id,
            "requeued_tasks": recovered,
            "event_digest": event["digest"],
        }

    def _require_worker(self, worker_id: str) -> Worker:
        worker_id = _id(worker_id, "worker_id")
        try:
            return self._workers[worker_id]
        except KeyError as exc:
            raise ValueError("worker_unknown") from exc

    def _require_task(self, task_id: str) -> Task:
        task_id = _id(task_id, "task_id")
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise ValueError("task_unknown") from exc

    def _require_owned_active_task(self, task: Task, worker: Worker) -> None:
        if task.status not in {TaskStatus.ASSIGNED, TaskStatus.RUNNING}:
            raise ValueError("task_not_active")
        if task.assigned_worker_id != worker.worker_id or task.task_id not in worker.active_tasks:
            raise ValueError("task_assignment_mismatch")

    def get_status(self) -> dict[str, Any]:
        worker_status = {status.value: 0 for status in WorkerStatus}
        for worker in self._workers.values():
            worker_status[worker.status.value] += 1
        task_status = {status.value: 0 for status in TaskStatus}
        for task in self._tasks.values():
            task_status[task.status.value] += 1
        capacity = sum(worker.max_concurrency for worker in self._workers.values() if worker.status is WorkerStatus.ACTIVE)
        active = sum(len(worker.active_tasks) for worker in self._workers.values())
        return {
            "status": "HEALTHY" if all(worker.status is not WorkerStatus.UNHEALTHY for worker in self._workers.values()) else "DEGRADED",
            "agents": len(self._workers),
            "worker_status": worker_status,
            "jobs": len(self._tasks),
            "task_status": task_status,
            "active_assignments": active,
            "available_capacity": max(0, capacity - active),
            "event_count": len(self._events),
            "telemetry_head": self._event_head,
        }

    swarm_status = get_status

    def telemetry(self, after_sequence: int = 0) -> list[dict[str, Any]]:
        if not isinstance(after_sequence, int) or isinstance(after_sequence, bool) or after_sequence < 0:
            raise ValueError("after_sequence_invalid")
        return [dict(event) for event in self._events if event["sequence"] > after_sequence]

    def verify_telemetry(self) -> dict[str, Any]:
        previous = "0" * 64
        errors: list[str] = []
        for expected_sequence, event in enumerate(self._events, start=1):
            core = {
                "sequence": event.get("sequence"),
                "event_type": event.get("event_type"),
                "fields": event.get("fields"),
                "previous_digest": event.get("previous_digest"),
            }
            if event.get("sequence") != expected_sequence:
                errors.append(f"sequence:{expected_sequence}")
            if event.get("previous_digest") != previous:
                errors.append(f"previous_digest:{expected_sequence}")
            if _digest(core) != event.get("digest"):
                errors.append(f"digest:{expected_sequence}")
            previous = str(event.get("digest"))
        return {
            "status": "PASS" if not errors else "FAIL",
            "event_count": len(self._events),
            "errors": errors,
            "telemetry_head": previous,
        }

    def snapshot(self) -> dict[str, Any]:
        core = {
            "schema": "glaciereq.swarm-orchestrator.v1",
            "workers": [self._workers[key].as_dict() for key in sorted(self._workers)],
            "tasks": [self._tasks[key].as_dict() for key in sorted(self._tasks)],
            "events": [dict(event) for event in self._events],
            "event_head": self._event_head,
            "sequence": self._sequence,
        }
        return {**core, "snapshot_digest": _digest(core)}

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> "SwarmOrchestrator":
        if not isinstance(payload, Mapping) or payload.get("schema") != "glaciereq.swarm-orchestrator.v1":
            raise ValueError("snapshot_schema_invalid")
        supplied_digest = payload.get("snapshot_digest")
        core = {key: payload.get(key) for key in ("schema", "workers", "tasks", "events", "event_head", "sequence")}
        if supplied_digest != _digest(core):
            raise ValueError("snapshot_digest_invalid")
        orchestrator = cls()
        workers = payload.get("workers")
        tasks = payload.get("tasks")
        events = payload.get("events")
        if not isinstance(workers, list) or not isinstance(tasks, list) or not isinstance(events, list):
            raise ValueError("snapshot_collections_invalid")
        for raw in workers:
            worker = Worker(
                worker_id=_id(raw.get("worker_id"), "worker_id"),
                capabilities=_capabilities(raw.get("capabilities") or []),
                max_concurrency=int(raw.get("max_concurrency")),
                status=WorkerStatus(raw.get("status")),
                active_tasks=set(raw.get("active_tasks") or []),
                completed_tasks=int(raw.get("completed_tasks", 0)),
                failed_tasks=int(raw.get("failed_tasks", 0)),
                heartbeat_sequence=int(raw.get("heartbeat_sequence", 0)),
                metadata=dict(raw.get("metadata") or {}),
            )
            if worker.worker_id in orchestrator._workers:
                raise ValueError("snapshot_duplicate_worker")
            orchestrator._workers[worker.worker_id] = worker
        for raw in tasks:
            task = Task(
                task_id=_id(raw.get("task_id"), "task_id"),
                required_capabilities=_capabilities(raw.get("required_capabilities") or []),
                payload_digest=str(raw.get("payload_digest") or ""),
                priority=int(raw.get("priority", 0)),
                max_attempts=int(raw.get("max_attempts", 0)),
                status=TaskStatus(raw.get("status")),
                assigned_worker_id=raw.get("assigned_worker_id"),
                attempts=int(raw.get("attempts", 0)),
                failed_worker_ids=set(raw.get("failed_worker_ids") or []),
                result_digest=raw.get("result_digest"),
                last_error=raw.get("last_error"),
            )
            if len(task.payload_digest) != 64:
                raise ValueError("snapshot_payload_digest_invalid")
            if task.max_attempts <= 0 or task.attempts < 0:
                raise ValueError("snapshot_task_attempts_invalid")
            if task.task_id in orchestrator._tasks:
                raise ValueError("snapshot_duplicate_task")
            orchestrator._tasks[task.task_id] = task
        orchestrator._events = [dict(event) for event in events]
        orchestrator._event_head = str(payload.get("event_head"))
        orchestrator._sequence = int(payload.get("sequence", 0))
        if orchestrator.verify_telemetry()["status"] != "PASS":
            raise ValueError("snapshot_telemetry_invalid")
        orchestrator._verify_cross_references()
        return orchestrator

    def _verify_cross_references(self) -> None:
        for worker in self._workers.values():
            if len(worker.active_tasks) > worker.max_concurrency:
                raise ValueError("snapshot_worker_over_capacity")
            for task_id in worker.active_tasks:
                if task_id not in self._tasks:
                    raise ValueError("snapshot_worker_references_unknown_task")
                task = self._tasks[task_id]
                if task.assigned_worker_id != worker.worker_id:
                    raise ValueError("snapshot_assignment_inconsistent")
        for task in self._tasks.values():
            if task.assigned_worker_id is not None:
                worker = self._workers.get(task.assigned_worker_id)
                if worker is None or task.task_id not in worker.active_tasks:
                    raise ValueError("snapshot_task_references_unknown_worker")


def run() -> dict[str, Any]:
    """Cold-start demonstrator used by generic operate probes."""
    swarm = SwarmOrchestrator()
    swarm.register_agent("builder-a", ["python", "tests"], max_concurrency=2)
    swarm.register_agent("builder-b", ["python", "tests", "deploy"], max_concurrency=1)
    assignment = swarm.assign_task(
        "demo-task",
        ["python", "tests"],
        {"objective": "prove swarm routing"},
        priority=10,
    )
    if assignment["status"] != "ASSIGNED":
        raise RuntimeError("demo task was not assigned")
    swarm.start_task("demo-task", assignment["worker_id"])
    swarm.complete_task("demo-task", assignment["worker_id"], {"verified": True})
    status = swarm.get_status()
    return {
        "status": status["status"],
        "result": "swarm_demo_complete",
        "agents": status["agents"],
        "jobs": status["jobs"],
        "available_capacity": status["available_capacity"],
        "telemetry": swarm.verify_telemetry(),
        "snapshot_digest": swarm.snapshot()["snapshot_digest"],
    }


Mechanism = SwarmOrchestrator
