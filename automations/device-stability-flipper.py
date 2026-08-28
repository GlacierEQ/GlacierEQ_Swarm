#!/usr/bin/env python3
"""Device stability flipper — safe reclaim + health snapshot for low-RAM Macs.
Zero LLM. Idempotent. Does NOT quit user apps or disable login items.
Ptr outputs → GlacierEQ_Swarm/state/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

STATE = Path("/Users/kcbflux/GlacierEQ_Swarm/state")
OUT = STATE / "device_stability_last.json"
HOME = Path.home()

# Rebuildable only — never app profiles / documents / evidence
SAFE_CACHE_DIRS = [
    HOME / "Library/Caches/com.todesktop.230313mzl4w4u92.ShipIt",
    HOME / "Library/Caches/com.todesktop.240716u3u1yy41w.ShipIt",
    HOME / "Library/Caches/mem-updater",
    HOME / "Library/Caches/@littlebirddesktop-updater",
    HOME / "Library/Caches/ms-playwright-go",
    HOME / "Library/Caches/electron",
    HOME / "Library/Caches/node-gyp",
    HOME / "Library/Caches/puccinialin",
    HOME / "Library/Caches/pip",
    HOME / ".npm/_cacache",
    HOME / ".npm/_logs",
]


def sh(cmd: list[str], timeout: int = 60) -> str:
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.STDOUT, timeout=timeout
        )
    except Exception as e:
        return f"err:{e}"


def free_kb() -> int:
    out = sh(["df", "-k", "/System/Volumes/Data"])
    try:
        return int(out.strip().splitlines()[-1].split()[3])
    except Exception:
        return -1


def rm_tree(p: Path) -> int:
    if not p.exists():
        return 0
    size = 0
    try:
        for root, dirs, files in os.walk(p):
            for f in files:
                fp = Path(root) / f
                try:
                    size += fp.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass
    return size


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = free_kb()
    reclaimed = 0
    removed = []
    for d in SAFE_CACHE_DIRS:
        b = rm_tree(d)
        if b:
            reclaimed += b
            removed.append(str(d))

    # pip / npm if present
    sh(["python3", "-m", "pip", "cache", "purge"])
    if shutil.which("npm"):
        sh(["npm", "cache", "clean", "--force"])
    if shutil.which("uv"):
        sh(["uv", "cache", "prune"])

    # old grok session logs
    sessions = HOME / ".grok/sessions"
    if sessions.is_dir():
        now = time.time()
        for f in sessions.rglob("*.log"):
            try:
                if now - f.stat().st_mtime > 14 * 86400:
                    reclaimed += f.stat().st_size
                    f.unlink(missing_ok=True)
            except OSError:
                pass

    after = free_kb()
    swap = sh(["sysctl", "vm.swapusage"]).strip()
    load = sh(["uptime"]).strip()
    mem = sh(["memory_pressure"]).splitlines()[:8]

    # top RSS snapshot (do not kill)
    top = sh(["ps", "-Amco", "%mem,rss,pid,comm"])
    top_lines = top.splitlines()[:12]

    report = {
        "ts": ts,
        "free_kb_before": before,
        "free_kb_after": after,
        "reclaimed_bytes_walk": reclaimed,
        "reclaimed_mb_walk": round(reclaimed / (1024 * 1024), 1),
        "df_delta_mb": round((after - before) / 1024, 1)
        if before > 0 and after > 0
        else None,
        "removed_dirs": removed,
        "swap": swap,
        "load": load,
        "memory_pressure_head": mem,
        "top_mem": top_lines,
        "stability_profile": {
            "hw": "8GB RAM class — browsers pinned: Comet + Opera Neon",
            "keep_always": ["Comet", "Opera Neon"],
            "login_items_heavy": [
                "Dropbox Dash",
                "Cloudflare WARP",
                "Littlebird",
                "Highlight",
                "Warp",
            ],
            "advice": [
                "KEEP Comet + Opera Neon (user pin)",
                "One IDE only (Antigravity XOR kilo) — dual IDE is the thrash path",
                "Quit/pause Dropbox Dash when coding",
                "Quit Littlebird if idle; WARP only when needed",
                "Keep ≥15% free disk on Data volume",
                "Never kill Comet/Neon for stability — cut IDE/Dash first",
            ],
        },
        "ptrs": {
            "state": str(OUT),
            "agents": "/Users/kcbflux/AGENTS.md",
            "map": str(STATE / "ecosystem_map.json"),
        },
    }
    OUT.write_text(json.dumps(report, indent=2))
    print(f"=== device-stability-flipper @ {ts}")
    print(
        f"reclaimed_walk_mb={report['reclaimed_mb_walk']} df_delta_mb={report['df_delta_mb']}"
    )
    print(swap)
    print(load)
    print(f"ptr: {OUT}")


if __name__ == "__main__":
    main()
