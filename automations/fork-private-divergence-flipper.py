#!/usr/bin/env python3
"""Fork → private for *significant divergence* only.

GitHub cannot privatize a public fork of a public upstream.
Path: rename fork → create private same-name non-fork → mirror push → archive old.

Smart tier (default --convert):
  - legal + ahead >= 1  OR
  - 3 <= ahead <= 80 AND size_kb < 200000
  - skip already converted / non-fork private

Uses existing scan: state/fork_divergence_scan_2026-07-12.json
  --rescan   rebuild scan (slow)
  --convert  convert smart tier
  --dry-run  list only

Token-saver: all large results → state JSON; stdout = counts only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OWNER = "GlacierEQ"
STATE = Path.home() / "GlacierEQ_Swarm" / "state"
SCAN = STATE / "fork_divergence_scan_2026-07-12.json"
OUT = STATE / "fork_private_conversion_last.json"
MIN_AHEAD, MAX_AHEAD, MAX_SIZE = 3, 80, 200_000


def token() -> str:
    for k in ("GITHUB_PRIMARY_TOKEN", "GITHUB_TOKEN_PRIMARY", "GITHUB_MASTER_TOKEN", "GITHUB_TOKEN"):
        v = os.environ.get(k)
        if v and len(v) > 20:
            return v
    raise SystemExit("no github token")


def api(method: str, path: str, data: Any = None, timeout: int = 90) -> tuple[int, Any]:
    url = "https://api.github.com" + path
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def run(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> tuple[int, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stderr or p.stdout or "")[-400:]


def smart_list(scan: dict) -> list[dict]:
    out = []
    for e in scan.get("qualified") or scan.get("all") or []:
        if not e.get("name"):
            continue
        ahead = e.get("ahead") or 0
        size = e.get("size_kb") or 0
        legal = e.get("is_legal")
        # legal: still cap ahead/size (avoid history-explode false positives)
        if legal and MIN_AHEAD <= ahead <= MAX_AHEAD and size < MAX_SIZE:
            out.append(e)
        elif (not legal) and MIN_AHEAD <= ahead <= MAX_AHEAD and size < MAX_SIZE:
            out.append(e)
    # dedupe by name
    seen = set()
    uniq = []
    for e in out:
        if e["name"] in seen:
            continue
        seen.add(e["name"])
        uniq.append(e)
    return sorted(uniq, key=lambda x: (-(x.get("ahead") or 0), x["name"]))


def convert_one(e: dict) -> dict:
    name = e["name"]
    archive = f"{name}__public_fork_archive"
    result: dict[str, Any] = {"name": name, "ahead": e.get("ahead"), "parent": e.get("parent"), "steps": []}

    st, existing = api("GET", f"/repos/{OWNER}/{name}")
    if st == 200 and existing.get("private") and not existing.get("fork"):
        result["status"] = "already_private_non_fork"
        return result
    if st != 200:
        result["status"] = f"missing_{st}"
        return result

    # rename public fork
    st, out = api("PATCH", f"/repos/{OWNER}/{name}", {"name": archive})
    result["steps"].append({"rename": st})
    if st not in (200, 201):
        archive = f"{name}__fork_archive_{int(time.time()) % 100000}"
        st, out = api("PATCH", f"/repos/{OWNER}/{name}", {"name": archive})
        result["steps"].append({"rename_retry": st, "to": archive})
        if st not in (200, 201):
            result["status"] = "rename_fail"
            result["err"] = out.get("error")
            return result

    time.sleep(0.4)

    st, created = api(
        "POST",
        "/user/repos",
        {
            "name": name,
            "private": True,
            "description": f"Private diverged from {e.get('parent')} (ahead={e.get('ahead')}) · converted 2026-07-12",
            "auto_init": False,
            "has_wiki": False,
        },
    )
    result["steps"].append({"create_private": st})
    if st not in (200, 201):
        result["status"] = "create_fail"
        result["archive_name"] = archive
        result["err"] = created.get("error")
        return result

    # Push heads+tags only — NOT --mirror (refs/pull/* rejected on non-forks → false push_fail)
    tmp = Path(tempfile.mkdtemp(prefix="forkpriv_"))
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        src = f"https://x-access-token:{token()}@github.com/{OWNER}/{archive}.git"
        dst = f"https://x-access-token:{token()}@github.com/{OWNER}/{name}.git"
        code, err = run(["git", "clone", "--bare", src, str(tmp / "m.git")], timeout=300)
        result["steps"].append({"clone": code})
        if code != 0:
            result["status"] = "clone_fail"
            result["archive_name"] = archive
            result["err"] = err
            return result
        # force heads + tags (ignore pull request refs)
        code1, err1 = run(
            ["git", "push", "--force", dst, "refs/heads/*:refs/heads/*"],
            cwd=str(tmp / "m.git"),
            timeout=300,
        )
        code2, err2 = run(
            ["git", "push", "--force", dst, "refs/tags/*:refs/tags/*"],
            cwd=str(tmp / "m.git"),
            timeout=120,
        )
        result["steps"].append({"push_heads": code1, "push_tags": code2})
        # success if heads push ok (tags optional / may be empty)
        if code1 != 0:
            # verify remote already has main (prior partial success)
            codev, outv = run(["git", "ls-remote", dst, "HEAD"], timeout=60)
            if codev != 0 or not outv.strip():
                result["status"] = "push_fail"
                result["archive_name"] = archive
                result["err"] = err1
                return result
            result["steps"].append({"recovered_via_ls_remote": True})
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["archive_name"] = archive
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    api(
        "PATCH",
        f"/repos/{OWNER}/{name}",
        {
            "private": True,
            "homepage": "https://github.com/GlacierEQ/AKOS",
        },
    )
    api(
        "PATCH",
        f"/repos/{OWNER}/{archive}",
        {
            "archived": True,
            "description": f"[ARCHIVED public fork] superseded by private {name}",
        },
    )

    stv, ver = api("GET", f"/repos/{OWNER}/{name}")
    ok = stv == 200 and ver.get("private") and not ver.get("fork")
    result["status"] = "converted_private" if ok else "verify_uncertain"
    result["private"] = ver.get("private")
    result["fork"] = ver.get("fork")
    result["archive_name"] = archive
    result["html"] = ver.get("html_url")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--convert", action="store_true")
    ap.add_argument("--limit", type=int, default=40, help="max converts this run")
    args = ap.parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"=== fork-private-divergence-flipper @ {ts}")

    if not SCAN.exists():
        print("error: missing scan", SCAN)
        return 1
    scan = json.loads(SCAN.read_text())
    smart = smart_list(scan)
    print(f"scan_qualify={scan.get('qualify_count')} smart_tier={len(smart)} limit={args.limit}")

    if args.dry_run or not args.convert:
        report = {
            "ts": ts,
            "mode": "dry_run",
            "smart_tier": len(smart),
            "names": [e["name"] for e in smart[: args.limit]],
            "criteria": {"min_ahead": MIN_AHEAD, "max_ahead": MAX_AHEAD, "max_size_kb": MAX_SIZE, "legal_ahead_ge": 1},
        }
        OUT.write_text(json.dumps(report, indent=2))
        print("dry names:", ", ".join(report["names"][:20]), ("..." if len(smart) > 20 else ""))
        print("ptr:", OUT)
        return 0

    batch = smart[: args.limit]
    results = []
    for i, e in enumerate(batch):
        print(f"[{i+1}/{len(batch)}] {e['name']} ahead={e.get('ahead')}", flush=True)
        try:
            r = convert_one(e)
        except Exception as ex:
            r = {"name": e["name"], "status": "exception", "err": str(ex)[:200]}
        results.append(r)
        print(" ->", r.get("status"), flush=True)
        time.sleep(0.25)

    summary = dict(Counter(r.get("status") for r in results))
    report = {
        "ts": ts,
        "mode": "convert_smart",
        "smart_tier_total": len(smart),
        "batch": len(batch),
        "summary": summary,
        "results": results,
        "scan_ptr": str(SCAN),
    }
    OUT.write_text(json.dumps(report, indent=2))
    # also durable dated
    (STATE / "fork_private_conversion_2026-07-12.json").write_text(json.dumps(report, indent=2))
    print("SUMMARY", summary)
    print("ptr:", OUT)
    return 0 if summary.get("converted_private", 0) + summary.get("already_private_non_fork", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
