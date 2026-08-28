#!/usr/bin/env python3
"""Real flipper: Scan AEON MOC for pro-code files, load map, pointer outputs.
Integrates with qualification for ecosystem data. Zero-LLM. Writes durable state.
"""

import json
import os
from datetime import datetime, timezone

AEON_DIR = "/Users/kcbflux/Documents/AEON-BRAIN-777/00_MOC/REPOS/"
MAP_P = "/Users/kcbflux/GlacierEQ_Swarm/state/ecosystem_map.json"
OUT_P = "/Users/kcbflux/GlacierEQ_Swarm/state/aeon_moc_last.json"


def scan():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"AEON MOC Pro-Code Scanner (real) @ {ts}")
    pro_files = []
    err = None
    try:
        if os.path.isdir(AEON_DIR):
            pro_files = [
                f
                for f in os.listdir(AEON_DIR)
                if "pro-code" in f.lower() or "pro_code" in f.lower()
            ]
        print(
            f"Found {len(pro_files)} pro-code MOC entries (ptr: full ls in background log)"
        )
    except Exception as e:
        err = str(e)
        print(f"Scan error: {e}")

    role = None
    try:
        with open(MAP_P) as f:
            m = json.load(f)
        role = m.get("pro_code", {}).get("role")
        print(f"Linked to map: {role}")
    except Exception as e:
        err = (err + "; " if err else "") + str(e)
        print(f"Map error (ptr {MAP_P}): {e}")

    print(
        "Savings: pointers to MOC files. For runner: use these as additional pro-code data."
    )
    print(f"Map: {MAP_P}")
    print(f"ptr: {OUT_P}")

    os.makedirs(os.path.dirname(OUT_P), exist_ok=True)
    with open(OUT_P, "w") as f:
        json.dump(
            {
                "ts": ts,
                "ok": err is None,
                "pro_code_count": len(pro_files),
                "pro_files": pro_files[:20],
                "map_role": role,
                "error": err,
                "ptrs": {"map": MAP_P, "aeon_dir": AEON_DIR, "out": OUT_P},
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    scan()
