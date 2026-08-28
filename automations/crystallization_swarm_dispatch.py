#!/usr/bin/env python3
"""Dispatch the entire owned GitHub estate through the persistent Swarm.

No representative sampling. Every repository discovered in the authoritative
inventory is recorded in the durable estate ledger. Mutable canonical repos are
submitted to `crystallization_work_unit.py`; archived/forked/disabled repos stay
explicitly unresolved until lineage/archive resolution is implemented, rather
than disappearing from scope.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crystallization_ledger import CrystallizationLedger, TERMINAL
from swarm_runtime import ConfiguredWorker, SwarmRuntime
from swarm_store import SwarmStateStore
from worker_adapters import SubprocessWorkerAdapter

OWNER = "GlacierEQ"


def run(argv: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )


def discover_owned_repositories() -> list[dict[str, Any]]:
    """Enumerate every repo owned by the authenticated account, including private."""
    proc = run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "/user/repos?affiliation=owner&per_page=100&sort=full_name&direction=asc",
        ],
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "github_inventory_failed:" + (proc.stderr or proc.stdout)[-2000:]
        )
    pages = json.loads(proc.stdout)
    if not isinstance(pages, list):
        raise ValueError("github_inventory_response_invalid")
    raw_rows: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, list):
            raw_rows.extend(row for row in page if isinstance(row, dict))
        elif isinstance(page, dict):
            raw_rows.append(page)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        full_name = raw.get("full_name")
        owner = (
            (raw.get("owner") or {}).get("login")
            if isinstance(raw.get("owner"), dict)
            else None
        )
        if not isinstance(full_name, str) or not full_name.startswith(OWNER + "/"):
            continue
        if owner != OWNER:
            continue
        if full_name in seen:
            continue
        seen.add(full_name)
        rows.append(
            {
                "full_name": full_name,
                "name": raw.get("name"),
                "default_branch": raw.get("default_branch") or "main",
                "private": bool(raw.get("private")),
                "visibility": raw.get("visibility")
                or ("private" if raw.get("private") else "public"),
                "archived": bool(raw.get("archived")),
                "disabled": bool(raw.get("disabled")),
                "fork": bool(raw.get("fork")),
                "fork_parent": (
                    (raw.get("parent") or {}).get("full_name")
                    if isinstance(raw.get("parent"), dict)
                    else None
                ),
                "description": raw.get("description") or "",
                "language": raw.get("language"),
                "topics": raw.get("topics") or [],
                "created_at": raw.get("created_at"),
                "updated_at": raw.get("updated_at"),
                "pushed_at": raw.get("pushed_at"),
                "size_kb": raw.get("size"),
                "has_issues": bool(raw.get("has_issues")),
                "has_wiki": bool(raw.get("has_wiki")),
                "html_url": raw.get("html_url"),
            }
        )
    rows.sort(key=lambda row: row["full_name"].lower())
    if not rows:
        raise RuntimeError("github_inventory_empty")
    return rows


def _slug(repository: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", repository.split("/", 1)[1]).strip("-")


def task_id(repository: str, generation: int) -> str:
    return f"crystallize::{_slug(repository)}::g{generation:04d}"


def _worker_command() -> list[str]:
    work_unit = ROOT / "automations" / "crystallization_work_unit.py"
    return [sys.executable, str(work_unit)]


def configured_workers(count: int, *, cwd: Path) -> list[ConfiguredWorker]:
    if not isinstance(count, int) or count <= 0:
        raise ValueError("worker_count_invalid")
    workers: list[ConfiguredWorker] = []
    command = _worker_command()
    for index in range(1, count + 1):
        worker_id = f"crystallizer-{index:03d}"
        adapter = SubprocessWorkerAdapter(
            worker_id=worker_id,
            argv=command,
            cwd=cwd,
            timeout_seconds=int(
                os.environ.get("CRYSTALLIZATION_WORK_UNIT_TIMEOUT", "10800")
            ),
            max_output_bytes=int(
                os.environ.get(
                    "CRYSTALLIZATION_WORK_UNIT_MAX_OUTPUT", str(16 * 1024 * 1024)
                )
            ),
        )
        workers.append(
            ConfiguredWorker(
                worker_id=worker_id,
                capabilities=("crystallize_repo",),
                max_concurrency=1,
                adapter=adapter,
            )
        )
    return workers


def latest_swarm_tasks(runtime: SwarmRuntime) -> dict[str, dict[str, Any]]:
    return {row["task_id"]: row for row in runtime.orchestrator.snapshot()["tasks"]}


def sync_results_to_ledger(runtime: SwarmRuntime, ledger: CrystallizationLedger) -> int:
    recorded = 0
    for row in runtime.orchestrator.snapshot()["tasks"]:
        task_id_value = row["task_id"]
        if (
            not task_id_value.startswith("crystallize::")
            or row["status"] != "SUCCEEDED"
        ):
            continue
        stored = runtime.store.get_task_result(task_id_value)
        result = stored["result"]
        if not isinstance(result, dict):
            raise ValueError(f"crystallization_result_invalid:{task_id_value}")
        repository = result.get("repository")
        if not isinstance(repository, str):
            raise ValueError(
                f"crystallization_result_repository_missing:{task_id_value}"
            )
        match = re.search(r"::g(\d+)$", task_id_value)
        if not match:
            raise ValueError(f"crystallization_task_generation_invalid:{task_id_value}")
        generation = int(match.group(1))
        ledger.record_work_unit(task_id_value, repository, generation, result)
        recorded += 1
    return recorded


def should_submit(
    metadata: Mapping[str, Any], latest: Mapping[str, Any] | None
) -> tuple[bool, str]:
    if metadata.get("archived"):
        return False, "ARCHIVE_RESOLUTION_REQUIRED"
    if metadata.get("fork"):
        return False, "FORK_LINEAGE_RESOLUTION_REQUIRED"
    if metadata.get("disabled"):
        return False, "DISABLED_REPOSITORY_RESOLUTION_REQUIRED"
    if latest and latest.get("status") in TERMINAL:
        return False, "ALREADY_TERMINAL"
    return True, "EXECUTE"


def submit_round(
    runtime: SwarmRuntime,
    ledger: CrystallizationLedger,
    repositories: Iterable[Mapping[str, Any]],
    *,
    push: bool,
    open_pr: bool,
    include: set[str],
    exclude: set[str],
    max_new_tasks: int,
) -> list[str]:
    existing_tasks = latest_swarm_tasks(runtime)
    submitted: list[str] = []
    for metadata in repositories:
        repository = str(metadata["full_name"])
        if include and repository not in include:
            continue
        if repository in exclude:
            continue
        latest = ledger.latest_result(repository)
        allowed, _reason = should_submit(metadata, latest)
        if not allowed:
            continue
        generation = ledger.next_generation(repository)
        identifier = task_id(repository, generation)
        if identifier in existing_tasks:
            # Already queued/running/completed in the persistent Swarm; never
            # duplicate work after process restart.
            continue
        runtime.submit_task(
            identifier,
            ["crystallize_repo"],
            {
                "repository": repository,
                "default_branch": metadata.get("default_branch") or "main",
                "push": push,
                "open_pr": open_pr,
                "inventory": {
                    "archived": bool(metadata.get("archived")),
                    "fork": bool(metadata.get("fork")),
                    "private": bool(metadata.get("private")),
                    "language": metadata.get("language"),
                    "description": metadata.get("description") or "",
                },
            },
            priority=100 if repository in include else 0,
            max_attempts=3,
        )
        submitted.append(identifier)
        if max_new_tasks > 0 and len(submitted) >= max_new_tasks:
            break
    return submitted


def write_ledger_snapshot(ledger: CrystallizationLedger, path: Path) -> dict[str, Any]:
    value = ledger.current_ledger()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dispatch the entire GlacierEQ repository estate through CRYSTALLIZATION-MANDATE Swarm workers"
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path(
            os.environ.get("CRYSTALLIZATION_STATE_ROOT", "/data/crystallization")
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("CRYSTALLIZATION_SWARM_WORKERS", "4")),
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="number of implementation generations to execute this invocation",
    )
    parser.add_argument(
        "--max-new-tasks",
        type=int,
        default=0,
        help="0 = every eligible repository in each round",
    )
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--no-pr", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.rounds <= 0:
        raise SystemExit("--rounds must be positive")
    state_root = args.state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    inventory = discover_owned_repositories()
    ledger = CrystallizationLedger(state_root / "estate-ledger.sqlite3")
    inventory_generation = ledger.record_inventory(inventory)
    snapshot_path = state_root / "estate-ledger.latest.json"

    if args.inventory_only:
        current = write_ledger_snapshot(ledger, snapshot_path)
        print(
            json.dumps(
                {
                    "inventory_generation": inventory_generation,
                    "repository_count": len(inventory),
                    "estate_complete": current["estate_complete"],
                    "ledger_digest": current["ledger_digest"],
                    "ledger_path": str(snapshot_path),
                },
                sort_keys=True,
            )
        )
        return 0 if current["estate_complete"] else 2

    runtime = SwarmRuntime(
        SwarmStateStore(state_root / "swarm.sqlite3"),
        configured_workers(args.workers, cwd=ROOT),
    )
    sync_results_to_ledger(runtime, ledger)
    include = {value if "/" in value else f"{OWNER}/{value}" for value in args.include}
    exclude = {value if "/" in value else f"{OWNER}/{value}" for value in args.exclude}

    rounds: list[dict[str, Any]] = []
    for round_index in range(1, args.rounds + 1):
        submitted = submit_round(
            runtime,
            ledger,
            inventory,
            push=not args.no_push,
            open_pr=not args.no_pr,
            include=include,
            exclude=exclude,
            max_new_tasks=args.max_new_tasks,
        )
        execution = runtime.run_until_idle()
        synced = sync_results_to_ledger(runtime, ledger)
        current = write_ledger_snapshot(ledger, snapshot_path)
        rounds.append(
            {
                "round": round_index,
                "submitted": submitted,
                "submitted_count": len(submitted),
                "synced_work_units": synced,
                "swarm_status": execution["status"],
                "unresolved_repositories": current["unresolved_repositories"],
                "status_counts": current["status_counts"],
                "ledger_digest": current["ledger_digest"],
            }
        )
        if current["estate_complete"]:
            break
        if not submitted:
            break

    final = write_ledger_snapshot(ledger, snapshot_path)
    report = {
        "schema": "glaciereq.crystallization-swarm-dispatch.v1",
        "inventory_generation": inventory_generation,
        "repository_count": len(inventory),
        "rounds": rounds,
        "swarm_store_integrity": runtime.store.integrity_report(),
        "ledger_integrity": ledger.integrity_report(),
        "estate": {
            "total_repositories": final["total_repositories"],
            "terminal_repositories": final["terminal_repositories"],
            "unresolved_repositories": final["unresolved_repositories"],
            "status_counts": final["status_counts"],
            "estate_complete": final["estate_complete"],
            "ledger_digest": final["ledger_digest"],
        },
        "ledger_path": str(snapshot_path),
    }
    print(json.dumps(report, sort_keys=True))
    if (
        report["swarm_store_integrity"]["status"] != "PASS"
        or report["ledger_integrity"]["status"] != "PASS"
    ):
        return 3
    return 0 if final["estate_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
