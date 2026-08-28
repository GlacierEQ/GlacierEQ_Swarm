#!/usr/bin/env python3
"""Token-saver connector — real surface for measured savings + distributed compute.

Modes:
  compress     — dedup/collapse only (keep content)
  essence      — 2–4 bullets or short lead
  pure_pointer — 100% savings mode: body lives on disk/MCP; reply is pointer only

Distributed cognition: heavy work routes to zero-LLM flippers / local tools, not chat context.
Truth: measure bytes_in vs bytes_out; never invent %.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOKEN_SAVER_SKILL = os.path.expanduser("~/.grok/skills/token-saver/SKILL.md")
STATE_DIR = Path(os.path.expanduser("~/GlacierEQ_Swarm/state"))
LEDGER = STATE_DIR / "token_savings_ledger.json"
BLOB_DIR = STATE_DIR / "externalized_blobs"
AUTO = Path(os.path.expanduser("~/GlacierEQ_Swarm/automations"))

# Distributed compute routes (local / zero-LLM first)
COMPUTE_ROUTES = {
    "stability": "device-stability-flipper.py",
    "qual": "qualification-savings-flipper.py",
    "aeon": "aeon-moc-procode-scanner.py",
    "github_map": "github-ecosystem-analyzer.py",
    "icloud": "state/icloud_analysis.json",
    "map": "state/ecosystem_map.json",
    "agents": "~/AGENTS.md",
    "runner": ".grok/skills/path-of-highest-power/.hidden_pistons/qualification_runner.py",
    "voice_stage_a": "voice-memo-stage-a-flipper.py",
    "voice_stage_c": "voice-memo-stage-c-queue-flipper.py",
    "make_heavy": "make-heavy-microwave-flipper.py",
    "token_100": "token-100pct-savings-flipper.py",
    "capability": "state/capability_merge.json",
    "voice_status": "state/voice_memos/STATUS.md",
}


def load_rules() -> str:
    try:
        with open(TOKEN_SAVER_SKILL, "r") as f:
            return f.read()
    except Exception:
        return "# token-saver rules unavailable (fallback)"


def measure_savings(original: str, processed: str) -> dict[str, Any]:
    """Honest byte metrics. pct = saved/original; 1.0 only if out==0 or pure empty."""
    o = len(original.encode("utf-8")) if original else 0
    p = len(processed.encode("utf-8")) if processed else 0
    saved = max(0, o - p)
    pct = (saved / o) if o else 0.0
    return {
        "bytes_in": o,
        "bytes_out": p,
        "bytes_saved": saved,
        "savings_ratio": round(pct, 6),
        "savings_pct": round(pct * 100, 2),
    }


def externalize_blob(text: str, label: str = "blob") -> str:
    """Write full body to state; return short pointer (100% mode primitive)."""
    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    path = BLOB_DIR / f"{label}_{h}.txt"
    if not path.exists():
        path.write_text(text)
    return f"[ptr:{path}]"


def apply_token_saver(
    text: str,
    dedup: bool = True,
    compress: bool = True,
    external: bool = True,
    mode: str = "essence",
    blob_label: str = "ctx",
) -> str:
    """Apply token-saver rules.

    mode:
      compress     — fillercut + dedup + collapse only
      essence      — short essence (+ optional soft external note)
      pure_pointer — write full text to state, return pointer only (~100% context savings)
    """
    if not text:
        return text

    if mode == "pure_pointer" and external:
        ptr = externalize_blob(text, blob_label)
        return f"{ptr} (100% mode: body externalized; 0 body tokens in chat)"

    rules = load_rules()
    rules_l = rules.lower()
    data = text

    filler = (
        "let me",
        "sure",
        "to do",
        "here is",
        "okay",
        "alright",
        "i will",
        "as an ai",
        "i can",
        "in conclusion",
    )
    changed = True
    while changed:
        changed = False
        low = data.strip().lower()
        for f in filler:
            if low.startswith(f):
                rest = data.strip()[len(f) :].lstrip(" ,:.")
                if rest and len(rest) < len(data):
                    data = rest
                    changed = True
                break
    data = "\n".join(ln for ln in data.splitlines() if ln.strip())

    if dedup or "compact" in rules_l or "concise" in rules_l:
        lines = list(dict.fromkeys(l.strip() for l in data.splitlines() if l.strip()))
        data = "\n".join(lines)

    if compress:
        data = re.sub(r"(.)\1{2,}", r"\1\1", data)

    if mode == "compress":
        return data

    # essence
    if len(data) > 80:
        sentences = re.split(r"(?<=[.!?])\s+", data)
        essence = [s.strip() for s in sentences if s.strip()][:3]
        if external and mode == "essence":
            ptr = externalize_blob(text, blob_label) if len(text) > 400 else ""
            lead = essence[0][:100] if essence else data[:100]
            tail = f" {ptr}" if ptr else ""
            data = f"{lead}… [essence]{tail}"
        else:
            data = "\n".join("- " + s[:100] for s in essence)

    if external and mode == "essence" and len(data) > 200:
        data = data[:80] + " [ptr: GlacierEQ_Swarm/state/ + brainsync + github MCP]"

    data = data.replace("full list", "[ptr: ecosystem_map.json | github search]")
    return data


def process_file(path: str | Path, mode: str = "pure_pointer") -> dict[str, Any]:
    """Load file, apply saver, return measured record (distributed unit of work)."""
    path = Path(path).expanduser()
    rec: dict[str, Any] = {"path": str(path), "ok": False, "error": None}
    try:
        raw = path.read_text(errors="replace")
        out = apply_token_saver(raw, mode=mode, blob_label=path.stem[:24])
        m = measure_savings(raw, out)
        rec.update(m)
        rec["ok"] = True
        rec["preview"] = out[:160]
        rec["mode"] = mode
    except Exception as e:
        rec["error"] = str(e)
    return rec


def microwave_batch(
    paths: list[str | Path], mode: str = "pure_pointer", max_workers: int = 4
) -> list[dict]:
    """Parallel hyperspeed batch (MICROWAVE piston pattern) — local threads, zero LLM."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(process_file, p, mode): p for p in paths}
        for fut in as_completed(futs):
            results.append(fut.result())
    return results


