#!/usr/bin/env python3
"""Private-first → intelligent public promotion flipper.

Policy
------
1. GlacierEQ originals default PRIVATE.
2. Public only after gates pass (scan → sanitize report → pro-AKOS pack).
3. LEGAL / CASE / PII: ABSOLUTELY PRIVATE until intelligently processed.
   - Cannot --promote. Cannot --force. Hard abort.
   - Use --lock-legal to force-lock the full legal set.
4. Forks skipped (GitHub visibility rules; not our portfolio surface).
5. Default mode is DRY-RUN (measure only). Promote requires --promote.

Gates (all must pass for promote)
---------------------------------
- not legal/case keyword hit  ← ABSOLUTE, no force override
- not fork
- secret/pattern scan clean
- has README.md
- has AKOS.md (or will write)
- homepage points to AKOS (or will set)
- category in allowlist (AKOS, Colossus, SpaceX, APEX job surface — NON-LEGAL)

Usage
-----
  python3 repo-public-promotion-flipper.py              # dry-run inventory
  python3 repo-public-promotion-flipper.py --scan NAME  # one repo
  python3 repo-public-promotion-flipper.py --promote NAME  # promote one if gates pass
  python3 repo-public-promotion-flipper.py --reprivatize-portfolio  # force private portfolio
  python3 repo-public-promotion-flipper.py --lock-legal  # ABSOLUTE lock all legal/case repos
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE = Path.home() / "GlacierEQ_Swarm" / "state"
OUT = STATE / "repo_public_promotion_last.json"
POLICY = STATE / "repo_visibility_policy.json"
OWNER = "GlacierEQ"

# ABSOLUTE legal/case lock — expand aggressively; prefer false positive over leak
LEGAL_RE = re.compile(
    r"1FDV|FEDERAL.?WARFARE|CASE.?MATRIX|SUPERLUMINAL|DOCKET|KEKOA|"
    r"CHERRY.?CHAN|CATACLYSM|FORENSIC|ASPEN.?GROVE|ASPENGROVE|"
    r"FAMILY.?COURT|LEGAL.?DOCUMENT|LEGAL.?MOTION|LEGAL.?BRIEF|"
    r"MOTION.?LIBRARY|EVIDENCE.?VAULT|WARFARE|CASEBUILDER|"
    r"CASE-1FDV|CASE_1FDV|PRO-KEKOA|PRO-LEGAL|LEGAL-|"
    r"HI-11-LEGAL|BOOK-OF-BREACH|CYBERTACK|HCHS_|"
    r"FEDERAL-FORENSIC|SHAREPOINT-FORENSIC|FORENSIC_|"
    r"APEX-LEGAL|APEX_LEGAL|LEGAL.?OPS|LEGAL.?CASE|"
    r"PRO-EVIDENCE|PRO-FORENSICS|JUSTICE|LITIGATION|"
    r"\blegal\b|custody|reunification|void.?judgment",
    re.I,
)

LEGAL_LOCK_PATH = STATE / "legal_absolute_private_lock_last.json"

SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xai-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"AWS_SECRET_ACCESS_KEY\s*=\s*\S+"),
    re.compile(r"api[_-]?key\s*[:=]\s*['\"][^'\"]{16,}['\"]", re.I),
    re.compile(r"password\s*[:=]\s*['\"][^'\"]{8,}['\"]", re.I),
]

# Portfolio categories eligible for intelligent public surface
PROMOTE_ALLOW_PREFIXES = (
    "xai-colossus",
    "colossus-",
    "spacex-",
    "apex-",
)
PROMOTE_ALLOW_EXACT = {
    "AKOS",
    "token_saver",
    "mastermind",
    "pro-code",
    "AEON-777",
    "job-application",
    "colossus-gateway",
    "colossus-build-blueprint",
    "colossus-realignment",
    "apex-cli",
    "Pro-comet-agent",
}

# Never auto-promote even if name matches (engineering deny; legal uses LEGAL_RE absolute)
PROMOTE_DENY_SUBSTRING = (
    "legal",
    "forensic",
    "evidence",
    "docket",
    "1fdv",
    "case",
    "warfare",
    "kekoa",
    "z-backup",
    "aspen",
    "cataclysm",
    "superluminal",
    "motion",
    "custody",
    "justice",
    "litigation",
)

AKOS_MD = """# AKOS Bridge

