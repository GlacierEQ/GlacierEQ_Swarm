"""Durable canonical ledger for estate-wide CRYSTALLIZATION-MANDATE progress."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

TERMINAL = {"CRYSTALLIZED", "CANONICALIZED_SUCCESSOR", "INTENTIONALLY_ARCHIVED"}
UNRESOLVED = {
    "UNKNOWN",
    "DISCOVERED",
    "UNDERSTOOD",
    "BROKEN",
    "INCOMPLETE",
    "FUNCTIONAL",
    "COMPLETE",
    "DEPLOYED",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class CrystallizationLedger:
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
                CREATE TABLE IF NOT EXISTS repositories (
                    repository TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    metadata_digest TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    inventory_generation INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_runs (
                    generation INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    repository_count INTEGER NOT NULL,
                    inventory_digest TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_units (
                    task_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    repository_status TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(repository) REFERENCES repositories(repository)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_units_repo_generation ON work_units(repository, generation DESC)"
            )

    def record_inventory(self, repositories: Iterable[Mapping[str, Any]]) -> int:
        rows = [dict(row) for row in repositories]
        names = [row.get("full_name") for row in rows]
        if not rows or not all(isinstance(name, str) and "/" in name for name in names):
            raise ValueError("inventory_invalid")
        if len(names) != len(set(names)):
            raise ValueError("inventory_duplicate_repository")
        rows.sort(key=lambda row: str(row["full_name"]).lower())
        digest = _digest(rows)
        recorded = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT INTO inventory_runs(recorded_at, repository_count, inventory_digest) VALUES(?, ?, ?)",
                (recorded, len(rows), digest),
            )
            generation = int(cursor.lastrowid)
            for row in rows:
                repository = str(row["full_name"])
                metadata_json = _json(row)
                metadata_digest = hashlib.sha256(
                    metadata_json.encode("utf-8")
                ).hexdigest()
                existing = connection.execute(
                    "SELECT first_seen FROM repositories WHERE repository=?",
                    (repository,),
                ).fetchone()
                first_seen = str(existing["first_seen"]) if existing else recorded
                connection.execute(
                    """
                    INSERT INTO repositories(
                        repository, metadata_json, metadata_digest,
                        first_seen, last_seen, inventory_generation
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repository) DO UPDATE SET
                        metadata_json=excluded.metadata_json,
                        metadata_digest=excluded.metadata_digest,
                        last_seen=excluded.last_seen,
                        inventory_generation=excluded.inventory_generation
                    """,
                    (
                        repository,
                        metadata_json,
                        metadata_digest,
                        first_seen,
                        recorded,
                        generation,
                    ),
                )
            connection.commit()
            return generation
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_work_unit(
        self, task_id: str, repository: str, generation: int, result: Mapping[str, Any]
    ) -> None:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("ledger_task_id_invalid")
        if not isinstance(repository, str) or "/" not in repository:
            raise ValueError("ledger_repository_invalid")
        if not isinstance(generation, int) or generation <= 0:
            raise ValueError("ledger_generation_invalid")
        doc = dict(result)
        if doc.get("repository") != repository:
            raise ValueError("ledger_result_repository_mismatch")
        status = doc.get("status")
        if status not in TERMINAL.union(UNRESOLVED):
            raise ValueError("ledger_result_status_invalid")
        result_json = _json(doc)
        result_digest = hashlib.sha256(result_json.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT result_digest FROM work_units WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing is not None:
                if existing["result_digest"] == result_digest:
                    connection.commit()
                    return
                raise ValueError("ledger_task_result_conflict")
            if (
                connection.execute(
                    "SELECT 1 FROM repositories WHERE repository=?", (repository,)
                ).fetchone()
                is None
            ):
                raise ValueError("ledger_repository_not_in_inventory")
            connection.execute(
                """
                INSERT INTO work_units(
                    task_id, repository, generation, result_json,
                    result_digest, repository_status, recorded_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    repository,
                    generation,
                    result_json,
                    result_digest,
                    status,
                    _now(),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def latest_result(self, repository: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM work_units
                WHERE repository=?
                ORDER BY generation DESC, recorded_at DESC
                LIMIT 1
                """,
                (repository,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row["result_json"])
        if not isinstance(value, dict):
            raise ValueError("ledger_result_corrupt")
        return value

    def next_generation(self, repository: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(generation) AS max_generation FROM work_units WHERE repository=?",
                (repository,),
            ).fetchone()
        current = row["max_generation"] if row else None
        return (int(current) if current is not None else 0) + 1

    def repositories(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT repository, metadata_json FROM repositories ORDER BY lower(repository)"
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if not isinstance(metadata, dict):
                raise ValueError("ledger_metadata_corrupt")
            values.append({"repository": row["repository"], "metadata": metadata})
        return tuple(values)

    def current_ledger(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        terminal = 0
        for item in self.repositories():
            repository = item["repository"]
            metadata = item["metadata"]
            result = self.latest_result(repository)
            if result is not None:
                status = str(result["status"])
                reason = None
            elif metadata.get("archived"):
                status = "UNKNOWN"
                reason = "ARCHIVE_RESOLUTION_REQUIRED"
            elif metadata.get("fork"):
                status = "UNKNOWN"
                reason = "FORK_LINEAGE_RESOLUTION_REQUIRED"
            elif metadata.get("disabled"):
                status = "UNKNOWN"
                reason = "DISABLED_REPOSITORY_RESOLUTION_REQUIRED"
            else:
                status = "DISCOVERED"
                reason = "NO_COMPLETED_WORK_UNIT"
            counts[status] = counts.get(status, 0) + 1
            if status in TERMINAL:
                terminal += 1
            rows.append(
                {
                    "repository": repository,
                    "status": status,
                    "resolution_reason": reason,
                    "archived": bool(metadata.get("archived")),
                    "fork": bool(metadata.get("fork")),
                    "disabled": bool(metadata.get("disabled")),
                    "default_branch": metadata.get("default_branch"),
                    "latest_result": result,
                }
            )
        total = len(rows)
        unresolved = total - terminal
        core = {
            "schema": "glaciereq.crystallization-estate-ledger.v1",
            "total_repositories": total,
            "terminal_repositories": terminal,
            "unresolved_repositories": unresolved,
            "status_counts": dict(sorted(counts.items())),
            "estate_complete": total > 0 and unresolved == 0,
            "repositories": rows,
        }
        return {**core, "ledger_digest": _digest(core)}

    def integrity_report(self) -> dict[str, Any]:
        errors: list[str] = []
        with self._connect() as connection:
            repos = connection.execute(
                "SELECT repository, metadata_json, metadata_digest FROM repositories"
            ).fetchall()
            work = connection.execute(
                "SELECT task_id, repository, result_json, result_digest, repository_status FROM work_units"
            ).fetchall()
        known = {row["repository"] for row in repos}
        for row in repos:
            if (
                hashlib.sha256(str(row["metadata_json"]).encode("utf-8")).hexdigest()
                != row["metadata_digest"]
            ):
                errors.append(f"metadata_corrupt:{row['repository']}")
        for row in work:
            if row["repository"] not in known:
                errors.append(f"work_unit_orphan:{row['task_id']}")
            if (
                hashlib.sha256(str(row["result_json"]).encode("utf-8")).hexdigest()
                != row["result_digest"]
            ):
                errors.append(f"work_unit_corrupt:{row['task_id']}")
            try:
                doc = json.loads(row["result_json"])
                if doc.get("status") != row["repository_status"]:
                    errors.append(f"status_mismatch:{row['task_id']}")
            except Exception:
                errors.append(f"work_unit_json_invalid:{row['task_id']}")
        core = {
            "status": "PASS" if not errors else "FAIL",
            "repository_count": len(repos),
            "work_unit_count": len(work),
            "errors": sorted(errors),
        }
        return {**core, "integrity_digest": _digest(core)}
