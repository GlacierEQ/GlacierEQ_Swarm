#!/usr/bin/env python3
"""CRYSTALLIZATION-MANDATE estate executor.

A repository is terminal only when its intended purpose is fully modeled,
material capabilities are implemented and verified, runtime behavior is proven,
and deployment is real where deployment is part of the system's natural form.
Green CI alone is never a terminal state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

import estate_function_restorer as plumbing

OWNER = "GlacierEQ"
DEFAULT_ROOT = Path.home() / "crystallization-estate"
STATE_ROOT = Path.home() / "GlacierEQ_Swarm" / "state" / "crystallization"
MACHINE = Path("machine/crystallization")
OPEN_STATES = {"PARTIAL", "BROKEN", "MISSING", "UNKNOWN"}
VALID_STATES = {"WORKING", "PARTIAL", "BROKEN", "MISSING", "OBSOLETE", "UNKNOWN"}
DEPLOYABLE_KINDS = {
    "web_app",
    "web_service",
    "api_service",
    "worker",
    "daemon",
    "scheduled_service",
    "edge_function",
    "serverless",
    "mcp_server",
    "agent_service",
}
FAKE_COMPLETION_MARKERS = (
    "SCAFFOLD STUB",
    "This leaf is a **scaffold**",
    "## Current scaffold state",
    "Implementation is the next agent's job",
)


class RepoStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    UNDERSTOOD = "UNDERSTOOD"
    BROKEN = "BROKEN"
    INCOMPLETE = "INCOMPLETE"
    FUNCTIONAL = "FUNCTIONAL"
    COMPLETE = "COMPLETE"
    DEPLOYED = "DEPLOYED"
    CRYSTALLIZED = "CRYSTALLIZED"
    CANONICALIZED_SUCCESSOR = "CANONICALIZED_SUCCESSOR"
    INTENTIONALLY_ARCHIVED = "INTENTIONALLY_ARCHIVED"


@dataclass(frozen=True)
class RepoMeta:
    full_name: str
    name: str
    owner: str
    default_branch: str
    visibility: str
    archived: bool
    fork: bool
    disabled: bool
    description: str
    parent_full_name: str | None = None


@dataclass
class Result:
    repository: str
    status: str
    purpose: str = ""
    outcome: str = ""
    system_kind: str = ""
    branch: str | None = None
    capability_count: int = 0
    verified_capability_count: int = 0
    remaining_gap_count: int = 0
    build_result: str = "UNKNOWN"
    test_result: str = "UNKNOWN"
    runtime_result: str = "UNKNOWN"
    deployment_result: str = "UNKNOWN"
    source_sha: str | None = None
    unresolved_gaps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    proof_artifacts: list[str] = field(default_factory=list)
    pr_url: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    check: bool = False,
):
    return plumbing.run(argv, cwd=cwd, timeout=timeout, check=check)


def discover_accessible_repositories() -> list[RepoMeta]:
    plumbing.require_tool("gh")
    result = run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "/user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=full_name",
        ],
        timeout=180,
        check=True,
    )
    raw = json.loads(result.stdout)
    pages = raw if isinstance(raw, list) else []
    rows: list[dict[str, Any]] = []
    if pages and all(isinstance(page, list) for page in pages):
        for page in pages:
            rows.extend(page)
    else:
        rows = [row for row in pages if isinstance(row, dict)]

    seen: set[str] = set()
    repos: list[RepoMeta] = []
    for row in rows:
        full_name = str(row.get("full_name") or "").strip()
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        owner = str((row.get("owner") or {}).get("login") or full_name.split("/", 1)[0])
        parent = row.get("parent") or {}
        repos.append(
            RepoMeta(
                full_name=full_name,
                name=str(row.get("name") or full_name.split("/", 1)[-1]),
                owner=owner,
                default_branch=str(row.get("default_branch") or "main"),
                visibility=str(
                    row.get("visibility")
                    or ("private" if row.get("private") else "public")
                ),
                archived=bool(row.get("archived")),
                fork=bool(row.get("fork")),
                disabled=bool(row.get("disabled")),
                description=str(row.get("description") or ""),
                parent_full_name=str(parent.get("full_name"))
                if parent.get("full_name")
                else None,
            )
        )
    return repos


def clone_or_refresh(meta: RepoMeta, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "__", meta.full_name)
    repo = root / safe
    if repo.exists():
        if not (repo / ".git").is_dir():
            raise RuntimeError(f"not a git worktree: {repo}")
        dirty = run(["git", "status", "--porcelain"], cwd=repo, timeout=30, check=True)
        if dirty.stdout.strip():
            raise RuntimeError("dirty worktree; refusing to overwrite uncommitted work")
        run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=180, check=True)
    else:
        run(["gh", "repo", "clone", meta.full_name, str(repo)], timeout=600, check=True)
    run(["git", "checkout", meta.default_branch], cwd=repo, timeout=60, check=True)
    run(
        ["git", "reset", "--hard", f"origin/{meta.default_branch}"],
        cwd=repo,
        timeout=60,
        check=True,
    )
    return repo


def prepare_branch(repo: Path, meta: RepoMeta) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", meta.name)[:48]
    branch = f"crystallize/{stamp}-{slug}"
    existing = run(
        ["git", "branch", "--list", branch], cwd=repo, timeout=30, check=True
    )
    if existing.stdout.strip():
        run(["git", "branch", "-D", branch], cwd=repo, timeout=30, check=True)
    run(
        ["git", "checkout", "-b", branch, f"origin/{meta.default_branch}"],
        cwd=repo,
        timeout=60,
        check=True,
    )
    return branch


def evidence_paths(repo: Path) -> str:
    found: list[str] = []
    for rel in (
        "README.md",
        "ISSUE_CONTRACT.md",
        "TARGET_CONTRACT.md",
        "ARCHITECTURE.md",
        "DEV_UP_INSTRUCTIONS.md",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "Dockerfile",
        "vercel.json",
        "fly.toml",
        "supabase/config.toml",
        "netlify.toml",
    ):
        if (repo / rel).exists():
            found.append(rel)
    for rel in (
        "src",
        "app",
        "lib",
        "packages",
        "services",
        "tests",
        "docs",
        "scripts",
        "deploy",
        "infra",
        ".github/workflows",
        "machine",
    ):
        if (repo / rel).exists():
            found.append(rel + "/")
    return ", ".join(found) if found else "entire repository tree and git history"


def worker_prompt(meta: RepoMeta, repo: Path) -> str:
    return textwrap.dedent(
        f"""
        CRYSTALLIZATION-MANDATE repository work unit
        Repository: {meta.full_name}

        This is a CODEBASE METAMORPHOSIS, not cleanup, modernization, CI repair,
        gate repair, or documentation work.

        Recover intention from total evidence: {evidence_paths(repo)}, repository
        history, branches, package/API/schema shapes, tests, deployments, generated
        artifacts, and predecessor/successor clues. Do not reduce purpose to whatever
        compiles today.

        First create/update these machine manifests:

        machine/crystallization/purpose-manifest.json
        {{
          "schema":"glaciereq.crystallization-purpose.v1",
          "canonical_identity":"...",
          "purpose":"...",
          "problem":"...",
          "intended_outcome":"...",
          "consumers":["..."],
          "system_kind":"web_app|web_service|api_service|worker|daemon|scheduled_service|edge_function|serverless|mcp_server|agent_service|library|cli|binary|local_first_runtime|data_pipeline|ml_pipeline|research_tool|developer_tool|historical_artifact|other",
          "naturally_deployable":true,
          "lineage":{{"predecessors":[],"successors":[],"duplicates":[],"canonical_successor":null}},
          "evidence":["..."]
        }}

        machine/crystallization/capability-manifest.json
        {{
          "schema":"glaciereq.crystallization-capabilities.v1",
          "capabilities":[
            {{"id":"...","class":"...","description":"...","material":true,
              "state":"WORKING|PARTIAL|BROKEN|MISSING|OBSOLETE|UNKNOWN",
              "implementation_paths":["..."],"verification":["..."],"consumer":"..."}}
          ]
        }}

        machine/crystallization/gap-matrix.json
        {{"schema":"glaciereq.crystallization-gaps.v1","gaps":[
          {{"capability_id":"...","state":"MISSING","required_work":"..."}}
        ]}}

        Then implement every material PARTIAL/BROKEN/MISSING capability supportable in
        this work unit. Connect the real execution paths. Restore real integrations.
        Add persistence/auth/APIs/UI/search/retrieval/workers only where purpose demands
        them. Preserve strong code. Remove dead abstraction only when it obstructs
        function. Choose language by technical advantage, not fashion.

        Test purpose: happy path, material failures, integrations, end-to-end/runtime,
        recovery/adversarial behavior, and performance where material. Examples must
        execute. Documentation must match observed behavior.

        If naturally deployable, deployment is part of completion. Create/update
        machine/crystallization/deployment-receipt.json with status PASS only after a
        real deployment/release smoke succeeds; include target, artifact_or_endpoint,
        smoke_command, smoke_returncode, health_or_readiness, logs_or_run_reference,
        and rollback_mechanism. Do not fabricate a deployment receipt when credentials
        or infrastructure are unavailable. Leave the capability explicitly incomplete.

        Never write CRYSTALLIZED yourself. The outer executor owns terminal status.
        No placeholders, TODO core paths, fake APIs, fake integrations, mock-as-final,
        dead routes, undocumented required secrets, or README claims unsupported by the
        runtime. Green CI is evidence, not completion.
        """
    ).strip()


def invoke_worker(meta: RepoMeta, repo: Path, worker_cmd: str | None):
    prompt = worker_prompt(meta, repo)
    if worker_cmd:
        argv = worker_cmd.split() + [prompt]
    else:
        plumbing.require_tool("grok")
        argv = ["grok", "-p", prompt, "--yolo", "--sandbox", "workspace"]
    return run(argv, cwd=repo, timeout=7200, check=False)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


def validate_manifests(repo: Path):
    blockers: list[str] = []
    purpose_path = repo / MACHINE / "purpose-manifest.json"
    caps_path = repo / MACHINE / "capability-manifest.json"
    gaps_path = repo / MACHINE / "gap-matrix.json"
    if not purpose_path.is_file():
        return None, [], [], ["purpose_manifest_missing"]
    if not caps_path.is_file():
        return None, [], [], ["capability_manifest_missing"]
    if not gaps_path.is_file():
        return None, [], [], ["gap_matrix_missing"]
    try:
        purpose = load_json(purpose_path)
        cap_doc = load_json(caps_path)
        gap_doc = load_json(gaps_path)
    except Exception as exc:
        return None, [], [], [f"manifest_invalid:{exc}"]

    for key in (
        "canonical_identity",
        "purpose",
        "problem",
        "intended_outcome",
        "system_kind",
    ):
        if not isinstance(purpose.get(key), str) or not str(purpose[key]).strip():
            blockers.append(f"purpose_{key}_missing")
    if not isinstance(purpose.get("consumers"), list) or not purpose["consumers"]:
        blockers.append("purpose_consumers_missing")
    if not isinstance(purpose.get("evidence"), list) or not purpose["evidence"]:
        blockers.append("purpose_evidence_missing")
    if not isinstance(purpose.get("naturally_deployable"), bool):
        blockers.append("purpose_naturally_deployable_missing")
    if not isinstance(purpose.get("lineage"), dict):
        blockers.append("purpose_lineage_missing")

    capabilities = cap_doc.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        blockers.append("capability_inventory_empty")
        capabilities = []
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for idx, cap in enumerate(capabilities):
        if not isinstance(cap, dict):
            blockers.append(f"capability_{idx}_not_object")
            continue
        cap_id = str(cap.get("id") or "").strip()
        state = str(cap.get("state") or "").upper()
        if not cap_id:
            blockers.append(f"capability_{idx}_id_missing")
        elif cap_id in seen:
            blockers.append(f"capability_duplicate:{cap_id}")
        seen.add(cap_id)
        if state not in VALID_STATES:
            blockers.append(f"capability_state_invalid:{cap_id or idx}")
        if not isinstance(cap.get("material"), bool):
            blockers.append(f"capability_material_missing:{cap_id or idx}")
        if (
            not isinstance(cap.get("description"), str)
            or not cap.get("description", "").strip()
        ):
            blockers.append(f"capability_description_missing:{cap_id or idx}")
        cap = dict(cap)
        cap["state"] = state
        normalized.append(cap)

    gaps = gap_doc.get("gaps")
    if not isinstance(gaps, list):
        blockers.append("gap_list_missing")
        gaps = []
    listed: set[str] = set()
    for gap in gaps:
        if not isinstance(gap, dict):
            blockers.append("gap_not_object")
            continue
        cap_id = str(gap.get("capability_id") or "").strip()
        if cap_id:
            listed.add(cap_id)
        if (
            not isinstance(gap.get("required_work"), str)
            or not gap.get("required_work", "").strip()
        ):
            blockers.append(f"gap_required_work_missing:{cap_id or 'unknown'}")
    expected = {
        str(cap.get("id"))
        for cap in normalized
        if cap.get("material") is True and cap.get("state") in OPEN_STATES
    }
    if expected - listed:
        blockers.append("gap_matrix_omits:" + ",".join(sorted(expected - listed)))
    if listed - expected:
        blockers.append("gap_matrix_extra:" + ",".join(sorted(listed - expected)))
    return purpose, normalized, gaps, blockers


def scan_fake_completion(repo: Path) -> list[str]:
    hits: list[str] = []
    for rel in ("README.md", "DEV_UP_INSTRUCTIONS.md"):
        path = repo / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in FAKE_COMPLETION_MARKERS:
            if marker in text:
                hits.append(f"{rel}:{marker}")
    for base in ("src", "app", "lib", "services"):
        root = repo / base
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            if path.suffix.lower() not in {
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".rs",
                ".go",
                ".java",
                ".kt",
                ".sql",
            }:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in FAKE_COMPLETION_MARKERS:
                if marker in text:
                    hits.append(f"{path.relative_to(repo)}:{marker}")
    return hits[:100]


def execute(repo: Path, commands: Iterable[list[str]], timeout: int = 1800):
    records: list[dict[str, Any]] = []
    ok = True
    for command in commands:
        result = run(command, cwd=repo, timeout=timeout, check=False)
        records.append(
            {
                "argv": command,
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-2000:],
                "stderr_tail": result.stderr[-2000:],
            }
        )
        if result.returncode != 0:
            ok = False
            break
    return ok, records


def runtime_commands(repo: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    if (repo / "scripts/operate.py").exists():
        commands.append([sys.executable, "scripts/operate.py"])
    if (repo / "Makefile").exists() and shutil.which("make"):
        text = (repo / "Makefile").read_text(encoding="utf-8", errors="ignore")
        for target in ("smoke", "demo", "run"):
            if re.search(rf"(?m)^{target}\s*:", text):
                commands.append(["make", target])
                break
    if (repo / "package.json").exists() and shutil.which("npm"):
        try:
            package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts") or {}
            for name in ("smoke", "demo", "healthcheck"):
                if scripts.get(name):
                    commands.append(["npm", "run", name])
                    break
        except Exception:
            pass
    return commands


def deployment_proof(repo: Path, purpose: dict[str, Any]):
    naturally = (
        bool(purpose.get("naturally_deployable"))
        or str(purpose.get("system_kind")) in DEPLOYABLE_KINDS
    )
    if not naturally:
        return True, "NOT_APPLICABLE", [{"not_applicable": True}], []
    receipt_path = repo / MACHINE / "deployment-receipt.json"
    if not receipt_path.is_file():
        return False, "FAIL", [], ["deployment_receipt_missing"]
    try:
        receipt = load_json(receipt_path)
    except Exception as exc:
        return False, "FAIL", [], [f"deployment_receipt_invalid:{exc}"]
    blockers: list[str] = []
    if receipt.get("status") != "PASS":
        blockers.append("deployment_status_not_pass")
    for key in (
        "target",
        "artifact_or_endpoint",
        "smoke_command",
        "health_or_readiness",
        "logs_or_run_reference",
        "rollback_mechanism",
    ):
        if not receipt.get(key):
            blockers.append(f"deployment_{key}_missing")
    if receipt.get("smoke_returncode") != 0:
        blockers.append("deployment_smoke_failed")
    return not blockers, "PASS" if not blockers else "FAIL", [receipt], blockers


def resolve_nonactive(meta: RepoMeta, repo: Path) -> Result | None:
    path = repo / MACHINE / "purpose-manifest.json"
    if not path.is_file():
        return None
    try:
        purpose = load_json(path)
    except Exception:
        return None
    lineage = purpose.get("lineage") or {}
    successor = (
        lineage.get("canonical_successor") if isinstance(lineage, dict) else None
    )
    if isinstance(successor, str) and successor.strip():
        return Result(
            repository=meta.full_name,
            status=RepoStatus.CANONICALIZED_SUCCESSOR.value,
            purpose=str(purpose.get("purpose") or ""),
            outcome=str(purpose.get("intended_outcome") or ""),
            system_kind=str(purpose.get("system_kind") or ""),
            proof_artifacts=[str(path.relative_to(repo))],
        )
    reason = purpose.get("intentional_archive_reason")
    if meta.archived and isinstance(reason, str) and reason.strip():
        return Result(
            repository=meta.full_name,
            status=RepoStatus.INTENTIONALLY_ARCHIVED.value,
            purpose=str(purpose.get("purpose") or ""),
            outcome=str(purpose.get("intended_outcome") or ""),
            system_kind=str(purpose.get("system_kind") or "historical_artifact"),
            proof_artifacts=[str(path.relative_to(repo))],
        )
    return None


def classify(
    *, purpose, capabilities, gaps, blockers, test_ok, build_ok, runtime_ok, deploy_ok
):
    if purpose is None:
        return RepoStatus.UNKNOWN
    material = [cap for cap in capabilities if cap.get("material") is True]
    if not material:
        return RepoStatus.UNDERSTOOD
    states = {cap.get("state") for cap in material}
    if "BROKEN" in states or not test_ok:
        return RepoStatus.BROKEN
    if states.intersection(OPEN_STATES) or gaps:
        return RepoStatus.INCOMPLETE
    if not build_ok or not runtime_ok:
        return RepoStatus.INCOMPLETE
    if not deploy_ok:
        return RepoStatus.COMPLETE
    if blockers:
        return RepoStatus.INCOMPLETE
    return RepoStatus.CRYSTALLIZED


def write_completion_receipt(
    repo: Path, result: Result, evidence: dict[str, Any]
) -> str:
    target = repo / MACHINE
    target.mkdir(parents=True, exist_ok=True)
    path = target / "completion-receipt.json"
    payload = {
        "schema": "glaciereq.crystallization-completion.v1",
        "mandate": "CRYSTALLIZATION-MANDATE@1.0",
        "repository_id": result.repository,
        "canonical_purpose": result.purpose,
        "intended_outcome": result.outcome,
        "system_kind": result.system_kind,
        "capability_count": result.capability_count,
        "verified_capability_count": result.verified_capability_count,
        "test_receipts": evidence["tests"],
        "build_receipts": evidence["build"],
        "runtime_receipts": evidence["runtime"],
        "deployment_receipts": evidence["deployment"],
        "unresolved_gaps": result.unresolved_gaps,
        "blockers": result.blockers,
        "source_sha": result.source_sha,
        "final_status": result.status,
        "terminal_truth": result.status == RepoStatus.CRYSTALLIZED.value,
        "verified_at": now(),
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return str(path.relative_to(repo))


def create_pr(repo: Path, meta: RepoMeta, branch: str, result: Result):
    body = textwrap.dedent(
        f"""
        CRYSTALLIZATION-MANDATE work unit.

        Current independently verified status: **{result.status}**
        Purpose: {result.purpose or "unresolved"}
        Verified material capabilities: {result.verified_capability_count}/{result.capability_count}
        Remaining material gaps: {result.remaining_gap_count}

        This PR is not complete merely because CI is green. CRYSTALLIZED requires
        purpose reconstruction, exhaustive material capability accounting, integrated
        runtime execution, real deployment where natural, truthful documentation, and
        zero unresolved material gaps.
        """
    ).strip()
    created = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            meta.full_name,
            "--base",
            meta.default_branch,
            "--head",
            branch,
            "--title",
            f"Crystallize {meta.name}: purpose-complete system",
            "--body",
            body,
        ],
        cwd=repo,
        timeout=120,
        check=False,
    )
    if created.returncode != 0:
        return None
    for line in created.stdout.splitlines():
        if line.startswith("https://github.com/"):
            return line.strip()
    return created.stdout.strip() or None


def crystallize_one(
    meta: RepoMeta, *, root: Path, worker_cmd: str | None, push: bool, open_pr: bool
) -> Result:
    branch: str | None = None
    try:
        repo = clone_or_refresh(meta, root)
        resolved = resolve_nonactive(meta, repo)
        if resolved:
            return resolved
        if meta.archived or meta.disabled:
            return Result(
                repository=meta.full_name,
                status=RepoStatus.UNKNOWN.value,
                blockers=[
                    "archived_or_disabled_without_verified_successor_or_archive_reason"
                ],
            )

        branch = prepare_branch(repo, meta)
        worker = invoke_worker(meta, repo, worker_cmd)
        if worker.returncode != 0:
            return Result(
                repository=meta.full_name,
                status=RepoStatus.BROKEN.value,
                branch=branch,
                blockers=[f"worker_exit:{worker.returncode}"],
            )

        purpose, capabilities, gaps, blockers = validate_manifests(repo)
        blockers.extend(f"fake_completion:{hit}" for hit in scan_fake_completion(repo))

        test_commands = plumbing.infer_test_commands(repo)
        build_commands = plumbing.infer_build_commands(repo)
        run_commands = runtime_commands(repo)
        test_ok, test_records = (
            execute(repo, test_commands) if test_commands else (False, [])
        )
        build_ok, build_records = (
            execute(repo, build_commands) if build_commands else (False, [])
        )
        runtime_ok, runtime_records = (
            execute(repo, run_commands) if run_commands else (False, [])
        )
        if not test_commands:
            blockers.append("behavior_test_command_missing")
        if not build_commands:
            blockers.append("reproducible_build_command_missing")
        if not run_commands:
            blockers.append("primary_runtime_command_missing")
        if not test_ok:
            blockers.append("behavior_tests_failed")
        if not build_ok:
            blockers.append("build_failed")
        if not runtime_ok:
            blockers.append("runtime_failed")

        if purpose is None:
            deploy_ok, deploy_result, deploy_records, deploy_blockers = (
                False,
                "UNKNOWN",
                [],
                ["deployment_applicability_unknown"],
            )
        else:
            deploy_ok, deploy_result, deploy_records, deploy_blockers = (
                deployment_proof(repo, purpose)
            )
        blockers.extend(deploy_blockers)

        material = [cap for cap in capabilities if cap.get("material") is True]
        open_ids = {
            str(cap.get("id")) for cap in material if cap.get("state") in OPEN_STATES
        }
        unresolved = sorted(open_ids)
        verified = [
            cap
            for cap in material
            if cap.get("state") == "WORKING"
            and isinstance(cap.get("verification"), list)
            and bool(cap.get("verification"))
        ]
        open_gaps = (
            [gap for gap in gaps if str(gap.get("capability_id")) in open_ids]
            if gaps
            else []
        )

        status = classify(
            purpose=purpose,
            capabilities=capabilities,
            gaps=open_gaps,
            blockers=blockers,
            test_ok=test_ok,
            build_ok=build_ok,
            runtime_ok=runtime_ok,
            deploy_ok=deploy_ok,
        )
        result = Result(
            repository=meta.full_name,
            status=status.value,
            purpose=str((purpose or {}).get("purpose") or ""),
            outcome=str((purpose or {}).get("intended_outcome") or ""),
            system_kind=str((purpose or {}).get("system_kind") or ""),
            branch=branch,
            capability_count=len(material),
            verified_capability_count=len(verified),
            remaining_gap_count=len(unresolved),
            build_result="PASS" if build_ok else "FAIL",
            test_result="PASS" if test_ok else "FAIL",
            runtime_result="PASS" if runtime_ok else "FAIL",
            deployment_result=deploy_result,
            source_sha=plumbing.source_tree_sha(repo),
            unresolved_gaps=unresolved,
            blockers=sorted(set(blockers)),
            proof_artifacts=[
                str(MACHINE / "purpose-manifest.json"),
                str(MACHINE / "capability-manifest.json"),
                str(MACHINE / "gap-matrix.json"),
            ],
        )
        receipt = write_completion_receipt(
            repo,
            result,
            {
                "tests": test_records,
                "build": build_records,
                "runtime": runtime_records,
                "deployment": deploy_records,
            },
        )
        result.proof_artifacts.append(receipt)

        changed = plumbing.changed_files(repo)
        if changed:
            run(["git", "add", "-A"], cwd=repo, timeout=60, check=True)
            run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"crystallize: {meta.name} purpose-complete work unit",
                ],
                cwd=repo,
                timeout=180,
                check=True,
            )
            if push:
                run(
                    ["git", "push", "-u", "origin", branch, "--force-with-lease"],
                    cwd=repo,
                    timeout=600,
                    check=True,
                )
                if open_pr:
                    result.pr_url = create_pr(repo, meta, branch, result)
        return result
    except Exception as exc:
        return Result(
            repository=meta.full_name,
            status=RepoStatus.BROKEN.value,
            branch=branch,
            blockers=[repr(exc)],
        )


def save_ledger(results: list[Result]) -> Path:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    resolved = {
        RepoStatus.CRYSTALLIZED.value,
        RepoStatus.CANONICALIZED_SUCCESSOR.value,
        RepoStatus.INTENTIONALLY_ARCHIVED.value,
    }
    payload = {
        "schema": "glaciereq.crystallization-estate-ledger.v1",
        "mandate": "CRYSTALLIZATION-MANDATE@1.0",
        "generated_at": now(),
        "total_repositories": len(results),
        "crystallized_repositories": counts.get(RepoStatus.CRYSTALLIZED.value, 0),
        "canonicalized_successors": counts.get(
            RepoStatus.CANONICALIZED_SUCCESSOR.value, 0
        ),
        "intentionally_archived": counts.get(
            RepoStatus.INTENTIONALLY_ARCHIVED.value, 0
        ),
        "broken_remaining": counts.get(RepoStatus.BROKEN.value, 0),
        "incomplete_remaining": counts.get(RepoStatus.INCOMPLETE.value, 0),
        "unknown_remaining": counts.get(RepoStatus.UNKNOWN.value, 0),
        "verified_capabilities": sum(
            result.verified_capability_count for result in results
        ),
        "unresolved_failures": [
            {
                "repository": result.repository,
                "status": result.status,
                "blockers": result.blockers,
                "gaps": result.unresolved_gaps,
            }
            for result in results
            if result.status not in resolved
        ],
        "results": [asdict(result) for result in results],
    }
    payload["estate_complete"] = all(result.status in resolved for result in results)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = STATE_ROOT / f"ledger-{stamp}.json"
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")
    (STATE_ROOT / "latest.json").write_text(rendered, encoding="utf-8")
    return path


def load_priority(path: Path | None) -> set[str]:
    if path is None:
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = (
        raw
        if isinstance(raw, list)
        else raw.get("repositories", [])
        if isinstance(raw, dict)
        else []
    )
    out: set[str] = set()
    for item in items:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict):
            value = item.get("repository") or item.get("repo") or item.get("name")
            if isinstance(value, str):
                out.add(value)
    return out


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Crystallize every accessible repository by purpose"
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--priority-file", type=Path)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="logistics only; 0 means all selected repositories",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--worker-cmd", default=os.environ.get("CRYSTALLIZATION_WORKER_CMD")
    )
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--no-pr", action="store_true")
    parser.add_argument("--list", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    priority = load_priority(args.priority_file)
    repos = discover_accessible_repositories()
    if args.repo:
        wanted = set(args.repo)
        repos = [
            repo for repo in repos if repo.full_name in wanted or repo.name in wanted
        ]
    repos.sort(
        key=lambda repo: (
            0 if repo.full_name in priority or repo.name in priority else 1,
            repo.full_name.lower(),
        )
    )
    if args.limit > 0:
        repos = repos[: args.limit]
    if args.list:
        print(json.dumps([asdict(repo) for repo in repos], indent=2))
        return 0
    if not repos:
        print("no repositories selected", file=sys.stderr)
        return 2

    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                crystallize_one,
                repo,
                root=args.root,
                worker_cmd=args.worker_cmd,
                push=not args.no_push,
                open_pr=not args.no_pr,
            ): repo
            for repo in repos
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"[{result.status}] {result.repository} verified={result.verified_capability_count}/{result.capability_count} gaps={result.remaining_gap_count}",
                flush=True,
            )

    results.sort(key=lambda item: item.repository.lower())
    ledger = save_ledger(results)
    resolved = {
        RepoStatus.CRYSTALLIZED.value,
        RepoStatus.CANONICALIZED_SUCCESSOR.value,
        RepoStatus.INTENTIONALLY_ARCHIVED.value,
    }
    unresolved = [result for result in results if result.status not in resolved]
    print(
        f"crystallized={sum(result.status == RepoStatus.CRYSTALLIZED.value for result in results)}/{len(results)}"
    )
    print(f"unresolved={len(unresolved)}")
    print(f"ledger={ledger}")
    return 0 if not unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
