#!/usr/bin/env python3
"""Real flipper: Load ecosystem_map.json, produce pointer-based analysis.
Uses pro-code rules: pointers, no full dumps. Writes durable state.
"""

import json
import os
from datetime import datetime, timezone

MAP_P = "/Users/kcbflux/GlacierEQ_Swarm/state/ecosystem_map.json"
STATE_P = "/Users/kcbflux/GlacierEQ_Swarm/goals/goal-2026-07-02-high-ai-position.md"
OUT_P = "/Users/kcbflux/GlacierEQ_Swarm/state/github_ecosystem_last.json"


def analyze():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"GitHub Ecosystem Analyzer (real, pointer mode) @ {ts}")
    result = {
        "ts": ts,
        "ok": False,
        "public_repos_count": None,
        "themes": None,
        "error": None,
        "ptrs": {"map": MAP_P, "goal": STATE_P, "out": OUT_P},
    }
    try:
        with open(MAP_P) as f:
            m = json.load(f)
        pubs = m.get("public_repos_count", 0)
        themes = len(m.get("key_themes", []))
        result["ok"] = True
        result["public_repos_count"] = pubs
        result["themes"] = themes
        result["private_hot"] = m.get("private_hot", [])
        result["last_daily_run"] = m.get("last_daily_run")
        print(f"Anchored {pubs} public + private signals from map (see {MAP_P})")
        print(
            f"Themes: {themes} (pro-code, colossus, mastermind, MCPs, legal, memory, spacex)"
        )
        print("Pointers only: raw MCP in state/daily_scratch/ when present")
        print(
            "For qualification: map to path-of-highest-power (pistons for workers, token-saver for efficiency)"
        )
    except Exception as e:
        result["error"] = str(e)
        print(f"Map error (ptr to {MAP_P}): {e}")
    print(f"Full state: {STATE_P}")
    print(f"ptr: {OUT_P}")

    os.makedirs(os.path.dirname(OUT_P), exist_ok=True)
    with open(OUT_P, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    analyze()
