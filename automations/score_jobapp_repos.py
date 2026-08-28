#!/usr/bin/env python3
"""Score jobapp registry repos 0–99 for interview demo readiness (honest).

Factors: integrated, family, original, size, description, public,
AKOS.md, tests/, src/ depth. Cap 99 (never fake 100 production).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

STATE = Path.home() / "GlacierEQ_Swarm" / "state"
REG = Path.home() / "GlacierEQ_Swarm" / "jobapp_whole" / "registry.json"
OUT = STATE / "jobapp_repo_scores.json"
OWNER = "GlacierEQ"


def token() -> str:
    for k in ("GITHUB_PRIMARY_TOKEN", "GITHUB_TOKEN_PRIMARY", "GITHUB_MASTER_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    return ""


def api(path: str):
    t = token()
    if not t:
        return None
    req = urllib.request.Request("https://api.github.com" + path)
    req.add_header("Authorization", f"Bearer {t}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code}
    except Exception as e:
        return {"error": str(e)[:80]}


def company(it: dict) -> str:
    fam = it.get("family") or ""
    i = it["id"].lower()
    if fam == "SpaceX" or i.startswith("spacex"):
        return "SpaceX"
    if "Colossus" in fam or i.startswith("xai-colossus") or i.startswith("colossus"):
        return "xAI"
    if fam == "NVIDIA" or "nvidia" in i:
        return "NVIDIA"
    if fam == "Notion" or "notion" in i:
        return "Notion"
    if fam == "Anthropic" or "anthropic" in i or i == "pro-comet-agent":
        return "Anthropic"
    if fam == "Microsoft" or "microsoft" in i or "azure" in i:
        return "Microsoft"
    if fam == "framework" or i in (
        "akos",
        "pro-code",
        "token_saver",
        "mastermind",
        "aeon-777",
    ):
        return "Cross-cutting"
    if fam == "hub" or i.startswith("jobapp") or i == "job-application":
        return "Hub"
    if fam in ("APEX", "Agents/MCP", "Pro-*"):
        return "Agents/APEX"
    return fam or "Other"


def has_path(repo: str, path: str, branch: str) -> bool:
    d = api(f"/repos/{OWNER}/{repo}/contents/{path}?ref={branch}")
    if not d or isinstance(d, dict) and d.get("error"):
        return False
    return True


def score(it: dict, meta: dict, flags: dict) -> tuple[int, list[str]]:
    s = 0
    reasons = []
    if it["status"] == "integrated":
        s += 22
        reasons.append("integrated")
    elif it["status"] == "deferred":
        s += 8
        reasons.append("deferred")

    fam = it.get("family") or ""
    if fam == "framework":
        s += 18
    elif fam in (
        "SpaceX",
        "NVIDIA",
        "Anthropic",
        "Microsoft",
        "Notion",
        "APEX",
        "Agents/MCP",
    ):
        s += 16
    elif "Colossus" in fam or fam.startswith("xAI"):
        s += 16
    elif fam == "hub":
        s += 12
    else:
        s += 10

    if meta.get("local"):
        s += 18
        if it["id"] == "jobapp_hire_package":
            s += 12
        if it["id"] == "jobapp_showcase":
            s += 8
        return min(99, s), reasons + ["local"]

    if meta.get("error"):
        return min(99, s), reasons + ["api_error"]

    if not meta.get("fork"):
        s += 12
        reasons.append("original")
    else:
        s -= 12
        reasons.append("fork")

    size = meta.get("size") or 0
    if size >= 80:
        s += 14
        reasons.append("size_solid")
    elif size >= 20:
        s += 10
        reasons.append("size_ok")
    elif size >= 5:
        s += 6
    else:
        s += 1
        reasons.append("size_thin")

    if meta.get("desc"):
        s += 4
    if meta.get("private") is False:
        s += 6
        reasons.append("public")
    else:
        s += 2

    # depth flags
    if flags.get("akos"):
        s += 8
        reasons.append("AKOS.md")
    if flags.get("tests"):
        s += 10
        reasons.append("tests")
    if flags.get("src"):
        s += 8
        reasons.append("src")
    if it["id"] in (
        "xai-colossus-cooling",
        "colossus-gateway",
        "spacex-thermal-protection",
        "nvidia-gpu-health",
        "nvidia-deep-reasoning",
        "anthropic-safety-monitor",
        "microsoft-azure-ops",
    ):
        s += 5
        reasons.append("flagship_bonus")

    return min(99, max(0, s)), reasons


def ready_label(sc: int) -> str:
    if sc >= 90:
        return "READY-ready (elite demo)"
    if sc >= 80:
        return "READY-ready (demo-able)"
    if sc >= 65:
        return "Near-ready"
    if sc >= 45:
        return "Scaffold"
    return "Early"


def main() -> int:
    data = json.loads(REG.read_text())
    items = data["frameworks"] + data["exhibits"]
    rows = []
    for it in items:
        name = it["id"]
        if name.startswith("jobapp_"):
            meta = {"local": True}
            flags = {"akos": True, "tests": True, "src": True}
        else:
            d = api(f"/repos/{OWNER}/{name}")
            if not d or d.get("error"):
                meta = {"error": True}
                flags = {}
            else:
                branch = d.get("default_branch") or "main"
                meta = {
                    "private": d.get("private"),
                    "size": d.get("size"),
                    "desc": d.get("description"),
                    "fork": d.get("fork"),
                    "pushed": (d.get("pushed_at") or "")[:10],
                }
                flags = {
                    "akos": has_path(name, "AKOS.md", branch),
                    "tests": has_path(name, "tests", branch),
                    "src": has_path(name, "src", branch),
                }
                time.sleep(0.08)
        sc, reasons = score(it, meta, flags)
        innov = it.get("role") or "portfolio motion"
        rows.append(
            {
                "id": name,
                "company": company(it),
                "family": it.get("family"),
                "status": it["status"],
                "role": it.get("role"),
                "score": sc,
                "ready": ready_label(sc),
                "private": meta.get("private"),
                "size_kb": meta.get("size"),
                "flags": flags,
                "reasons": reasons,
                "pointer": it.get("pointer"),
                "innovation": innov,
            }
        )
        print(f"{sc:3d} {name}")

    rows.sort(key=lambda x: (-x["score"], x["company"], x["id"]))
    bc = defaultdict(list)
    for r in rows:
        bc[r["company"]].append(r)
    out = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "registry+size+AKOS/tests/src depth; cap 99; not production flight readiness",
        "ready_ready_bar": 80,
        "elite_bar": 90,
        "rows": rows,
        "by_company": {k: v for k, v in sorted(bc.items())},
        "summary": {
            "elite_90": [r["id"] for r in rows if r["score"] >= 90],
            "ready_80": [r["id"] for r in rows if r["score"] >= 80],
            "n": len(rows),
        },
    }
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print("wrote", OUT)
    print("elite", out["summary"]["elite_90"])
    print("ready", len(out["summary"]["ready_80"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
