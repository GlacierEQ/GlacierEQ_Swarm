#!/usr/bin/env python3
"""Burger flipper for this goal: periodic qualification run + token savings report.
Local exec, pointers only. Uses goal scratch. Follow swarm/path/pro-code.
"""

import os
import sys
import subprocess
from datetime import datetime

BASE = "/Users/kcbflux/.grok/skills/path-of-highest-power"
STATE_DIR = "/Users/kcbflux/GlacierEQ_Swarm/state"
SCRATCH = os.path.join(STATE_DIR, "daily_scratch")
STATE_PTR = "/Users/kcbflux/GlacierEQ_Swarm/goals/goal-2026-07-02-high-ai-position.md"
MAP_PTR = os.path.join(STATE_DIR, "ecosystem_map.json")
FLIPPER_LOG = os.path.join(STATE_DIR, "flipper_last_run.json")


def run():
    os.makedirs(SCRATCH, exist_ok=True)
    ts = datetime.utcnow().isoformat() + "Z"
    print(f"=== Qualification + Savings Flipper @ {ts} (durable scratch)")
    print(f"State pointer: {STATE_PTR}")
    print(f"Map pointer: {MAP_PTR}")
    result = {"ts": ts, "ok": False, "runner_tail": "", "error": None}
    try:
        out = subprocess.check_output(
            [sys.executable, f"{BASE}/.hidden_pistons/qualification_runner.py"],
            cwd=BASE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
        )
        tail = out[-1500:] if out else ""
        print(tail)
        result["ok"] = True
        result["runner_tail"] = tail
        with open(os.path.join(SCRATCH, "runner_output_tail.txt"), "w") as f:
            f.write(tail)
    except Exception as e:
        result["error"] = str(e)
        print(f"Runner error: {e}")
    try:
        import json

        with open(FLIPPER_LOG, "w") as f:
            json.dump(result, f, indent=2)
    except Exception as e:
        print(f"Log write error: {e}")
    print(
        "Pro_Code savings on ecosystem (GlacierEQ + pro-code MOC). Upgrade: pointers + MCPs for < burn."
    )
    print(f"Full results ptr: {SCRATCH}/ + {MAP_PTR} + {STATE_PTR}")


if __name__ == "__main__":
    run()
