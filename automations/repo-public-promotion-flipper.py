#!/usr/bin/env python3
"""Repository visibility readiness + explicit visibility action tool.

This module separates three things that the previous implementation conflated:

1. security/readiness evidence;
2. technical permission to change GitHub state; and
3. project-direction authority.

Project direction remains with the Operator. A category, AKOS pointer, CI result,
receipt, manifest, score, or technical admin permission never authorizes a repository
visibility/lifecycle decision by itself.

Default mode is read-only inventory. Visibility changes occur only through an explicit
CLI action. AKOS compatibility metadata is optional and never a promotion gate.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE = Path.home() / "GlacierEQ_Swarm" / "state"
OUT = STATE / "repo_visibility_readiness_last.json"
POLICY = STATE / "repo_visibility_policy.json"
SENSITIVE_ACTION_LOG = STATE / "sensitive_visibility_action_last.json"
OWNER = "GlacierEQ"

# Risk classifier only. Matching this expression does not create project authority.
SENSITIVE_RE = re.compile(
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

PORTFOLIO_PREFIX_HINTS = ("xai-colossus", "colossus-", "spacex-", "apex-")
PORTFOLIO_EXACT_HINTS = {
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

# Optional compatibility bridge. It does not appoint AKOS as owner/governor/truth root.
AKOS_MD = """# AKOS Compatibility / Provenance Bridge

Project direction: **OPERATOR**.

AKOS is an optional architecture, cognition, composition, and verification peer for
this repository. This pointer does not make AKOS the source of the repository's
identity, lifecycle, priority, visibility, roadmap, product direction, or truth.

- AKOS manifests, CI, receipts, topology, maturity, selection, or persistence do not
  create project authority.
- Shared AKOS / Pro-Code / ECHO references may be reused when they strengthen the
  Operator-selected objective.
- Repository-specific implementation and evidence remain local to their proper sources.
- Historical central-bridge, canonical-truth, or `do not fork truth` language is
  provenance only, not current policy.

AKOS reference: https://github.com/GlacierEQ/AKOS

