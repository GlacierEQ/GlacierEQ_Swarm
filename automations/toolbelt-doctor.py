#!/usr/bin/env python3
"""Toolbelt doctor — health of skills, flippers, hire surfaces, AGENTS.

Writes state/toolbelt_doctor_last.json · stdout = summary only (token-saver).
Exit 0 if healthy (no critical failures); 1 if critical missing.
"""

from __future__ import annotations

import ast
import json
import py_compile
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
STATE = HOME / "GlacierEQ_Swarm" / "state"
AUTO = HOME / "GlacierEQ_Swarm" / "automations"
SKILLS = HOME / ".grok" / "skills"
BUNDLED = HOME / ".grok" / "bundled" / "skills"
TOOLBELT = HOME / "GlacierEQ_Swarm" / "toolbelt"
OUT = STATE / "toolbelt_doctor_last.json"

CORE_SKILLS = (
    "token-saver",
    "swarm-orchestrator",
    "path-of-highest-power",
    "repo-indexer",
)
FLIPPERS = (
    "token-100pct-savings-flipper.py",
    "device-stability-flipper.py",
    "qualification-savings-flipper.py",
    "github-ecosystem-analyzer.py",
    "aeon-moc-procode-scanner.py",
    "aworkers_orchestrator.py",
    "repo-public-promotion-flipper.py",
    "fork-private-divergence-flipper.py",
    "toolbelt-doctor.py",
    "voice-memo-stage-a-flipper.py",
    "voice-memo-stage-c-queue-flipper.py",
    "make-heavy-microwave-flipper.py",
    "akos-echo-maximize.py",
)
HIRE = (
    "jobapp_whole/WHOLE.md",
    "jobapp_whole/registry.json",
    "jobapp_showcase/SHOWCASE.md",
    "jobapp_hire_package/RESUME_MUSK_ORBIT.md",
)
LEGAL = re.compile(r"1FDV|FEDERAL-WARFARE|SUPERLUMINAL|cathedrals_cases_distill", re.I)


def check_skill_dirs(root: Path) -> list[dict]:
    rows = []
    if not root.exists():
        return [{"path": str(root), "ok": False, "error": "missing"}]
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        skill = d / "SKILL.md"
        # nested package (mimo_skills)
        nested = list(d.glob("*/SKILL.md"))
        if skill.is_file():
            rows.append(
                {
                    "id": d.name,
                    "ok": True,
                    "bytes": skill.stat().st_size,
                    "tier": "skill",
                }
            )
        elif nested:
            rows.append(
                {
                    "id": d.name,
                    "ok": True,
                    "nested": [str(p.relative_to(d)) for p in nested],
                    "note": "package root has nested SKILL only",
                    "tier": "skill_package",
                }
            )
        else:
            rows.append(
                {"id": d.name, "ok": False, "error": "no SKILL.md", "tier": "broken"}
            )
    return rows


def check_flippers() -> list[dict]:
    rows = []
    for name in FLIPPERS:
        p = AUTO / name
        row = {"id": name, "ok": False}
        if not p.is_file():
            row["error"] = "missing"
            rows.append(row)
            continue
        try:
            ast.parse(p.read_text())
            # py_compile to temp
            with tempfile.NamedTemporaryFile(suffix=".pyc", delete=True) as tf:
                py_compile.compile(str(p), cfile=tf.name, doraise=True)
            row["ok"] = True
            row["has_main"] = (
                '__name__ == "__main__"' in p.read_text() or "__main__" in p.read_text()
            )
        except Exception as e:
            row["error"] = str(e)[:200]
        rows.append(row)
    # stray tests in automations root?
    for p in AUTO.glob("test_*.py"):
        rows.append(
            {
                "id": p.name,
                "ok": False,
                "error": "test in flipper root; move to tests/",
                "tier": "misplaced",
            }
        )
    return rows


def check_hire() -> list[dict]:
    rows = []
    base = HOME / "GlacierEQ_Swarm"
    for rel in HIRE:
        p = base / rel
        row = {"id": rel, "ok": p.is_file()}
        if p.is_file():
            text = p.read_text(errors="ignore")
            row["bytes"] = len(text)
            row["legal_leak"] = bool(LEGAL.search(text))
            if row["legal_leak"]:
                row["ok"] = False
                row["error"] = "legal token"
        else:
            row["error"] = "missing"
        rows.append(row)
    return rows


def check_agents() -> dict:
    for p in (
        HOME / ".grok" / "Agents.md",
        HOME / "AGENTS.md",
        HOME / ".grok" / "AGENTS.md",
    ):
        if p.is_file() or p.is_symlink():
            t = p.read_text(errors="ignore")
            return {
                "path": str(p),
                "ok": "token-saver" in t and "L0" in t,
                "has_toolbelt_ptr": "toolbelt" in t.lower(),
                "bytes": len(t),
            }
    return {"ok": False, "error": "AGENTS.md not found"}


def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== toolbelt-doctor @ {ts}")
    skills = check_skill_dirs(SKILLS)
    bundled = check_skill_dirs(BUNDLED)
    flippers = check_flippers()
    hire = check_hire()
    agents = check_agents()

    critical = []
    for s in skills:
        if s.get("id") in CORE_SKILLS and not s.get("ok"):
            critical.append(f"core_skill:{s['id']}")
    for f in flippers:
        if f["id"] in FLIPPERS[:3] and not f.get("ok"):
            critical.append(f"flipper:{f['id']}")
    if not agents.get("ok"):
        critical.append("agents")
    for h in hire:
        if not h.get("ok"):
            critical.append(f"hire:{h['id']}")

    report = {
        "ts": ts,
        "ok": len(critical) == 0,
        "critical": critical,
        "counts": {
            "skills_ok": sum(1 for s in skills if s.get("ok")),
            "skills_total": len(skills),
            "bundled_ok": sum(1 for s in bundled if s.get("ok")),
            "bundled_total": len(bundled),
            "flippers_ok": sum(
                1
                for f in flippers
                if f.get("ok") and "misplaced" not in str(f.get("error", ""))
            ),
            "flippers_total": len([f for f in flippers if f["id"] in FLIPPERS]),
            "hire_ok": sum(1 for h in hire if h.get("ok")),
        },
        "skills": skills,
        "bundled": bundled,
        "flippers": flippers,
        "hire": hire,
        "agents": agents,
        "toolbelt_md": str(TOOLBELT / "TOOLBELT.md"),
        "registry": str(TOOLBELT / "toolbelt_registry.json"),
    }
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    # also write slim registry for activation
    reg = {
        "ts": ts,
        "t0": list(CORE_SKILLS) + ["sequential-thinking", "ai-humanizer"],
        "flippers": list(FLIPPERS),
        "hire_entry": "GlacierEQ_Swarm/jobapp_whole/WHOLE.md",
        "toolbelt": "GlacierEQ_Swarm/toolbelt/TOOLBELT.md",
        "doctor_ok": report["ok"],
        "critical": critical,
    }
    (TOOLBELT / "toolbelt_registry.json").write_text(json.dumps(reg, indent=2))

    print(
        f"ok={report['ok']} skills={report['counts']['skills_ok']}/{report['counts']['skills_total']} "
        f"flippers={report['counts']['flippers_ok']}/{report['counts']['flippers_total']} "
        f"hire={report['counts']['hire_ok']}/{len(hire)} critical={critical or 'none'}"
    )
    print(f"ptr: {OUT}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
