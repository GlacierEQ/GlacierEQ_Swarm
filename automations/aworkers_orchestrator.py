#!/usr/bin/env python3
"""AWorkers — meta-orchestrator: workers that run workers + unified memory.

Zero-LLM core path: plan packets → parallel L1 flippers → merge results into
unified_memory.json. Notion/SDAL workers are addressed by pointer (dispatch
hint), not reimplemented here.

Usage:
  python3 aworkers_orchestrator.py                  # status + memory load
  python3 aworkers_orchestrator.py run              # default massive pack
  python3 aworkers_orchestrator.py run --goal "..." # custom goal label
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

STATE = Path.home() / "GlacierEQ_Swarm/state"
AUTO = Path.home() / "GlacierEQ_Swarm/automations"
MEMORY = STATE / "unified_memory.json"
REGISTRY = STATE / "aworkers_registry.json"
LAST = STATE / "aworkers_last_run.json"
SCRATCH = STATE / "daily_scratch"

# Default L1 pack for "massive work" without LLM
DEFAULT_PACK = [
    "device-stability-flipper.py",
    "token-100pct-savings-flipper.py",
    "github-ecosystem-analyzer.py",
    "aeon-moc-procode-scanner.py",
    "qualification-savings-flipper.py",
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    return json.loads(path.read_text())


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def run_flipper(script: str, timeout: int = 300) -> dict:
    path = AUTO / script
    rec = {
        "worker_id": script,
        "layer": "L1_local_zero_llm",
        "status": "error",
        "outputs_ptr": None,
        "tail": "",
        "error": None,
        "ts": now(),
    }
    if not path.is_file():
        rec["error"] = f"missing {path}"
        return rec
    try:
        out = subprocess.check_output(
            [sys.executable, str(path)],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            cwd=str(AUTO),
        )
        rec["status"] = "ok"
        rec["tail"] = out[-800:] if out else ""
        # capture ptr lines
        for line in (out or "").splitlines():
            if line.strip().startswith("ptr:") or "ptr:" in line:
                rec["outputs_ptr"] = line.strip()
        return rec
    except subprocess.TimeoutExpired as e:
        rec["error"] = f"timeout {timeout}s"
        rec["tail"] = (e.output or "")[-400:] if isinstance(e.output, str) else ""
        return rec
    except Exception as e:
        rec["error"] = str(e)
        return rec


def merge_memory(
    goal: str, results: list[dict], cathedral: str | None, case_id: str | None
) -> dict:
    mem = load_json(MEMORY, {})
    mem["ts"] = now()
    sess = mem.setdefault("session", {})
    sess["goal"] = goal
    if cathedral:
        sess["cathedral"] = cathedral
    if case_id:
        sess["case_id"] = case_id
    decisions = sess.setdefault("decisions", [])
    open_loops = sess.setdefault("open_loops", [])
    ok = [r for r in results if r.get("status") == "ok"]
    err = [r for r in results if r.get("status") != "ok"]
    decisions.append(
        {
            "ts": now(),
            "type": "aworkers_run",
            "goal": goal,
            "ok_count": len(ok),
            "err_count": len(err),
            "workers": [r.get("worker_id") for r in results],
        }
    )
    # keep last 50 decisions
    sess["decisions"] = decisions[-50:]
    for r in err:
        open_loops.append(
            {
                "ts": now(),
                "worker_id": r.get("worker_id"),
                "error": r.get("error"),
                "next": "re-run flipper or inspect log",
            }
        )
    sess["open_loops"] = open_loops[-30:]
    sess["last_worker_run"] = {
        "ts": now(),
        "goal": goal,
        "results_ptr": str(LAST),
        "ok": len(ok),
        "err": len(err),
    }
    save_json(MEMORY, mem)
    return mem


def status() -> int:
    mem = load_json(MEMORY, {})
    reg = load_json(REGISTRY, {})
    print("=== AWorkers status ===")
    print(f"memory: {MEMORY} ts={mem.get('ts')}")
    sess = mem.get("session") or {}
    print(f"session.goal: {sess.get('goal')}")
    print(f"session.cathedral: {sess.get('cathedral')} case: {sess.get('case_id')}")
    print(f"last_worker_run: {sess.get('last_worker_run')}")
    print(f"open_loops: {len(sess.get('open_loops') or [])}")
    print(f"registry layers: {list((reg.get('layers') or {}).keys())}")
    print("L1 flippers available:")
    for s in DEFAULT_PACK:
        print(f"  {'OK' if (AUTO / s).is_file() else 'MISSING'} {s}")
    print(f"ptr: {LAST if LAST.exists() else '(no run yet)'}")
    return 0


def run(goal: str, cathedral: str | None, case_id: str | None, max_workers: int) -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    print(f"=== AWorkers run @ {now()}")
    print(f"goal: {goal}")
    print(f"cathedral: {cathedral} case: {case_id}")
    print(f"dispatch L1 pack ({len(DEFAULT_PACK)}) max_workers={max_workers}")

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(run_flipper, s): s for s in DEFAULT_PACK}
        for fut in as_completed(futs):
            rec = fut.result()
            results.append(rec)
            print(
                f"  [{rec['status']}] {rec['worker_id']} {rec.get('outputs_ptr') or rec.get('error') or ''}"
            )

    mem = merge_memory(goal, results, cathedral, case_id)
    report = {
        "ts": now(),
        "goal": goal,
        "cathedral": cathedral,
        "case_id": case_id,
        "results": results,
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "err": sum(1 for r in results if r["status"] != "ok"),
        "memory_ptr": str(MEMORY),
        "notion_hints": {
            "sdal": "https://app.notion.com/p/38fb1e4f3223818f889ecb8bce7c91cc",
            "worker_registry": "https://app.notion.com/p/95b27c8ab04b4512a986e01d5d124e5d",
            "control_hub": "https://app.notion.com/p/39ab1e4f322381efa889c52df4ee8586",
            "mesh_config": "mimo-config-backup/NOTION_WORKERS_MESH.json",
        },
        "next_layers": [
            "L3: authorize SDAL cycle on Notion Control Center if case work",
            "L4: deploy_waves.py if mesh stale",
            "L2: spawn_subagent / pistons only when L1 insufficient",
        ],
    }
    save_json(LAST, report)
    print(f"ok={report['ok']} err={report['err']}")
    print(f"ptr: {LAST}")
    print(f"memory: {MEMORY}")
    return 0 if report["err"] == 0 else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AWorkers meta-orchestrator")
    p.add_argument("cmd", nargs="?", default="status", choices=["status", "run"])
    p.add_argument(
        "--goal", default="massive local pack: stability + token + map + qual"
    )
    p.add_argument("--cathedral", default="AKOS / Grok Engineering")
    p.add_argument("--case", default="engineering_qualification", dest="case_id")
    p.add_argument("--max-workers", type=int, default=3)
    args = p.parse_args(argv)
    if args.cmd == "status":
        return status()
    return run(args.goal, args.cathedral, args.case_id, args.max_workers)


if __name__ == "__main__":
    sys.exit(main())
