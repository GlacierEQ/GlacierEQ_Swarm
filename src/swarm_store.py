"""Durable state store for GlacierEQ Swarm.

The in-memory scheduler remains the single transition engine. This store makes
its state survivable: snapshots, task payloads, task results, and execution
receipts are persisted transactionally in SQLite. Snapshot writes are monotonic
by revision and protected by content digests so a restart cannot silently load
an older or corrupted orchestration state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from mechanism import SwarmOrchestrator


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class SwarmStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS swarm_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_payloads (
                    task_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_results (
                    task_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    execution_receipt_json TEXT NOT NULL,
                    execution_receipt_digest TEXT NOT NULL
                )
                """
            )

    def revision(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision FROM swarm_state WHERE singleton=1"
            ).fetchone()
        return int(row["revision"]) if row else 0

    def save(
        self, orchestrator: SwarmOrchestrator, *, expected_revision: int | None = None
    ) -> int:
        snapshot = orchestrator.snapshot()
        snapshot_json = _json(snapshot)
        digest = _sha(snapshot_json.encode("utf-8"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, snapshot_digest FROM swarm_state WHERE singleton=1"
            ).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision is not None and current != expected_revision:
                raise ValueError(
                    f"swarm_revision_conflict:{current}:{expected_revision}"
                )
            if row and row["snapshot_digest"] == digest:
                connection.commit()
                return current
            revision = current + 1
            connection.execute(
                """
                INSERT INTO swarm_state(singleton, revision, snapshot_json, snapshot_digest)
                VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    revision=excluded.revision,
                    snapshot_json=excluded.snapshot_json,
                    snapshot_digest=excluded.snapshot_digest
                """,
                (revision, snapshot_json, digest),
            )
            connection.commit()
            return revision
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load(self) -> tuple[SwarmOrchestrator, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision, snapshot_json, snapshot_digest FROM swarm_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            return SwarmOrchestrator(), 0
        snapshot_json = str(row["snapshot_json"])
        if _sha(snapshot_json.encode("utf-8")) != row["snapshot_digest"]:
            raise ValueError("swarm_snapshot_store_corrupt")
        payload = json.loads(snapshot_json)
        if not isinstance(payload, dict):
            raise ValueError("swarm_snapshot_store_invalid")
        return SwarmOrchestrator.from_snapshot(payload), int(row["revision"])

    def put_task_payload(
        self, task_id: str, payload: Any, expected_digest: str
    ) -> None:
        payload_json = _json(payload)
        digest = _digest(payload)
        if digest != expected_digest:
            raise ValueError("task_payload_digest_mismatch")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_digest FROM task_payloads WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing is not None:
                if existing["payload_digest"] == digest:
                    connection.commit()
                    return
                raise ValueError("task_payload_conflict")
            connection.execute(
                "INSERT INTO task_payloads(task_id, payload_json, payload_digest) VALUES(?, ?, ?)",
                (task_id, payload_json, digest),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_task_payload(self, task_id: str, expected_digest: str | None = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, payload_digest FROM task_payloads WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"task_payload_missing:{task_id}")
        payload_json = str(row["payload_json"])
        payload = json.loads(payload_json)
        digest = _digest(payload)
        if digest != row["payload_digest"]:
            raise ValueError("task_payload_store_corrupt")
        if expected_digest is not None and digest != expected_digest:
            raise ValueError("task_payload_scheduler_digest_mismatch")
        return payload

    def put_task_result(
        self, task_id: str, result: Any, execution_receipt: Mapping[str, Any]
    ) -> None:
        result_json = _json(result)
        result_digest = _digest(result)
        receipt_doc = dict(execution_receipt)
        receipt_json = _json(receipt_doc)
        receipt_digest = _sha(receipt_json.encode("utf-8"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT result_digest, execution_receipt_digest FROM task_results WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["result_digest"] == result_digest
                    and existing["execution_receipt_digest"] == receipt_digest
                ):
                    connection.commit()
                    return
                raise ValueError("task_result_conflict")
            connection.execute(
                """
                INSERT INTO task_results(
                    task_id, result_json, result_digest,
                    execution_receipt_json, execution_receipt_digest
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (task_id, result_json, result_digest, receipt_json, receipt_digest),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_task_result(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_json, result_digest, execution_receipt_json,
                       execution_receipt_digest
                FROM task_results WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"task_result_missing:{task_id}")
        result_json = str(row["result_json"])
        receipt_json = str(row["execution_receipt_json"])
        result = json.loads(result_json)
        receipt = json.loads(receipt_json)
        if _digest(result) != row["result_digest"]:
            raise ValueError("task_result_store_corrupt")
        if _sha(receipt_json.encode("utf-8")) != row["execution_receipt_digest"]:
            raise ValueError("task_execution_receipt_corrupt")
        return {
            "result": result,
            "result_digest": row["result_digest"],
            "execution_receipt": receipt,
            "execution_receipt_digest": row["execution_receipt_digest"],
        }

    def integrity_report(self) -> dict[str, Any]:
        errors: list[str] = []
        try:
            orchestrator, revision = self.load()
            snapshot = orchestrator.snapshot()
            scheduler_tasks = {row["task_id"]: row for row in snapshot["tasks"]}
        except Exception as exc:
            return {
                "status": "FAIL",
                "revision": 0,
                "errors": [f"snapshot:{type(exc).__name__}:{exc}"],
                "integrity_digest": _digest({"errors": [str(exc)]}),
            }

        with self._connect() as connection:
            payload_rows = connection.execute(
                "SELECT task_id, payload_json, payload_digest FROM task_payloads"
            ).fetchall()
            result_rows = connection.execute(
                "SELECT task_id, result_json, result_digest, execution_receipt_json, execution_receipt_digest FROM task_results"
            ).fetchall()
        for row in payload_rows:
            try:
                payload = json.loads(row["payload_json"])
                if _digest(payload) != row["payload_digest"]:
                    errors.append(f"payload_corrupt:{row['task_id']}")
                task = scheduler_tasks.get(row["task_id"])
                if task is None:
                    errors.append(f"payload_orphan:{row['task_id']}")
                elif task["payload_digest"] != row["payload_digest"]:
                    errors.append(f"payload_scheduler_mismatch:{row['task_id']}")
            except Exception:
                errors.append(f"payload_invalid:{row['task_id']}")
        for task_id, task in scheduler_tasks.items():
            if task["status"] not in {"SUCCEEDED"} and task_id not in {
                row["task_id"] for row in payload_rows
            }:
                errors.append(f"payload_missing:{task_id}")
        for row in result_rows:
            try:
                result = json.loads(row["result_json"])
                if _digest(result) != row["result_digest"]:
                    errors.append(f"result_corrupt:{row['task_id']}")
                if (
                    _sha(str(row["execution_receipt_json"]).encode("utf-8"))
                    != row["execution_receipt_digest"]
                ):
                    errors.append(f"execution_receipt_corrupt:{row['task_id']}")
                task = scheduler_tasks.get(row["task_id"])
                if task is None:
                    errors.append(f"result_orphan:{row['task_id']}")
                elif task["status"] != "SUCCEEDED":
                    errors.append(f"result_for_non_success:{row['task_id']}")
                elif task["result_digest"] != row["result_digest"]:
                    errors.append(f"result_scheduler_mismatch:{row['task_id']}")
            except Exception:
                errors.append(f"result_invalid:{row['task_id']}")
        core = {
            "status": "PASS" if not errors else "FAIL",
            "revision": revision,
            "scheduler_tasks": len(scheduler_tasks),
            "stored_payloads": len(payload_rows),
            "stored_results": len(result_rows),
            "errors": sorted(errors),
        }
        return {**core, "integrity_digest": _digest(core)}