`compatibility != control` · `selection != ownership` · `persistence != authority`
"""


def token() -> str:
    for key in (
        "GITHUB_PRIMARY_TOKEN",
        "GITHUB_TOKEN_PRIMARY",
        "GITHUB_MASTER_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        value = os.environ.get(key)
        if value and len(value) > 20:
            return value
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
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode() or "{}"
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode()[:800]}


def is_sensitive(name: str, description: str = "") -> bool:
    """Return a disclosure-risk hint, not a project disposition."""
    return bool(SENSITIVE_RE.search(f"{name} {description or ''}"))


def portfolio_hint(name: str) -> bool:
    lowered = name.lower()
    return name in PORTFOLIO_EXACT_HINTS or any(
        lowered.startswith(prefix) for prefix in PORTFOLIO_PREFIX_HINTS
    )


def get_file(repo: str, path: str) -> dict[str, Any] | None:
    status, data = api("GET", f"/repos/{OWNER}/{repo}/contents/{path}")
    if status == 200 and isinstance(data, dict) and data.get("sha"):
        return data
    return None


def put_file(
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str | None = None,
) -> tuple[int, Any]:
    status, meta = api("GET", f"/repos/{OWNER}/{repo}")
    if status != 200:
        return status, meta
    target_branch = branch or meta.get("default_branch") or "main"
    existing = get_file(repo, path)
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": target_branch,
    }
    if existing:
        payload["sha"] = existing["sha"]
    return api("PUT", f"/repos/{OWNER}/{repo}/contents/{path}", payload)


def scan_tree_for_secrets(repo: str, max_files: int = 60) -> list[str]:
    """Bounded secret-pattern scan used as readiness evidence."""
    hits: list[str] = []
    status, meta = api("GET", f"/repos/{OWNER}/{repo}")
    if status != 200:
        return [f"repo_fetch_fail:{status}"]
    branch = meta.get("default_branch") or "main"
    status, ref = api("GET", f"/repos/{OWNER}/{repo}/git/ref/heads/{branch}")
    if status != 200:
        return [f"ref_fetch_fail:{status}"]
    sha = ref.get("object", {}).get("sha")
    status, tree = api("GET", f"/repos/{OWNER}/{repo}/git/trees/{sha}?recursive=1")
    if status != 200:
        return [f"tree_fetch_fail:{status}"]

    ranked: list[tuple[int, str]] = []
    for row in tree.get("tree", []):
        path = row.get("path", "")
        if row.get("type") != "blob" or path.startswith(".git"):
            continue
        lowered = path.lower()
        if any(
            lowered.endswith(ext)
            for ext in (".png", ".jpg", ".jpeg", ".pdf", ".zip", ".lock")
        ):
            continue
        score = 0
        if any(
            x in lowered for x in (".env", "secret", "credential", "token", "config")
        ):
            score += 10
        if lowered.endswith(
            (".py", ".ts", ".js", ".md", ".json", ".toml", ".yml", ".yaml", ".sh")
        ):
            score += 3
        ranked.append((score, path))

    ranked.sort(reverse=True)
    for _, path in ranked[:max_files]:
        item = get_file(repo, path)
        if not item or item.get("encoding") != "base64":
            continue
        try:
            text = base64.b64decode(item["content"]).decode("utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path}: pattern {pattern.pattern[:40]}")
                break
        time.sleep(0.03)
    return hits


def evaluate_repo(name: str) -> dict[str, Any]:
    status, repo = api("GET", f"/repos/{OWNER}/{name}")
    if status != 200:
        return {"name": name, "observed": False, "reason": f"missing_{status}"}

    description = repo.get("description") or ""
    secret_hits = scan_tree_for_secrets(name)
    readme = get_file(name, "README.md") or get_file(name, "readme.md")
    bridge = get_file(name, "AKOS.md")
    sensitive = is_sensitive(name, description)

    readiness = {
        "not_fork": not bool(repo.get("fork")),
        "has_readme": readme is not None,
        "secret_scan_clean": not secret_hits,
        "sensitive_content_risk_hint": sensitive,
        "portfolio_hint": portfolio_hint(name),
        "akos_bridge_present": bridge is not None,
    }
    public_readiness = (
        readiness["not_fork"]
        and readiness["has_readme"]
        and readiness["secret_scan_clean"]
        and not sensitive
    )
    return {
        "name": name,
        "observed": True,
        "private": bool(repo.get("private")),
        "visibility": repo.get("visibility"),
        "default_branch": repo.get("default_branch"),
        "html": repo.get("html_url"),
        "project_direction_authority": "OPERATOR",
        "technical_permission_is_project_direction": False,
        "readiness": readiness,
        "secret_hits": secret_hits[:20],
        "public_readiness": public_readiness,
        "readiness_semantics": "EVIDENCE_ONLY_NOT_VISIBILITY_AUTHORIZATION",
    }


def compatibility_pack(name: str) -> dict[str, Any]:
    """Write only a non-authoritative AKOS compatibility bridge.

    This function deliberately does not change repository visibility or homepage.
    """
    status, output = put_file(
        name,
        "AKOS.md",
        AKOS_MD,
        "docs: non-authoritative AKOS compatibility bridge",
    )
    return {
        "name": name,
        "ok": status in (200, 201),
        "status": status,
        "visibility_changed": False,
        "homepage_changed": False,
        "project_authority_created": False,
        "output": output if status not in (200, 201) else None,
    }


def promote(name: str, force: bool = False) -> dict[str, Any]:
    """Execute an explicit public-visibility request for a non-sensitive repository."""
    report = evaluate_repo(name)
    if not report.get("observed"):
        return {
            "name": name,
            "promoted": False,
            "reason": "repository_unavailable",
            "eval": report,
        }
    if report["readiness"]["sensitive_content_risk_hint"]:
        return {
            "name": name,
            "promoted": False,
            "reason": "sensitive_repository_requires_dedicated_disclosure_review",
            "eval": report,
            "force_ignored": bool(force),
        }
    if report["secret_hits"]:
        return {
            "name": name,
            "promoted": False,
            "reason": "secret_scan_not_clean",
            "eval": report,
            "force_ignored": bool(force),
        }
    if not report["public_readiness"] and not force:
        return {
            "name": name,
            "promoted": False,
            "reason": "readiness_checks_failed",
            "eval": report,
        }

    status, output = api("PATCH", f"/repos/{OWNER}/{name}", {"private": False})
    ok = status in (200, 201) and output.get("private") is False
    return {
        "name": name,
        "promoted": ok,
        "status": status,
        "eval": report,
        "authority_basis": "EXPLICIT_CLI_ACTION",
        "akos_gate_used": False,
    }


def set_private(name: str) -> dict[str, Any]:
    status, repo = api("GET", f"/repos/{OWNER}/{name}")
    if status != 200:
        return {"name": name, "status": f"missing_{status}"}
    if repo.get("private"):
        return {"name": name, "status": "already_private"}
    status, output = api("PATCH", f"/repos/{OWNER}/{name}", {"private": True})
    ok = status in (200, 201) and output.get("private") is True
    return {"name": name, "status": "set_private" if ok else f"fail_{status}"}


def lock_sensitive_all() -> dict[str, Any]:
    """Compatibility command for explicit `--lock-legal` invocation.

    The regex is a safety selector only. The action is authorized by the explicit CLI
    invocation, not by the selector, AKOS, or persisted policy.
    """
    page = 1
    repos: list[dict[str, Any]] = []
    while page <= 40:
        status, batch = api(
            "GET",
            f"/user/repos?per_page=100&page={page}&affiliation=owner&sort=full_name",
        )
        if status != 200 or not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    results = []
    for repo in repos:
        if repo.get("owner", {}).get("login") != OWNER:
            continue
        name = repo["name"]
        if not is_sensitive(name, repo.get("description") or ""):
            continue
        results.append(set_private(name))
        time.sleep(0.05)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "explicit_sensitive_set_private",
        "project_direction_authority": "OPERATOR",
        "selector_semantics": "RISK_SELECTOR_ONLY",
        "summary": dict(Counter(row["status"] for row in results)),
        "results": results,
    }
    SENSITIVE_ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    SENSITIVE_ACTION_LOG.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def portfolio_list_from_state() -> list[str]:
    names: set[str] = set(PORTFOLIO_EXACT_HINTS)
    slim = STATE / "ultimate_repo_map_slim.json"
    if slim.exists():
        try:
            data = json.loads(slim.read_text(encoding="utf-8"))
            for block in data.get("categories", {}).values():
                for name in block.get("repos", []):
                    if isinstance(name, str):
                        names.add(name)
        except (OSError, ValueError, TypeError):
            pass
    return sorted(names)


def reprivatize_portfolio() -> dict[str, Any]:
    """Execute only when explicitly requested with `--reprivatize-portfolio`."""
    results = [set_private(name) for name in portfolio_list_from_state()]
    return {
        "action": "explicit_reprivatize_portfolio",
        "project_direction_authority": "OPERATOR",
        "results": results,
        "summary": dict(Counter(row["status"] for row in results)),
    }


def write_policy() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    policy = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "project_direction_authority": "OPERATOR",
        "machine_visibility_authority": False,
        "technical_permission_is_project_direction": False,
        "readiness_is_visibility_authorization": False,
        "akos_bridge_required_for_legitimacy": False,
        "akos_homepage_required": False,
        "default_operation": "READ_ONLY_READINESS_EVALUATION",
        "safety_defaults": {
            "private_first": True,
            "secret_scan_before_public": True,
            "sensitive_repository_requires_dedicated_disclosure_review": True,
        },
        "visibility_change_requires": "EXPLICIT_CLI_ACTION_WITH_AVAILABLE_TECHNICAL_PERMISSION",
        "invariants": [
            "inventory != authority",
            "technical_permission != project_direction",
            "visibility_readiness != visibility_authorization",
            "compatibility != control",
            "persistence != authority",
        ],
    }
    POLICY.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repository visibility readiness and explicit action tool"
    )
    parser.add_argument(
        "--scan", metavar="REPO", help="Read-only evaluation of one repository"
    )
    parser.add_argument(
        "--promote",
        metavar="REPO",
        help="Explicitly make one non-sensitive repository public if safety checks pass",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override non-security readiness gaps; never overrides secret or sensitive-data safety",
    )
    parser.add_argument(
        "--pack",
        metavar="REPO",
        help="Write optional non-authoritative AKOS compatibility bridge only",
    )
    parser.add_argument(
        "--reprivatize-portfolio",
        action="store_true",
        help="Explicitly set the current portfolio list private",
    )
    parser.add_argument(
        "--lock-legal",
        action="store_true",
        help="Compatibility alias: explicitly set repositories matching the sensitive-risk selector private",
    )
    parser.add_argument(
        "--dry-run-all",
        action="store_true",
        help="Read-only evaluation of current portfolio list",
    )
    args = parser.parse_args(argv)

    write_policy()
    report: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "project_direction_authority": "OPERATOR",
    }

    if args.scan:
        report.update({"mode": "scan", "result": evaluate_repo(args.scan)})
    elif args.pack:
        report.update(
            {"mode": "compatibility_pack", "result": compatibility_pack(args.pack)}
        )
    elif args.promote:
        report.update(
            {
                "mode": "explicit_promote",
                "result": promote(args.promote, force=args.force),
            }
        )
    elif args.reprivatize_portfolio:
        report.update(
            {"mode": "explicit_reprivatize", "result": reprivatize_portfolio()}
        )
    elif args.lock_legal:
        report.update(
            {"mode": "explicit_sensitive_set_private", "result": lock_sensitive_all()}
        )
    else:
        names = portfolio_list_from_state()
        evals = []
        for name in names:
            try:
                evals.append(evaluate_repo(name))
            except Exception as exc:
                evals.append({"name": name, "observed": False, "error": str(exc)})
            time.sleep(0.05)
        report.update(
            {
                "mode": "dry_run_all",
                "portfolio_count": len(names),
                "public_ready": [
                    row["name"] for row in evals if row.get("public_readiness")
                ],
                "evals": evals,
            }
        )

    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
