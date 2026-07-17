#!/usr/bin/env python3
"""100% token savings flipper — distributed local compute, zero LLM.

Runs MICROWAVE batch over high-value files, pure_pointer externalizes bodies to
GlacierEQ_Swarm/state/externalized_blobs/, measures honest savings, writes ledger.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CONNECTOR = Path.home() / ".grok/skills/path-of-highest-power/.hidden_pistons/token_saver_connector.py"
STATE = Path.home() / "GlacierEQ_Swarm/state"
OUT = STATE / "token_100pct_last.json"

# Targets that would otherwise burn chat context
TARGETS = [
    Path.home() / "AGENTS.md",
    STATE / "ecosystem_map.json",
    STATE / "icloud_analysis.json",
    STATE / "device_stability_profile.md",
    STATE / "agents_monolith_distill.json",
    STATE / "capability_merge.json",
    STATE / "voice_memos" / "STATUS.md",
    STATE / "voice_memos" / "batch_summary.json",
    STATE / "voice_memos" / "stage_c_queue.json",
    Path.home() / ".grok/skills/token-saver/SKILL.md",
    Path.home() / ".grok/skills/path-of-highest-power/SKILL.md",
    Path.home() / ".grok/skills/swarm-orchestrator/SKILL.md",
    Path.home() / ".grok/skills/voice-memo-processor/SKILL.md",
    Path.home() / "mimo-code/packages/opencode/skills/gemini/make-it-heavy/SKILL.md",
]


def load_connector():
    import importlib.util

    spec = importlib.util.spec_from_file_location("token_saver_connector", CONNECTOR)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== token-100pct-savings-flipper @ {ts}")
    try:
        tsav = load_connector()
    except Exception as e:
        print(f"error: connector load failed: {e}")
        return 1

    paths = [p for p in TARGETS if p.is_file()]
    missing = [str(p) for p in TARGETS if not p.is_file()]

    # Distributed parallel batch (local threads)
    records = tsav.microwave_batch(paths, mode="pure_pointer", max_workers=4)
    ledger_path = tsav.append_ledger(
        records,
        meta={
            "flipper": "token-100pct-savings-flipper",
            "mode": "pure_pointer",
            "distributed": "ThreadPool MICROWAVE",
            "missing": missing,
        },
    )

    totals = {
        "bytes_in": sum(r.get("bytes_in", 0) for r in records if r.get("ok")),
        "bytes_out": sum(r.get("bytes_out", 0) for r in records if r.get("ok")),
        "bytes_saved": sum(r.get("bytes_saved", 0) for r in records if r.get("ok")),
        "files_ok": sum(1 for r in records if r.get("ok")),
        "files_fail": sum(1 for r in records if not r.get("ok")),
    }
    tin = totals["bytes_in"]
    totals["savings_pct"] = round(100 * totals["bytes_saved"] / tin, 2) if tin else 0.0

    # Route map for distributed cognition (no LLM)
    routes = {
        k: tsav.route_compute(k)
        for k in (
            "stability",
            "qual",
            "aeon",
            "github_map",
            "icloud",
            "map",
            "voice_stage_a",
            "voice_stage_c",
            "make_heavy",
            "token_100",
            "capability",
        )
    }

    report = {
        "ts": ts,
        "mode": "pure_pointer",
        "goal": "100% context savings for large payloads via externalize + local compute",
        "truth": "savings_pct = (bytes_in - bytes_out) / bytes_in on measured files",
        "totals": totals,
        "routes": routes,
        "ledger": str(ledger_path),
        "blobs": str(tsav.BLOB_DIR),
        "records": [
            {
                "path": r.get("path"),
                "ok": r.get("ok"),
                "bytes_in": r.get("bytes_in"),
                "bytes_out": r.get("bytes_out"),
                "savings_pct": r.get("savings_pct"),
                "preview": r.get("preview"),
                "error": r.get("error"),
            }
            for r in records
        ],
    }
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))

    print(f"files_ok={totals['files_ok']} fail={totals['files_fail']}")
    print(f"bytes_in={totals['bytes_in']} out={totals['bytes_out']} saved={totals['bytes_saved']} pct={totals['savings_pct']}")
    print(f"ptr: {OUT}")
    print(f"ledger: {ledger_path}")
    print(f"blobs: {tsav.BLOB_DIR}")
    print("distributed routes:", json.dumps(routes))
    return 0 if totals["files_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
