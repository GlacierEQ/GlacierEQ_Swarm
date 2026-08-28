#!/usr/bin/env python3
"""AKOS + ECHO maximize harness — token savings, doctor, config, health snapshot.

Runs local flippers only (no app kills). Writes state/akos_echo_maximize_last.json.
Stdout: dense summary (token-saver).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
SWARM = HOME / "GlacierEQ_Swarm"
STATE = SWARM / "state"
AUTO = SWARM / "automations"
CONFIG = HOME / ".grok" / "config.toml"
OUT = STATE / "akos_echo_maximize_last.json"
JOB_APP = SWARM / "job-app"


def run_py(script: str) -> dict:
    p = AUTO / script
    if not p.is_file():
        return {"ok": False, "error": f"missing {script}"}
    try:
        r = subprocess.run(
            [sys.executable, str(p)],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(SWARM),
        )
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "tail": (r.stdout or r.stderr or "")[-500:],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def tighten_config() -> dict:
    """Ensure max-savings config keys without ui.yolo force."""
    if not CONFIG.exists():
        return {"ok": False, "error": "no config"}
    # reuse apply_grok_config_perf then force compact 65
    apply = AUTO / "apply_grok_config_perf.py"
    if apply.is_file():
        subprocess.run([sys.executable, str(apply)], capture_output=True, timeout=60)
    text = CONFIG.read_text()
    text2 = re.sub(
        r"(?m)^auto_compact_threshold_percent\s*=\s*\d+",
        "auto_compact_threshold_percent = 65",
        text,
        count=1,
    )
    if "auto_compact_threshold_percent" not in text2:
        text2 = text2.rstrip() + "\n\n[session]\nauto_compact_threshold_percent = 65\n"
    # ensure memory search precision
    if "[memory.search]" in text2:
        text2 = re.sub(
            r"(?m)^min_score\s*=\s*[\d.]+",
            "min_score = 0.38",
            text2,
            count=1,
        )
    if text2 != text:
        bak = CONFIG.with_name(
            f"config.toml.bak-echo-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        bak.write_text(text)
        CONFIG.write_text(text2)
        return {"ok": True, "changed": True, "backup": str(bak), "compact": 65}
    return {"ok": True, "changed": False, "compact": 65}


def parse_config_health() -> dict:
    if not CONFIG.exists():
        return {}
    t = CONFIG.read_text()
    mcps = re.findall(r"\[mcp_servers\.([^\]]+)\]", t)
    enabled = []
    for name in mcps:
        # crude: section after [mcp_servers.name] has enabled = true within 15 lines
        m = re.search(
            rf"\[mcp_servers\.{re.escape(name)}\](.*?)(?=\n\[|\Z)",
            t,
            re.S,
        )
        if m and re.search(r"enabled\s*=\s*true", m.group(1)):
            enabled.append(name)
    compact = re.search(r"auto_compact_threshold_percent\s*=\s*(\d+)", t)
    return {
        "mcp_servers": mcps,
        "mcp_enabled": enabled,
        "auto_compact": int(compact.group(1)) if compact else None,
        "memory": bool(re.search(r"\[memory\][^\[]*enabled\s*=\s*true", t, re.S)),
        "agent_yolo": "yolo = true" in t,
        "remember_approvals": "remember_tool_approvals = true" in t,
    }


def hooks_health() -> dict:
    hdir = HOME / ".grok" / "hooks"
    if not hdir.is_dir():
        return {"ok": False, "hooks": []}
    hooks = [p.name for p in hdir.glob("*.json")]
    return {
        "ok": "azop-safe-shell.json" in hooks,
        "hooks": hooks,
        "safe_shell": (hdir / "bin" / "safe-shell.sh").is_file(),
    }


def skills_presence() -> dict:
    root = HOME / ".grok" / "skills"
    need = ["token-saver", "toolbelt", "swarm-orchestrator", "path-of-highest-power"]
    present = {n: (root / n / "SKILL.md").is_file() for n in need}
    return {"ok": all(present.values()), "skills": present}


def akos_presence() -> dict:
    paths = [
        JOB_APP / "repos" / "AKOS" / "CURRENT_STATE.md",
        JOB_APP / "repos" / "AKOS" / "GROK_SWARM_BRIDGE.md",
        JOB_APP / "docs" / "AKOS_ECHO_RUNTIME.md",
        HOME / "AGENTS.md",
    ]
    return {str(p): p.exists() for p in paths}


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== akos-echo-maximize @ {ts}")
    STATE.mkdir(parents=True, exist_ok=True)

    cfg = tighten_config()
    token = run_py("token-100pct-savings-flipper.py")
    doctor = run_py("toolbelt-doctor.py")
    # stability optional light — already scheduled; skip kill-heavy
    health = {
        "ts": ts,
        "protocol": "AKOS+ECHO",
        "config_tighten": cfg,
        "config_health": parse_config_health(),
        "hooks": hooks_health(),
        "skills": skills_presence(),
        "akos": akos_presence(),
        "token_100pct": token,
        "toolbelt_doctor": doctor,
        "ptrs": {
            "echo": str(JOB_APP / "docs" / "AKOS_ECHO_RUNTIME.md"),
            "token_last": str(STATE / "token_100pct_last.json"),
            "doctor_last": str(STATE / "toolbelt_doctor_last.json"),
            "this": str(OUT),
            "agents": str(HOME / "AGENTS.md"),
        },
        "echo_loop": ["Externalize", "Compact", "Handoff", "Orchestrate"],
    }

    # pull measured savings if present
    tpath = STATE / "token_100pct_last.json"
    if tpath.is_file():
        try:
            health["token_measure"] = json.loads(tpath.read_text())
        except Exception:
            pass
    dpath = STATE / "toolbelt_doctor_last.json"
    if dpath.is_file():
        try:
            doc = json.loads(dpath.read_text())
            health["doctor_ok"] = doc.get("ok")
            health["doctor_counts"] = doc.get("counts")
        except Exception:
            pass

    OUT.write_text(json.dumps(health, indent=2))
    ch = health["config_health"]
    print(
        f"compact={ch.get('auto_compact')} mcp={ch.get('mcp_enabled')} "
        f"doctor={health.get('doctor_ok')} token_flip={token.get('ok')} "
        f"skills={health['skills'].get('ok')} hooks={health['hooks'].get('ok')}"
    )
    if health.get("token_measure"):
        tm = health["token_measure"]
        # various shapes
        pct = (
            tm.get("savings_pct")
            or tm.get("pct")
            or (tm.get("totals") or {}).get("savings_pct")
        )
        print(f"token_savings_pct={pct}")
    print(f"ptr: {OUT}")
    print(f"echo: {health['ptrs']['echo']}")
    ok = (
        bool(health.get("doctor_ok")) and token.get("ok") and health["skills"].get("ok")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