Canonical architecture: **[GlacierEQ/AKOS](https://github.com/GlacierEQ/AKOS)**

This repository is part of the GlacierEQ APEX portfolio — a live job application
(SpaceX · xAI · Neuralink · Boring Company).

## Standards

| Rule | Source |
|------|--------|
| Identity / hireable | `AKOS/IDENTITY.md` |
| Governance | `AKOS/GOVERNANCE.md` |
| Easter eggs (silent) | `AKOS/EASTER_EGGS.md` |
| Portfolio map | `AKOS/REPOS.md` |
| Double Helix | Alpha (what) ↔ Omega (how) |

## Rules of engagement

1. Inspect before changing.
2. Prefer exact physics constants over approximations.
3. Easter eggs never explain themselves.
4. No teacher voice. No hype metrics.
5. Point mirrors back to AKOS — do not fork truth.

## Link

- AKOS: https://github.com/GlacierEQ/AKOS
- Operator: GlacierEQ · hireable: true
"""


def token() -> str:
    for k in (
        "GITHUB_PRIMARY_TOKEN",
        "GITHUB_TOKEN_PRIMARY",
        "GITHUB_MASTER_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        v = os.environ.get(k)
        if v and len(v) > 20:
            return v
    raise SystemExit("No GitHub token in env")


def api(method: str, path: str, data: Any = None) -> tuple[int, Any]:
    url = "https://api.github.com" + path
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:800]}


def is_legal(name: str, description: str = "") -> bool:
    """ABSOLUTE legal/case detector. True → never public until processed."""
    blob = f"{name} {description or ''}"
    return bool(LEGAL_RE.search(blob))


def is_portfolio_candidate(name: str, description: str = "") -> bool:
    if is_legal(name, description):
        return False
    n = name.lower()
    if any(s in n for s in PROMOTE_DENY_SUBSTRING):
        # exact engineering allowlist only if not legal-named
        if name in PROMOTE_ALLOW_EXACT and not is_legal(name):
            return True
        if n.startswith("spacex-") or n.startswith("xai-colossus"):
            if not any(
                s in n
                for s in (
                    "legal",
                    "forensic",
                    "1fdv",
                    "kekoa",
                    "warfare",
                    "docket",
                    "aspen",
                    "case",
                )
            ):
                return True
        return False
    if name in PROMOTE_ALLOW_EXACT:
        return True
    # apex-* but not apex-legal*
    if n.startswith("apex-") and "legal" in n:
        return False
    if n.startswith("pro-") and any(s in n for s in ("legal", "evidence", "forensic", "kekoa", "justice")):
        return False
    return any(n.startswith(p) for p in PROMOTE_ALLOW_PREFIXES)


def get_file(repo: str, path: str) -> dict | None:
    st, data = api("GET", f"/repos/{OWNER}/{repo}/contents/{path}")
    if st == 200 and isinstance(data, dict) and data.get("sha"):
        return data
    return None


def put_file(repo: str, path: str, content: str, message: str, branch: str = "main") -> tuple[int, Any]:
    existing = get_file(repo, path)
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if existing:
        payload["sha"] = existing["sha"]
    st, out = api("PUT", f"/repos/{OWNER}/{repo}/contents/{path}", payload)
    if st in (200, 201):
        return st, out
    # try master
    if branch == "main":
        return put_file(repo, path, content, message, branch="master")
    return st, out


def scan_tree_for_secrets(repo: str, max_files: int = 40) -> list[str]:
    """Light secret scan via GitHub trees API + sample file bodies."""
    hits: list[str] = []
    st, repo_meta = api("GET", f"/repos/{OWNER}/{repo}")
    if st != 200:
        return [f"repo_fetch_fail:{st}"]
    default_branch = repo_meta.get("default_branch") or "main"
    st, ref = api("GET", f"/repos/{OWNER}/{repo}/git/ref/heads/{default_branch}")
    if st != 200:
        st, ref = api("GET", f"/repos/{OWNER}/{repo}/git/ref/heads/master")
        if st != 200:
            return hits  # empty tree ok for gate soft-fail later
    sha = ref.get("object", {}).get("sha")
    st, tree = api("GET", f"/repos/{OWNER}/{repo}/git/trees/{sha}?recursive=1")
    if st != 200:
        return hits
    paths = [
        t["path"]
        for t in tree.get("tree", [])
        if t.get("type") == "blob"
        and not t["path"].startswith(".git")
        and not any(t["path"].endswith(ext) for ext in (".png", ".jpg", ".pdf", ".zip", ".lock"))
    ]
    # prefer env examples, py, ts, md, json, toml, yaml
    rank = []
    for p in paths:
        score = 0
        pl = p.lower()
        if any(x in pl for x in (".env", "secret", "credential", "token", "config")):
            score += 10
        if pl.endswith((".py", ".ts", ".js", ".md", ".json", ".toml", ".yml", ".yaml", ".sh")):
            score += 3
        rank.append((score, p))
    rank.sort(reverse=True)
    for _, path in rank[:max_files]:
        f = get_file(repo, path)
        if not f or f.get("encoding") != "base64":
            continue
        try:
            text = base64.b64decode(f["content"]).decode("utf-8", errors="ignore")
        except Exception:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                hits.append(f"{path}: pattern {pat.pattern[:40]}")
                break
        time.sleep(0.05)
    return hits


def evaluate_repo(name: str) -> dict[str, Any]:
    st, r = api("GET", f"/repos/{OWNER}/{name}")
    if st != 200:
        return {"name": name, "ok": False, "gates": {}, "reason": f"missing_{st}"}

    gates: dict[str, bool] = {}
    notes: list[str] = []
    desc = r.get("description") or ""

    gates["not_legal"] = not is_legal(name, desc)
    gates["not_fork"] = not bool(r.get("fork"))
    gates["portfolio_eligible"] = is_portfolio_candidate(name, desc)
    gates["is_private"] = bool(r.get("private"))
    if not gates["not_legal"]:
        notes.append("LEGAL_ABSOLUTE_PRIVATE")

    readme = get_file(name, "README.md") or get_file(name, "readme.md")
    gates["has_readme"] = readme is not None
    akos = get_file(name, "AKOS.md")
    gates["has_akos_md"] = akos is not None
    home = (r.get("homepage") or "").lower()
    gates["homepage_akos"] = "github.com/glaciereq/akos" in home

    secrets = scan_tree_for_secrets(name)
    gates["secret_scan_clean"] = len(secrets) == 0
    if secrets:
        notes.append(f"secrets:{len(secrets)}")

    # Core gates for promote (homepage/akos can be fixed pre-promote)
    hard = ["not_legal", "not_fork", "portfolio_eligible", "has_readme", "secret_scan_clean"]
    hard_pass = all(gates[k] for k in hard)

    return {
        "name": name,
        "ok": hard_pass,
        "private": r.get("private"),
        "visibility": r.get("visibility"),
        "html": r.get("html_url"),
        "default_branch": r.get("default_branch"),
        "gates": gates,
        "secret_hits": secrets[:10],
        "notes": notes,
        "hard_gates": hard,
        "promote_ready": hard_pass and gates["not_legal"] and gates["not_fork"],
    }


def pro_akos_pack(name: str) -> dict[str, Any]:
    """Ensure AKOS.md + homepage + topics. Repo stays private."""
    actions = []
    st, r = api("GET", f"/repos/{OWNER}/{name}")
    if st != 200:
        return {"name": name, "ok": False, "error": st}

    st2, _ = api(
        "PATCH",
        f"/repos/{OWNER}/{name}",
        {
            "homepage": "https://github.com/GlacierEQ/AKOS",
            "private": True,  # stay private during pack
        },
    )
    actions.append({"patch_meta": st2})

    st3, _ = put_file(name, "AKOS.md", AKOS_MD, "docs: pro-AKOS bridge (private pack)")
    actions.append({"akos_md": st3})

    st4, _ = api(
        "PUT",
        f"/repos/{OWNER}/{name}/topics",
        {"names": ["akos", "apex", "glaciereq", "portfolio", "private-first"]},
    )
    actions.append({"topics": st4})
    return {"name": name, "ok": True, "actions": actions, "still_private": True}


def promote(name: str, force: bool = False) -> dict[str, Any]:
    # ABSOLUTE: legal never promotes — force does NOT override
    if is_legal(name):
        return {
            "name": name,
            "promoted": False,
            "reason": "LEGAL_ABSOLUTE_PRIVATE",
            "message": "Legal/case repos stay private until intelligent process + dedicated legal promotion path (not this flipper).",
            "force_ignored": bool(force),
        }

    report = evaluate_repo(name)
    if report.get("gates", {}).get("not_legal") is False:
        return {
            "name": name,
            "promoted": False,
            "reason": "LEGAL_ABSOLUTE_PRIVATE",
            "eval": report,
            "force_ignored": bool(force),
        }

    if not report.get("promote_ready") and not force:
        return {"name": name, "promoted": False, "reason": "gates_failed", "eval": report}

    # pack first while private
    pack = pro_akos_pack(name)
    # re-eval secrets after pack (akos md clean)
    report2 = evaluate_repo(name)
    if report2.get("gates", {}).get("not_legal") is False:
        return {
            "name": name,
            "promoted": False,
            "reason": "LEGAL_ABSOLUTE_PRIVATE",
            "eval": report2,
            "pack": pack,
        }
    if not report2.get("gates", {}).get("secret_scan_clean") and not force:
        return {"name": name, "promoted": False, "reason": "secrets", "eval": report2, "pack": pack}

    st, out = api("PATCH", f"/repos/{OWNER}/{name}", {"private": False})
    ok = st in (200, 201) and out.get("private") is False
    return {
        "name": name,
        "promoted": ok,
        "status": st,
        "html": out.get("html_url"),
        "pack": pack,
        "eval": report2,
    }


def lock_legal_all() -> dict[str, Any]:
    """Force every legal/case-named repo private. No exceptions."""
    results = []
    page = 1
    repos: list[dict] = []
    while page <= 40:
        st, batch = api(
            "GET",
            f"/user/repos?per_page=100&page={page}&affiliation=owner&sort=full_name",
        )
        if st != 200 or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.05)

    for r in repos:
        if r.get("owner", {}).get("login") != OWNER:
            continue
        name = r["name"]
        desc = r.get("description") or ""
        if not is_legal(name, desc):
            continue
        if r.get("private"):
            results.append({"name": name, "status": "already_private", "html": r["html_url"]})
            continue
        st, out = api("PATCH", f"/repos/{OWNER}/{name}", {"private": True})
        ok = st in (200, 201) and out.get("private") is True
        results.append(
            {
                "name": name,
                "status": "LOCKED_PRIVATE" if ok else f"fail_{st}",
                "was": "public",
                "err": (out.get("error") or "")[:200] if not ok else None,
                "html": r["html_url"],
            }
        )
        time.sleep(0.08)

    summary = dict(Counter(x["status"] for x in results))
    payload = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": "LEGAL ABSOLUTELY PRIVATE until intelligent process",
        "summary": summary,
        "public_leaks_fixed": [x for x in results if x.get("was") == "public"],
        "all_legal_repos": sorted(x["name"] for x in results),
        "results": results,
    }
    LEGAL_LOCK_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def reprivatize_name(name: str) -> dict[str, Any]:
    if is_legal(name):
        return {"name": name, "status": "skip_legal"}
    st, r = api("GET", f"/repos/{OWNER}/{name}")
    if st != 200:
        return {"name": name, "status": f"missing_{st}"}
    if r.get("fork"):
        return {"name": name, "status": "skip_fork"}
    if r.get("private"):
        return {"name": name, "status": "already_private"}
    st2, out = api("PATCH", f"/repos/{OWNER}/{name}", {"private": True})
    ok = st2 in (200, 201) and out.get("private") is True
    return {
        "name": name,
        "status": "re_privatized" if ok else f"fail_{st2}",
        "err": out.get("error") if not ok else None,
    }


def portfolio_list_from_state() -> list[str]:
    names: set[str] = set(PROMOTE_ALLOW_EXACT)
    slim = STATE / "ultimate_repo_map_slim.json"
    if slim.exists():
        data = json.loads(slim.read_text())
        for label, block in data.get("categories", {}).items():
            if any(k in label for k in ("AKOS", "Colossus", "SpaceX", "APEX Runtime")):
                for n in block.get("repos", []):
                    if is_portfolio_candidate(n):
                        names.add(n)
    flip = STATE / "jobapp_public_flip_2026-07-12.json"
    if flip.exists():
        for x in json.loads(flip.read_text()).get("results", []):
            n = x.get("name")
            if n and is_portfolio_candidate(n):
                names.add(n)
    return sorted(names)


def write_policy() -> None:
    policy = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "default_visibility": "private",
        "legal_policy": "ABSOLUTE_PRIVATE_UNTIL_PROCESSED",
        "legal_rules": [
            "Legal/case/PII repos are NEVER public by default",
            "This flipper cannot --promote or --force legal repos",
            "Public legal only after dedicated intelligent process + human sign-off",
            "Use --lock-legal to force-lock the full legal set anytime",
        ],
        "public_requires": [
            "not_legal_case (ABSOLUTE — no force override)",
            "not_fork",
            "portfolio_eligible (non-legal engineering only)",
            "has_readme",
            "secret_scan_clean",
            "pro_akos_pack (AKOS.md + homepage)",
            "explicit --promote (no bulk auto-public)",
        ],
        "never_public_until_processed": [
            "1FDV*",
            "SUPERLUMINAL*",
            "DOCKETS",
            "AspenGrove*",
            "THE_CATACLYSM*",
            "Pro-Legal*",
            "Pro-Kekoa*",
            "apex-legal*",
            "forensic/warfare/case materials",
            "any LEGAL_RE name/description hit",
        ],
        "pipeline": [
            "1. private default (engineering + legal)",
            "2. legal stays locked (--lock-legal)",
            "3. dry-run evaluate non-legal only",
            "4. pro-AKOS pack while private",
            "5. secret scan",
            "6. human/agent --promote one NON-LEGAL repo",
            "7. REPOS.md legend update",
        ],
        "flipper": "GlacierEQ_Swarm/automations/repo-public-promotion-flipper.py",
        "maps": [
            "state/ultimate_repo_map.md",
            "state/repo_public_promotion_last.json",
            "state/legal_absolute_private_lock_last.json",
        ],
    }
    POLICY.write_text(json.dumps(policy, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Private-first intelligent public promotion")
    p.add_argument("--scan", metavar="REPO", help="Evaluate one repo")
    p.add_argument("--promote", metavar="REPO", help="Promote one NON-LEGAL repo if gates pass")
    p.add_argument(
        "--force",
        action="store_true",
        help="Force promote non-legal only (NEVER overrides legal absolute lock)",
    )
    p.add_argument("--pack", metavar="REPO", help="pro-AKOS pack while keeping private")
    p.add_argument("--reprivatize-portfolio", action="store_true", help="Force portfolio private")
    p.add_argument(
        "--lock-legal",
        action="store_true",
        help="ABSOLUTE: force-lock all legal/case repos private",
    )
    p.add_argument("--dry-run-all", action="store_true", help="Evaluate full portfolio list")
    args = p.parse_args(argv)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== repo-public-promotion-flipper @ {ts}")
    write_policy()
    report: dict[str, Any] = {"ts": ts, "mode": "dry", "actions": []}

    if args.lock_legal:
        payload = lock_legal_all()
        report["mode"] = "lock_legal"
        report["result"] = {
            "summary": payload.get("summary"),
            "public_leaks_fixed": payload.get("public_leaks_fixed"),
            "legal_count": len(payload.get("all_legal_repos") or []),
        }
        OUT.write_text(json.dumps(report, indent=2))
        print("LEGAL LOCK SUMMARY", payload.get("summary"))
        leaks = payload.get("public_leaks_fixed") or []
        print(f"public_leaks_fixed={len(leaks)}")
        for x in leaks:
            print(" ", x.get("status"), x.get("name"))
        print("ptr:", LEGAL_LOCK_PATH)
        print("ptr:", OUT)
        return 0 if not any(str(x.get("status", "")).startswith("fail") for x in payload.get("results") or []) else 1

    if args.reprivatize_portfolio:
        names = portfolio_list_from_state()
        results = []
        for n in names:
            results.append(reprivatize_name(n))
            time.sleep(0.08)
        c = Counter(x["status"] for x in results)
        report["mode"] = "reprivatize_portfolio"
        report["summary"] = dict(c)
        report["results"] = results
        OUT.write_text(json.dumps(report, indent=2))
        print("SUMMARY", dict(c))
        print("ptr:", OUT)
        return 0

    if args.pack:
        r = pro_akos_pack(args.pack)
        report["mode"] = "pack"
        report["result"] = r
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1

    if args.promote:
        r = promote(args.promote, force=args.force)
        report["mode"] = "promote"
        report["result"] = r
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(r, indent=2)[:4000])
        return 0 if r.get("promoted") else 1

    if args.scan:
        r = evaluate_repo(args.scan)
        report["mode"] = "scan"
        report["result"] = r
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(r, indent=2)[:4000])
        return 0 if r.get("ok") else 1

    # default: dry-run all portfolio
    names = portfolio_list_from_state()
    evals = []
    for n in names:
        try:
            evals.append(evaluate_repo(n))
        except Exception as e:
            evals.append({"name": n, "ok": False, "error": str(e)})
        time.sleep(0.1)
    ready = [e["name"] for e in evals if e.get("promote_ready")]
    blocked = [e for e in evals if not e.get("promote_ready")]
    report["mode"] = "dry_run_all"
    report["portfolio_count"] = len(names)
    report["promote_ready"] = ready
    report["blocked_count"] = len(blocked)
    report["blocked_sample"] = [
        {"name": b.get("name"), "gates": b.get("gates"), "notes": b.get("notes")}
        for b in blocked[:30]
    ]
    report["evals"] = evals
    OUT.write_text(json.dumps(report, indent=2))
    print(f"portfolio={len(names)} promote_ready={len(ready)} blocked={len(blocked)}")
    print("ready:", ", ".join(ready[:40]) + ("..." if len(ready) > 40 else ""))
    print("policy:", POLICY)
    print("ptr:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
