#!/usr/bin/env python3
"""Merge safe Grok config performance keys into ~/.grok/config.toml.

Backs up to ~/.grok/config.toml.bak-TIMESTAMP.
Does not set ui.yolo unless --force-ui-yolo.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path.home() / ".grok" / "config.toml"
STATE = Path.home() / "GlacierEQ_Swarm" / "state"


def upsert_section_kv(text: str, section: str, key: str, value: str) -> str:
    """Set key = value under [section]. Create section if missing."""
    sec_re = re.compile(rf"(?m)^\[{re.escape(section)}\]\s*$")
    m = sec_re.search(text)
    line = f"{key} = {value}"
    if not m:
        return text.rstrip() + f"\n\n[{section}]\n{line}\n"
    start = m.end()
    next_sec = re.search(r"(?m)^\[", text[start:])
    end = start + next_sec.start() if next_sec else len(text)
    body = text[start:end]
    if re.search(rf"(?m)^{re.escape(key)}\s*=", body):
        body2 = re.sub(rf"(?m)^{re.escape(key)}\s*=.*$", line, body, count=1)
    else:
        body2 = "\n" + line + "\n" + body.lstrip("\n")
    return text[:start] + body2 + text[end:]


def apply(force_ui_yolo: bool = False) -> dict:
    if not CONFIG.exists():
        raise SystemExit(f"missing {CONFIG}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = CONFIG.with_name(f"config.toml.bak-{ts}")
    shutil.copy2(CONFIG, bak)
    text = CONFIG.read_text()
    ops = [
        ("ui", "remember_tool_approvals", "true"),
        ("session", "auto_compact_threshold_percent", "70"),
        ("memory", "enabled", "true"),
        ("agent", "yolo", "true"),
        ("agent.goal", "enabled", "true"),
        ("agent.goal", "classifier_enabled", "true"),
    ]
    if force_ui_yolo:
        ops.append(("ui", "yolo", "true"))
    for sec, key, val in ops:
        text = upsert_section_kv(text, sec, key, val)
    if "[memory.search]" not in text:
        text = (
            text.rstrip() + "\n\n[memory.search]\nmax_results = 8\nmin_score = 0.35\n"
        )
    else:
        text = upsert_section_kv(text, "memory.search", "max_results", "8")
        text = upsert_section_kv(text, "memory.search", "min_score", "0.35")
    if "[memory.initial_injection]" not in text:
        text = (
            text.rstrip()
            + "\n\n[memory.initial_injection]\nenabled = true\nmin_score = 0.20\n"
        )
    else:
        text = upsert_section_kv(text, "memory.initial_injection", "enabled", "true")
        text = upsert_section_kv(text, "memory.initial_injection", "min_score", "0.20")
    if "[subagents.personas.concise]" not in text:
        text = text.rstrip() + (
            "\n\n[subagents.personas.concise]\n"
            'description = "Ultra-concise subagent persona"\n'
            'instructions = "Be extremely concise. Lead with outcome. Tables/bullets. Preserve decisions/paths."\n'
        )
    CONFIG.write_text(text)
    result = {
        "ts": ts,
        "backup": str(bak),
        "config": str(CONFIG),
        "force_ui_yolo": force_ui_yolo,
        "ok": True,
    }
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "grok_config_perf_last.json").write_text(json.dumps(result, indent=2))
    print(f"ok backup={bak}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-ui-yolo", action="store_true")
    args = ap.parse_args()
    apply(force_ui_yolo=args.force_ui_yolo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
