#!/usr/bin/env python3
"""MICROWAVE: Stage A intake for Desktop/Organized_Voice_Memos → state/voice_memos/.

Preserves originals (symlinks). SHA-256 + afinfo duration. No STT.
Usage:
  python3 ~/GlacierEQ_Swarm/automations/voice-memo-stage-a-flipper.py
  python3 ... --root /path/to/memos --limit 10
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path.home() / "Desktop/Organized_Voice_Memos"
OUT = Path.home() / "GlacierEQ_Swarm/state/voice_memos"


def parse_name(name: str) -> dict:
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{4})_(.+)\.m4a$", name)
    if not m:
        return {}
    local_date, hm, rest = m.groups()
    return {
        "local_date": local_date,
        "local_hhmm": hm,
        "apple_export_token": rest,
        "apple_voice_memo_title": name.replace(".m4a", ""),
    }


def afinfo_meta(path: Path) -> dict:
    try:
        out = subprocess.check_output(
            ["afinfo", str(path)], text=True, stderr=subprocess.STDOUT, timeout=30
        )
    except Exception as e:
        return {"afinfo_err": str(e)}
    meta: dict = {}
    for line in out.splitlines():
        if "estimated duration:" in line:
            meta["duration_seconds"] = float(line.split(":")[-1].strip().split()[0])
        if "Data format:" in line:
            m = re.search(r"(\d+)\s*ch.*?(\d+)\s*Hz.*?(\w+)", line)
            if m:
                meta["channels"] = int(m.group(1))
                meta["sample_rate_hz"] = int(m.group(2))
                meta["audio_codec"] = m.group(3)
        if "bit rate:" in line and "bits per second" in line:
            try:
                meta["bitrate_bps"] = int(line.split(":")[-1].strip().split()[0])
            except Exception:
                pass
    return meta


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def process_one(p: Path, intake: Path) -> dict:
    resolved = p.resolve()
    st = resolved.stat()
    rec = {
        "file_id": p.stem,
        "original_filename": p.name,
        "original_path": str(p),
        "resolved_path": str(resolved),
        "file_extension": ".m4a",
        "file_size_bytes": st.st_size,
        "created_at_filesystem": datetime.fromtimestamp(st.st_ctime, timezone.utc).isoformat(),
        "modified_at_filesystem": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "import_method": "symlink_Organized_Voice_Memos→VoiceMemos.shared/Recordings",
        "device_source": "Apple Voice Memos (macOS Group Container)",
        "icloud_source": "unknown",
        "preservation_status": "original_unmodified",
        "processing_started_at": datetime.now(timezone.utc).isoformat(),
    }
    rec.update(parse_name(p.name))
    try:
        rec["sha256_hash"] = sha256_file(resolved)
    except Exception as e:
        rec["sha256_err"] = str(e)
    rec.update(afinfo_meta(resolved))
    rec["processing_completed_at"] = datetime.now(timezone.utc).isoformat()
    (intake / f"{p.stem}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    root: Path = args.root.expanduser()
    if not root.is_dir():
        print(json.dumps({"ok": False, "error": f"root missing: {root}"}))
        return 1

    out = OUT
    intake = out / "intake"
    (out / "reports").mkdir(parents=True, exist_ok=True)
    intake.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()

    files = sorted(root.glob("*.m4a"), key=lambda p: p.name)
    if args.limit:
        files = files[: args.limit]

    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(process_one, p, intake) for p in files]
        for i, fut in enumerate(as_completed(futs), 1):
            records.append(fut.result())
            if i % 50 == 0:
                print(f"{i}/{len(files)}", flush=True)

    records.sort(key=lambda r: r.get("original_filename", ""))
    durs = [r["duration_seconds"] for r in records if "duration_seconds" in r]
    total_sec = sum(durs) if durs else 0
    by_year: dict[str, int] = {}
    for r in records:
        y = (r.get("local_date") or "unknown")[:4]
        by_year[y] = by_year.get(y, 0) + 1

    index = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "processing_started_at": started,
        "root": str(root),
        "total_m4a": len(records),
        "total_bytes": sum(r.get("file_size_bytes") or 0 for r in records),
        "total_duration_seconds": round(total_sec, 2),
        "total_duration_hours": round(total_sec / 3600, 2),
        "duration_known": len(durs),
        "by_year": by_year,
        "tiny_files_under_50kb": sum(1 for r in records if (r.get("file_size_bytes") or 0) < 50_000),
        "duration_under_1s": sum(1 for r in records if (r.get("duration_seconds") or 1) < 1.0),
        "stage_c_stt": "blocked_no_whisper",
        "intake_dir": str(intake),
    }
    (out / "batch_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    legacy = Path.home() / "GlacierEQ_Swarm/state/voice_memo_batch_index.json"
    legacy.write_text(json.dumps(index, indent=2), encoding="utf-8")

    csv_path = out / "batch_index.csv"
    fields = [
        "file_id",
        "original_filename",
        "local_date",
        "local_hhmm",
        "file_size_bytes",
        "duration_seconds",
        "sample_rate_hz",
        "channels",
        "audio_codec",
        "bitrate_bps",
        "sha256_hash",
        "resolved_path",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in fields})

    facts = {
        "ok": True,
        "total": len(records),
        "hours": index["total_duration_hours"],
        "gb": round(index["total_bytes"] / 1e9, 2),
        "by_year": by_year,
        "ptrs": [str(out / "batch_index.json"), str(csv_path), str(intake)],
    }
    print(json.dumps(facts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
