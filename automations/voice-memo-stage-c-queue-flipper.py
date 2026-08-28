#!/usr/bin/env python3
"""Stage-C STT priority queue — zero LLM MICROWAVE.

Builds ranked queue from Stage-A intake. Does not invent transcripts.
STT remains blocked until whisper/whisper-cpp/remote available.

Usage:
  python3 ~/GlacierEQ_Swarm/automations/voice-memo-stage-c-queue-flipper.py
  python3 ... --top 30 --min-sec 5 --max-sec 600
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE = Path.home() / "GlacierEQ_Swarm/state"
VM = STATE / "voice_memos"
INTAKE = VM / "intake"
OUT = VM / "stage_c_queue.json"
LAST = STATE / "voice_memo_stage_c_queue_last.json"
OVERLAP = VM / "aeon_queue_overlap.json"


def load_intake() -> list[dict]:
    rows = []
    if not INTAKE.is_dir():
        return rows
    for p in INTAKE.glob("*.json"):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def aeon_tokens() -> set[str]:
    if not OVERLAP.is_file():
        return set()
    try:
        j = json.loads(OVERLAP.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for m in j.get("matched") or []:
        fid = m.get("file_id")
        if fid:
            out.add(fid)
    return out


def score(rec: dict, aeon: set[str]) -> float:
    """Higher = process sooner.

    Prefer: short enough for device, non-empty, aeon-matched (legal path private),
    recent local_date. Penalize multi-hour on 8GB host.
    """
    d = float(rec.get("duration_seconds") or 0)
    size = int(rec.get("file_size_bytes") or 0)
    s = 0.0
    # sweet spot 30s–10m on this host
    if 30 <= d <= 600:
        s += 50
    elif 5 <= d < 30:
        s += 35
    elif 600 < d <= 1800:
        s += 20
    elif d > 1800:
        s += 5  # defer heavy
    if d < 1 or size < 50_000:
        s -= 100  # emptyish last
    if rec.get("file_id") in aeon:
        s += 15  # legal pipeline priority but keep private
    # recency from local_date string
    ld = rec.get("local_date") or ""
    if ld.startswith("2026"):
        s += 10
    elif ld.startswith("2025"):
        s += 5
    # prefer smaller files for first STT experiments
    if size and size < 5_000_000:
        s += 8
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--min-sec", type=float, default=5.0)
    ap.add_argument("--max-sec", type=float, default=7200.0)
    args = ap.parse_args()

    recs = load_intake()
    if not recs:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "no intake — run voice-memo-stage-a-flipper first",
                }
            )
        )
        return 1

    aeon = aeon_tokens()
    ranked = []
    for r in recs:
        d = float(r.get("duration_seconds") or 0)
        if d < args.min_sec or d > args.max_sec:
            continue
        ranked.append(
            {
                "file_id": r.get("file_id"),
                "duration_seconds": d,
                "file_size_bytes": r.get("file_size_bytes"),
                "local_date": r.get("local_date"),
                "sha256_hash": r.get("sha256_hash"),
                "resolved_path": r.get("resolved_path"),
                "aeon_matched": r.get("file_id") in aeon,
                "priority_score": round(score(r, aeon), 2),
                "stt_status": "queued_blocked_no_whisper",
                "privacy": "legal_private"
                if r.get("file_id") in aeon
                else "private_default",
            }
        )
    ranked.sort(key=lambda x: (-x["priority_score"], x["duration_seconds"]))
    top = ranked[: args.top]

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "intake_total": len(recs),
        "ranked_total": len(ranked),
        "top_n": len(top),
        "stt_engine": None,
        "stt_blocked_reason": "no whisper/faster_whisper/mlx_whisper/whisper-cpp; ollama has 0 models; disk ~6.4Gi free",
        "device_policy": "8GB host: process short memos first; never load large-v3 locally",
        "queue": top,
        "ptrs": {
            "queue": str(OUT),
            "intake": str(INTAKE),
            "status": str(VM / "STATUS.md"),
        },
    }
    VM.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    LAST.write_text(
        json.dumps(
            {
                "ts": report["ts"],
                "ok": True,
                "top_n": len(top),
                "ranked_total": len(ranked),
                "first": top[0]["file_id"] if top else None,
                "stt": "blocked",
                "ptr": str(OUT),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "top_n": len(top),
                "ranked": len(ranked),
                "first3": [q["file_id"] for q in top[:3]],
                "ptr": str(OUT),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