def route_compute(kind: str) -> str:
    """Map cognitive job → local artifact/script (distributed logic)."""
    return COMPUTE_ROUTES.get(kind, f"[unknown route: {kind}]")


def append_ledger(records: list[dict], meta: dict | None = None) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ledger = {"runs": []}
    if LEDGER.exists():
        try:
            ledger = json.loads(LEDGER.read_text())
        except Exception:
            ledger = {"runs": []}
    run = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "meta": meta or {},
        "records": records,
        "totals": {
            "bytes_in": sum(r.get("bytes_in", 0) for r in records if r.get("ok")),
            "bytes_out": sum(r.get("bytes_out", 0) for r in records if r.get("ok")),
            "bytes_saved": sum(r.get("bytes_saved", 0) for r in records if r.get("ok")),
        },
    }
    tin = run["totals"]["bytes_in"]
    run["totals"]["savings_pct"] = (
        round(100 * run["totals"]["bytes_saved"] / tin, 2) if tin else 0.0
    )
    ledger.setdefault("runs", []).append(run)
    # keep last 50 runs
    ledger["runs"] = ledger["runs"][-50:]
    ledger["last"] = run
    LEDGER.write_text(json.dumps(ledger, indent=2))
    return LEDGER


def get_skill_path() -> str:
    return TOKEN_SAVER_SKILL


if __name__ == "__main__":
    rules = load_rules()
    sample = ("Let me explain. " + ("aaaa bbbb aaaa cccc " * 30)) * 3
    for mode in ("compress", "essence", "pure_pointer"):
        out = apply_token_saver(sample, mode=mode)
        m = measure_savings(sample, out)
        print(
            f"mode={mode} in={m['bytes_in']} out={m['bytes_out']} saved={m['bytes_saved']} pct={m['savings_pct']}"
        )
        print("  preview:", out[:100])
    print("routes:", list(COMPUTE_ROUTES))
    print("rules_len:", len(rules))
