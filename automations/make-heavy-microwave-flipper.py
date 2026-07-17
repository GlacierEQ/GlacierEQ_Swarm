#!/usr/bin/env python3
"""Make-it-heavy × MICROWAVE — exhaustive local rigor, zero LLM chat burn.

Merges:
  - mimo-code make-it-heavy (depth, hashes, verification gates)
  - path-of-highest-power MICROWAVE (ThreadPool pure_pointer)
  - token-saver 100% mode (measure savings)

Heavy = full verification on disk artifacts, not chat walls.
"""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

STATE = Path.home() / "GlacierEQ_Swarm/state"
OUT = STATE / "make_heavy_microwave_last.json"
CONNECTOR = (
    Path.home()
    / ".grok/skills/path-of-highest-power/.hidden_pistons/token_saver_connector.py"
)

# High-stakes + mission-critical paths (exist-checked at runtime)
TARGETS = [
    STATE / "capability_merge.json",
    STATE / "voice_memos" / "STATUS.md",
    STATE / "voice_memos" / "batch_index.json",
    STATE / "voice_memos" / "batch_summary.json",
    STATE / "voice_memos" / "stage_c_queue.json",
    STATE / "voice_memos" / "aeon_queue_overlap.json",
    STATE / "token_100pct_last.json",
    STATE / "toolbelt_doctor_last.json",
    STATE / "ecosystem_map.json",
    Path.home() / "AGENTS.md",
    Path.home() / ".grok/skills/token-saver/SKILL.md",
    Path.home() / ".grok/skills/voice-memo-processor/SKILL.md",
    Path.home() / ".grok/skills/path-of-highest-power/SKILL.md",
    Path.home() / "mimo-code/packages/opencode/skills/gemini/make-it-heavy/SKILL.md",
    Path.home() / "mimo-code/GROK_DEV_PIPELINE.md",
]


def load_connector():
    import importlib.util

    spec = importlib.util.spec_from_file_location("token_saver_connector", CONNECTOR)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def verify_file(p: Path) -> dict:
    """Make-it-heavy verification gate: exists, size, sha256, mtime."""
    row = {"path": str(p), "ok": False}
    if not p.is_file():
        row["error"] = "missing"
        return row
    try:
        data = p.read_bytes()
        row.update(
            {
                "ok": True,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mtime_utc": datetime.fromtimestamp(
                    p.stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    except Exception as e:
        row["error"] = str(e)[:200]
    return row


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    paths = [p for p in TARGETS if p.is_file()]
    missing = [str(p) for p in TARGETS if not p.is_file()]

    # Exhaustive parallel verify (make-it-heavy)
    verified = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(verify_file, p): p for p in paths}
        for fut in as_completed(futs):
            verified.append(fut.result())
    verified.sort(key=lambda r: r.get("path", ""))

    # MICROWAVE pure_pointer externalize
    microwave_records = []
    savings = {"bytes_in": 0, "bytes_out": 0, "bytes_saved": 0, "files_ok": 0}
    try:
        tsav = load_connector()
        microwave_records = tsav.microwave_batch(paths, mode="pure_pointer", max_workers=4)
        for r in microwave_records:
            if r.get("ok"):
                savings["bytes_in"] += r.get("bytes_in", 0)
                savings["bytes_out"] += r.get("bytes_out", 0)
                savings["bytes_saved"] += r.get("bytes_saved", 0)
                savings["files_ok"] += 1
        if savings["bytes_in"]:
            savings["savings_pct"] = round(
                100 * savings["bytes_saved"] / savings["bytes_in"], 2
            )
        else:
            savings["savings_pct"] = 0.0
        ledger = tsav.append_ledger(
            microwave_records,
            meta={
                "flipper": "make-heavy-microwave-flipper",
                "mode": "pure_pointer",
                "protocol": "make-it-heavy×MICROWAVE",
            },
        )
    except Exception as e:
        ledger = None
        savings["error"] = str(e)[:200]

    # Voice mission slice
    intake_n = len(list((STATE / "voice_memos" / "intake").glob("*.json"))) if (
        STATE / "voice_memos" / "intake"
    ).is_dir() else 0
    queue_ptr = STATE / "voice_memos" / "stage_c_queue.json"

    report = {
        "ts": ts,
        "ok": all(v.get("ok") for v in verified) if verified else False,
        "protocol": "make-it-heavy × MICROWAVE × token-saver",
        "verified_ok": sum(1 for v in verified if v.get("ok")),
        "verified_fail": sum(1 for v in verified if not v.get("ok")),
        "missing": missing,
        "savings": savings,
        "ledger": str(ledger) if ledger else None,
        "voice": {
            "intake_json": intake_n,
            "stage_c_queue_exists": queue_ptr.is_file(),
            "stt": "blocked_no_whisper",
        },
        "mimo_merge": {
            "make_it_heavy_skill": str(
                Path.home()
                / "mimo-code/packages/opencode/skills/gemini/make-it-heavy/SKILL.md"
            ),
            "helix_pro_code": str(
                Path.home()
                / "mimo-code/packages/opencode/skills/grok/helix-pro-code/SKILL.md"
            ),
            "cli": str(Path.home() / ".mimocode/bin/mimo"),
        },
        "verified": verified,
        "ptr_capability": str(STATE / "capability_merge.json"),
    }
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    # chat-safe summary only
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "verified_ok": report["verified_ok"],
                "verified_fail": report["verified_fail"],
                "savings_pct": savings.get("savings_pct"),
                "bytes_saved": savings.get("bytes_saved"),
                "voice_intake": intake_n,
                "ptr": str(OUT),
            },
            indent=2,
        )
    )
    return 0 if report["ok"] or report["verified_ok"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
