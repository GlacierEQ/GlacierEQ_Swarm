#!/usr/bin/env python3
"""CRYSTALLIZATION-MANDATE estate executor.

This is the controlling estate metamorphosis lane. It does not treat a green
build, passing tests, a proof receipt, or a repaired central mechanism as
completion. A repository can be marked CRYSTALLIZED only when its purpose is
reconstructed, material capabilities are explicitly enumerated, no material
capability remains missing/broken/partial/unknown, primary execution is proven,
integrations are real, deployment is proven where deployment is natural, and
documentation matches observed behavior.

Legacy restoration helpers are reused only as plumbing for checkout, command
execution, hashing, test/build discovery, and isolated-branch handling. Their
old terminal status is not authoritative here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Compatibility plumbing only. Crystallization semantics live in this module.
import estate_function_restorer as plumbing

OWNER = "GlacierEQ"
DEFAULT_ROOT = Path.home() / "crystallization-estate"
STATE_ROOT = Path.home() / "GlacierEQ_Swarm" / "state" / "crystallization"
MACHINE_DIR = Path("machine/crystallization")
MANDATE_PATH = Path("governance/CRYSTALLIZATION_MANDATE.yaml")
CAPABILITY_STATES = {"WORKING", "PARTIAL", "BROKEN", "MISSING", "OBSOLETE", "UNKNOWN"}
MATERIAL_OPEN_STATES = {"PARTIAL", "BROKEN", "MISSING", "UNKNOWN"}
NATURAL_DEPLOY_KINDS = {
    "web_app", "web_service", "api_service", "worker", "daemon", "scheduled_service",
    "edge_function", "serverless", "mcp_server", "agent_service",
}
RUNTIME_ARTIFACT_KINDS = {
    "library", "cli", "binary", "local_first_runtime", "data_pipeline", "ml_pipeline",
    "research_tool", "developer_tool",
}
PROHIBITED_COMPLETION_MARKERS = (
    "SCAFFOLD STUB", "This leaf is a **scaffold**", "## Current scaffold state",
    "Implementation is the next agent's job", "TODO_AS_IMPLEMENTATION",
)


class RepoStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    DISCOVERED = "DISCOVERED"
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
class EstateRepo:
    full_name: str
    name: str
    owner: str
    default_branch: str
    visibility: str
    archived: bool
    fork: bool
    disabled: bool
    description: str
    pushed_at: str
    parent_full_name: str | None = None


@dataclass
class VerificationEvidence:
    build: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    runtime: list[dict[str, Any]] = field(default_factory=list)
    deployment: list[dict[str, Any]] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CrystallizationResult:
    repository: str
    status: str
    canonical_identity: str | None = None
    purpose: str | None = None
    outcome: str | None = None
    system_kind: str | None = None
    capability_count: int = 0
    implemented_capability_count: int = 0
    verified_capability_count: int = 0
    remaining_gap_count: int = 0
    branch: str | None = None
    source_sha: str | None = None
    proof_artifacts: list[str] = field(default_factory=list)
    build_result: str | None = None
    test_result: str | None = None
    runtime_result: str | None = None
    deployment_result: str | None = None
    unresolved_gaps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    pr_url: str | None = None
    worker_tail: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(argv: Sequence[str], *, cwd: Path | None = None, timeout: int = 900, check: bool = False) -> plumbing.CommandResult:
    return plumbing.run(argv, cwd=cwd, timeout=timeout, check=check)


def require_tool(name: str) -> str:
    return plumbing.require_tool(name)


def discover_accessible_repositories() -> list[EstateRepo]:
    """Enumerate every repository visible to the authenticated GitHub identity."""
    require_tool("gh")
    result = run(
        [
            "gh", "api", "--paginate", "--slurp",
            "/user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=full_name",
        ],
        timeout=180,
        check=True,
    )
    payload = json.loads(result.stdout)
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list) and payload and all(isinstance(item, list) for item in payload):
        for page in payload:
            rows.extend(page)
    elif isinstance(payload, list):
        rows = payload
    else:
        raise RuntimeError("GitHub repository enumeration returned non-list payload")

    repos: list[EstateRepo] = []
    seen: set[str] = set()
    for row in rows:
        full_name = str(row.get("full_name") or "").strip()
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        owner = str((row.get("owner") or {}).get("login") or full_name.split("/", 1)[0])
        parent = row.get("parent") or {}
        repos.append(
            EstateRepo(
                full_name=full_name,
                name=str(row.get("name") or full_name.split("/", 1)[-1]),
                owner=owner,
                default_branch=str(row.get("default_branch") or "main"),
                visibility=str(row.get("visibility") or ("private" if row.get("private") else "public")),
                archived=bool(row.get("archived")),
                fork=bool(row.get("fork")),
                disabled=bool(row.get("disabled")),
                description=str(row.get("description") or ""),
                pushed_at=str(row.get("pushed_at") or ""),
                parent_full_name=str(parent.get("full_name")) if parent.get("full_name") else None,
            )
        )
    return repos


def target_priority(repo: EstateRepo, explicit: set[str]) -> tuple[int, str]:
    score = 0
    if repo.full_name in explicit or repo.name in explicit:
        score += 100_000
    if repo.owner == OWNER:
        score += 10_000
    if not repo.archived and not repo.disabled:
        score += 2_000
    if repo.visibility.lower() == "public":
        score += 500
    if repo.description:
        score += 50
    return (-score, repo.full_name.lower())


def clone_or_refresh(repo_meta: EstateRepo, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "__", repo_meta.full_name)
    repo = root / safe
    if repo.exists():
        if not (repo / ".git").is_dir():
            raise RuntimeError(f"existing path is not a git repository: {repo}")
        dirty = run(["git", "status", "--porcelain"], cwd=repo, timeout=30, check=True)
        if dirty.stdout.strip():
            raise RuntimeError("local worktree dirty; refusing to overwrite uncommitted work")
        run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=180, check=True)
    else:
        run(["gh", "repo", "clone", repo_meta.full_name, str(repo)], timeout=600, check=True)
    run(["git", "checkout", repo_meta.default_branch], cwd=repo, timeout=60, check=True)
    run(["git", "reset", "--hard", f"origin/{repo_meta.default_branch}"], cwd=repo, timeout=60, check=True)
    return repo


def branch_name(repo_meta: EstateRepo) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_meta.name)[:48]
    return f"crystallize/{stamp}-{slug}"


def prepare_branch(repo: Path, repo_meta: EstateRepo) -> str:
    branch = branch_name(repo_meta)
    existing = run(["git", "branch", "--list", branch], cwd=repo, timeout=30, check=True)
    if existing.stdout.strip():
        run(["git", "branch", "-D", branch], cwd=repo, timeout=30, check=True)
    run(["git", "checkout", "-b", branch, f"origin/{repo_meta.default_branch}"], cwd=repo, timeout=60, check=True)
    return branch


def evidence_paths(repo: Path) -> list[str]:
    paths: list[str] = []
    candidates = [
        "README.md", "ISSUE_CONTRACT.md", "TARGET_CONTRACT.md", "ARCHITECTURE.md",
        "DEV_UP_INSTRUCTIONS.md", "QUALITY.md", "package.json", "pyproject.toml",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Makefile", "Dockerfile",
        "docker-compose.yml", "compose.yaml", "vercel.json", "fly.toml", "wrangler.toml",
        "supabase/config.toml", "netlify.toml",
    ]
    for rel in candidates:
        if (repo / rel).exists():
            paths.append(rel)
    for rel in ("src", "app", "lib", "packages", "services", "tests", "test", "docs", "scripts", "deploy", "infra", ".github/workflows", "machine"):
        if (repo / rel).exists():
            paths.append(rel + "/")
    return paths


def crystallization_prompt(repo_meta: EstateRepo, repo: Path) -> str:
    evidence = ", ".join(evidence_paths(repo)) or "entire repository tree and git history"
    fork_note = f"Fork parent: {repo_meta.parent_full_name}. Determine whether this is intentional specialization." if repo_meta.fork else ""
    archive_note = "Repository is archived. Do not unarchive merely to reduce unknown count; determine whether archive is intentional and justified by a verified successor or completed historical role." if repo_meta.archived else ""
    return textwrap.dedent(
        f"""
        CRYSTALLIZATION-MANDATE repository worker
        Repository: {repo_meta.full_name}
        {fork_note}
        {archive_note}

        THIS IS METAMORPHOSIS, NOT CLEANUP OR GATE REPAIR.

        Objective: make this repository the strongest complete supportable expression
        of the outcome it exists to produce. If it cannot honestly be completed in this
        work unit, leave it explicitly INCOMPLETE/BROKEN with exact material gaps. Never
        manufacture completion.

        AUTHORITY ORDER
        1. Purpose and intended outcome.
        2. Complete material capability set implied by that purpose.
        3. Real implementation and integrated execution.
        4. Correctness, reliability, integrations, deployment, observability.
        5. Maintainability and governance only insofar as they protect function.

        INTENTION RECOVERY
        Inspect {evidence}, git log/history, branches where useful, issues/PR context if
        accessible, package/API/schema shapes, deployment configuration, generated
        artifacts, and neighboring/successor/predecessor clues. Do not reduce intent to
        today's compiling code. Recover buried, broken, abandoned, and implied
        capabilities that materially belong to the real system.

        REQUIRED MACHINE MANIFESTS
        Create/update machine/crystallization/purpose-manifest.json:
        {{
          "schema":"glaciereq.crystallization-purpose.v1",
          "canonical_identity":"...",
          "purpose":"...",
          "problem":"...",
          "intended_outcome":"...",
          "consumers":[...],
          "system_kind":"web_app|web_service|api_service|worker|daemon|scheduled_service|edge_function|serverless|mcp_server|agent_service|library|cli|binary|local_first_runtime|data_pipeline|ml_pipeline|research_tool|developer_tool|historical_artifact|other",
          "naturally_deployable":true|false,
          "lineage":{{"predecessors":[],"successors":[],"duplicates":[],"canonical_successor":null}},
          "evidence":[...]
        }}

        Create/update machine/crystallization/capability-manifest.json:
        {{
          "schema":"glaciereq.crystallization-capabilities.v1",
          "capabilities":[
            {{"id":"...","class":"...","description":"...","material":true,
              "state":"WORKING|PARTIAL|BROKEN|MISSING|OBSOLETE|UNKNOWN",
              "implementation_paths":[],"verification":[],"consumer":"..."}}
          ]
        }}

        Create/update machine/crystallization/gap-matrix.json containing every material
        PARTIAL/BROKEN/MISSING/UNKNOWN capability plus exact work required. Obsolete
        capability is not a gap only when the purpose manifest explains why.

        THEN CRYSTALLIZE
        - Implement every material missing/broken/partial capability supportable now.
        - Connect partial components into primary and required secondary execution paths.
        - Replace fake integrations with real integrations or mark the capability gap.
        - Add real persistence/auth/interfaces/search/retrieval/workers/APIs/UI/deployment
          only where purpose demands them.
        - Preserve strong existing mechanisms and unique features.
        - Remove or simplify dead abstraction/gates only when they obstruct function.
        - Choose language by technical boundary; never rewrite for fashion.
        - Make examples execute. Make docs describe observed reality.
        - For naturally deployable systems, add and exercise health/readiness,
          environment/secrets contract, reproducible deployment/release, logs, and
          rollback capability for future operational failure.

        PROOF
        Test purpose, not implementation shape. Exercise happy path, material failures,
        integrations, runtime, recovery/adversarial behavior, and deployment smoke where
        applicable. Do not write a CRYSTALLIZED receipt yourself. The outer verifier owns
        terminal status after independently reading manifests and executing proof.

        HARD PROHIBITIONS
        No placeholders/TODO core paths, fake APIs, fake integrations, mock-as-product,
        dead routes, undocumented required secrets, hardcoded local-only required paths,
        README claims unsupported by runtime, mass documentation as completion, or green
        CI presented as purpose completion.
        """
    ).strip()


def invoke_worker(repo_meta: EstateRepo, repo: Path, worker_cmd: str | None) -> plumbing.CommandResult:
    prompt = crystallization_prompt(repo_meta, repo)
    if worker_cmd:
        argv = worker_cmd.split() + [prompt]
    else:
        require_tool("grok")
        argv = ["grok", "-p", prompt, "--yolo", "--sandbox", "workspace"]
    return run(argv, cwd=repo, timeout=7200, check=False)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def validate_purpose_manifest(repo: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = repo / MACHINE_DIR / "purpose-manifest.json"
    if not path.is_file():
        return None, ["purpose_manifest_missing"]
    try:
        data = load_json(path)
    except Exception as exc:
        return None, [f"purpose_manifest_invalid:{exc}"]
    blockers: list[str] = []
    for key in ("canonical_identity", "purpose", "problem", "intended_outcome", "system_kind"):
        if not isinstance(data.get(key), str) or not str(data[key]).strip():
            blockers.append(f"purpose_manifest_{key}_missing")
    if not isinstance(data.get("consumers"), list) or not data["consumers"]:
        blockers.append("purpose_manifest_consumers_missing")
    if not isinstance(data.get("naturally_deployable"), bool):
        blockers.append("purpose_manifest_naturally_deployable_missing")
    if not isinstance(data.get("evidence"), list) or not data["evidence"]:
        blockers.append("purpose_manifest_evidence_missing")
    lineage = data.get("lineage")
    if not isinstance(lineage, dict):
        blockers.append("purpose_manifest_lineage_missing")
    return data, blockers


def validate_capability_manifest(repo: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    path = repo / MACHINE_DIR / "capability-manifest.json"
    if not path.is_file():
        return None, [], ["capability_manifest_missing"]
    try:
        data = load_json(path)
    except Exception as exc:
        return None, [], [f"capability_manifest_invalid:{exc}"]
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        return data, [], ["capability_inventory_empty"]
    blockers: list[str] = []
    ids: set[str] = set()
    valid: list[dict[str, Any]] = []
    for index, cap in enumerate(capabilities):
        if not isinstance(cap, dict):
            blockers.append(f"capability_{index}_not_object")
            continue
        cap_id = str(cap.get("id") or "").strip()
        state = str(cap.get("state") or "").upper()
        if not cap_id:
            blockers.append(f"capability_{index}_id_missing")
        elif cap_id in ids:
            blockers.append(f"capability_duplicate_id:{cap_id}")
        ids.add(cap_id)
        if state not in CAPABILITY_STATES:
            blockers.append(f"capability_{cap_id or index}_state_invalid")
        if not isinstance(cap.get("material"), bool):
            blockers.append(f"capability_{cap_id or index}_material_missing")
        if not isinstance(cap.get("description"), str) or not cap.get("description", "").strip():
            blockers.append(f"capability_{cap_id or index}_description_missing")
        cap["state"] = state
        valid.append(cap)
    return data, valid, blockers


def validate_gap_matrix(repo: Path, capabilities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    path = repo / MACHINE_DIR / "gap-matrix.json"
    expected = {
        str(cap.get("id"))
        for cap in capabilities
        if cap.get("material") is True and str(cap.get("state")) in MATERIAL_OPEN_STATES
    }
    if not path.is_file():
        return [], ["gap_matrix_missing"]
    try:
        data = load_json(path)
    except Exception as exc:
        return [], [f"gap_matrix_invalid:{exc}"]
    gaps = data.get("gaps")
    if not isinstance(gaps, list):
        return [], ["gap_matrix_gaps_missing"]
    listed = set()
    blockers: list[str] = []
    valid_gaps: list[dict[str, Any]] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            blockers.append("gap_not_object")
            continue
        cap_id = str(gap.get("capability_id") or "").strip()
        if not cap_id:
            blockers.append("gap_capability_id_missing")
            continue
        listed.add(cap_id)
        if not isinstance(gap.get("required_work"), str) or not gap.get("required_work", "").strip():
            blockers.append(f"gap_required_work_missing:{cap_id}")
        valid_gaps.append(gap)
    missing = expected - listed
    extra_open = listed - expected
    if missing:
        blockers.append("gap_matrix_omits_open_capabilities:" + ",".join(sorted(missing)))
    if extra_open:
        blockers.append("gap_matrix_lists_non_open_capabilities:" + ",".join(sorted(extra_open)))
    return valid_gaps, blockers


def scan_prohibited_markers(repo: Path) -> list[str]:
    hits: list[str] = []
    for rel in ("README.md", "DEV_UP_INSTRUCTIONS.md"):
        path = repo / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in PROHIBITED_COMPLETION_MARKERS:
            if marker in text:
                hits.append(f"{rel}:{marker}")
    for base in ("src", "app", "lib", "services"):
        root = repo / base
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt", ".sql"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in PROHIBITED_COMPLETION_MARKERS:
                if marker in text:
                    hits.append(f"{path.relative_to(repo)}:{marker}")
    return hits[:100]


def execute_commands(repo: Path, commands: Iterable[list[str]], timeout: int = 1800) -> tuple[bool, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    ok = True
    for command in commands:
        result = run(command, cwd=repo, timeout=timeout, check=False)
        records.append({
            "argv": command,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-3000:],
            "stderr_tail": result.stderr[-3000:],
        })
        if result.returncode != 0:
            ok = False
            break
    return ok, records


def infer_runtime_commands(repo: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    if (repo / "scripts/operate.py").exists() and shutil.which("python3"):
        commands.append([sys.executable, "scripts/operate.py"])
    if (repo / "package.json").exists() and shutil.which("npm"):
        try:
            package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
            scripts = package.get("scripts") or {}
            for name in ("smoke", "demo", "start:test", "healthcheck"):
                if scripts.get(name):
                    commands.append(["npm", "run", name])
                    break
        except Exception:
            pass
    if (repo / "Makefile").exists() and shutil.which("make"):
        text = (repo / "Makefile").read_text(encoding="utf-8", errors="ignore")
        for target in ("smoke", "demo", "run"):
            if re.search(rf"(?m)^{re.escape(target)}\s*:", text):
                commands.append(["make", target])
                break
    return commands


def infer_example_commands(repo: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    examples = repo / "examples"
    if not examples.is_dir():
        return commands
    for path in sorted(examples.iterdir()):
        if path.suffix == ".py" and shutil.which("python3"):
            commands.append([sys.executable, str(path.relative_to(repo))])
            break
        if path.suffix == ".sh" and shutil.which("bash"):
            commands.append(["bash", str(path.relative_to(repo))])
            break
    return commands


def deployment_evidence(repo: Path, purpose: Mapping[str, Any]) -> tuple[bool, list[dict[str, Any]], list[str]]:
    naturally = bool(purpose.get("naturally_deployable"))
    kind = str(purpose.get("system_kind") or "")
    if not naturally and kind not in NATURAL_DEPLOY_KINDS:
        return True, [{"not_applicable": True, "system_kind": kind}], []
    surfaces = [
        "Dockerfile", "docker-compose.yml", "compose.yaml", "vercel.json", "fly.toml",
        "netlify.toml", "wrangler.toml", "Procfile", "supabase/config.toml",
    ]
    found = [rel for rel in surfaces if (repo / rel).exists()]
    workflow_dir = repo / ".github/workflows"
    deploy_workflows: list[str] = []
    if workflow_dir.is_dir():
        for path in workflow_dir.glob("*.y*ml"):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if any(token in text for token in ("deploy", "release", "publish")):
                deploy_workflows.append(str(path.relative_to(repo)))
    evidence = [{"deployment_surfaces": found, "deployment_workflows": deploy_workflows}]
    blockers: list[str] = []
    if not found and not deploy_workflows:
        blockers.append("natural_deployment_surface_missing")
    health_found = any(
        token in path.read_text(encoding="utf-8", errors="ignore").lower()
        for base in ("src", "app", "services") if (path := repo / base).is_dir()
        for path in list(path.rglob("*.py"))[:20] + list(path.rglob("*.ts"))[:20] + list(path.rglob("*.go"))[:20]
        for token in ("/health", "healthcheck", "readiness", "/ready")
    ) if any((repo / base).is_dir() for base in ("src", "app", "services")) else False
    if not health_found and kind in NATURAL_DEPLOY_KINDS:
        blockers.append("health_or_readiness_surface_missing")
    return not blockers, evidence, blockers


def infer_status(
    *,
    purpose: dict[str, Any] | None,
    capabilities: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    blockers: list[str],
    test_ok: bool,
    build_ok: bool,
    runtime_ok: bool,
    deploy_ok: bool,
) -> RepoStatus:
    if purpose is None:
        return RepoStatus.UNKNOWN
    if not capabilities:
        return RepoStatus.UNDERSTOOD
    material = [cap for cap in capabilities if cap.get("material") is True]
    states = {str(cap.get("state")) for cap in material}
    if "BROKEN" in states or not test_ok:
        return RepoStatus.BROKEN
    if gaps or states.intersection(MATERIAL_OPEN_STATES):
        return RepoStatus.INCOMPLETE
    if not runtime_ok:
        return RepoStatus.INCOMPLETE
    if not build_ok:
        return RepoStatus.INCOMPLETE
    if not deploy_ok:
        return RepoStatus.COMPLETE
    if blockers:
        return RepoStatus.INCOMPLETE
    return RepoStatus.CRYSTALLIZED


def write_outer_receipt(
    repo: Path,
    repo_meta: EstateRepo,
    result: CrystallizationResult,
    evidence: VerificationEvidence,
) -> Path:
    target = repo / MACHINE_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / "completion-receipt.json"
    payload = {
        "schema": "glaciereq.crystallization-completion.v1",
        "mandate": "CRYSTALLIZATION-MANDATE@1.0",
        "repository_id": repo_meta.full_name,
        "canonical_purpose": result.purpose,
        "intended_outcome": result.outcome,
        "system_kind": result.system_kind,
        "capability_count": result.capability_count,
        "implemented_capability_count": result.implemented_capability_count,
        "verified_capability_count": result.verified_capability_count,
        "test_receipts": evidence.tests,
        "build_receipts": evidence.build,
        "runtime_receipts": evidence.runtime,
        "deployment_receipts": evidence.deployment,
        "example_receipts": evidence.examples,
        "unresolved_gaps": result.unresolved_gaps,
        "blockers": result.blockers,
        "source_sha": result.source_sha,
        "final_status": result.status,
        "verified_at": now(),
        "terminal_truth": result.status == RepoStatus.CRYSTALLIZED.value,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def open_pr(repo: Path, repo_meta: EstateRepo, branch: str, result: CrystallizationResult) -> str | None:
    title = f"Crystallize {repo_meta.name}: purpose-complete implementation"
    body = textwrap.dedent(
        f"""
        CRYSTALLIZATION-MANDATE work unit.

        Status after independent local verification: **{result.status}**

        Canonical purpose: {result.purpose or 'unresolved'}
        Material capabilities: {result.capability_count}
        Verified capabilities: {result.verified_capability_count}
        Remaining material gaps: {result.remaining_gap_count}

        This PR is not considered complete merely because CI is green. `CRYSTALLIZED`
        requires purpose reconstruction, exhaustive material capability accounting, real
        execution, behavioral proof, deployability/deployment where natural, truthful
        documentation, and zero unresolved material gaps.
        """
    ).strip()
    created = run(
        ["gh", "pr", "create", "--repo", repo_meta.full_name, "--base", repo_meta.default_branch,
         "--head", branch, "--title", title, "--body", body],
        cwd=repo, timeout=120, check=False,
    )
    if created.returncode != 0:
        return None
    for line in created.stdout.splitlines():
        if line.startswith("https://github.com/"):
            return line.strip()
    return created.stdout.strip() or None


def resolve_archived_or_successor(repo_meta: EstateRepo, repo: Path) -> CrystallizationResult | None:
    """Resolve only when repository-local lineage evidence explicitly proves it."""
    purpose_path = repo / MACHINE_DIR / "purpose-manifest.json"
    if not purpose_path.is_file():
        return None
    try:
        purpose = load_json(purpose_path)
    except Exception:
        return None
    lineage = purpose.get("lineage") or {}
    successor = lineage.get("canonical_successor") if isinstance(lineage, dict) else None
    if isinstance(successor, str) and successor.strip():
        return CrystallizationResult(
            repository=repo_meta.full_name,
            status=RepoStatus.CANONICALIZED_SUCCESSOR.value,
            canonical_identity=str(purpose.get("canonical_identity") or repo_meta.full_name),
            purpose=str(purpose.get("purpose") or ""),
            outcome=str(purpose.get("intended_outcome") or ""),
            system_kind=str(purpose.get("system_kind") or ""),
            proof_artifacts=[str(purpose_path.relative_to(repo))],
        )
    archive_reason = purpose.get("intentional_archive_reason")
    if repo_meta.archived and isinstance(archive_reason, str) and archive_reason.strip():
        return CrystallizationResult(
            repository=repo_meta.full_name,
            status=RepoStatus.INTENTIONALLY_ARCHIVED.value,
            canonical_identity=str(purpose.get("canonical_identity") or repo_meta.full_name),
            purpose=str(purpose.get("purpose") or ""),
            outcome=str(purpose.get("intended_outcome") or ""),
            system_kind=str(purpose.get("system_kind") or "historical_artifact"),
            proof_artifacts=[str(purpose_path.relative_to(repo))],
        )
    return None


def crystallize_one(
    repo_meta: EstateRepo,
    *,
    root: Path,
    worker_cmd: str | None,
    push: bool,
    create_pr: bool,
) -> CrystallizationResult:
    branch: str | None = None
    try:
        repo = clone_or_refresh(repo_meta, root)
        resolved = resolve_archived_or_successor(repo_meta, repo)
        if resolved:
            return resolved
        if repo_meta.archived or repo_meta.disabled:
            return CrystallizationResult(
                repository=repo_meta.full_name,
                status=RepoStatus.UNKNOWN.value,
                blockers=["archived_or_disabled_without_verified_resolution"],
            )

        branch = prepare_branch(repo, repo_meta)
        worker = invoke_worker(repo_meta, repo, worker_cmd)
        if worker.returncode != 0:
            return CrystallizationResult(
                repository=repo_meta.full_name,
                status=RepoStatus.BROKEN.value,
                branch=branch,
                blockers=[f"implementation_worker_exit:{worker.returncode}"],
                worker_tail=(worker.stdout + worker.stderr)[-4000:],
            )

        purpose, purpose_blockers = validate_purpose_manifest(repo)
        _, capabilities, capability_blockers = validate_capability_manifest(repo)
        gaps, gap_blockers = validate_gap_matrix(repo, capabilities)
        marker_blockers = [f"fake_completion_marker:{hit}" for hit in scan_prohibited_markers(repo)]
        blockers = purpose_blockers + capability_blockers + gap_blockers + marker_blockers

        test_commands = plumbing.infer_test_commands(repo)
        build_commands = plumbing.infer_build_commands(repo)
        runtime_commands = infer_runtime_commands(repo)
        example_commands = infer_example_commands(repo)
        test_ok, test_records = execute_commands(repo, test_commands) if test_commands else (False, [])
        build_ok, build_records = execute_commands(repo, build_commands) if build_commands else (False, [])
        runtime_ok, runtime_records = execute_commands(repo, runtime_commands) if runtime_commands else (False, [])
        example_ok, example_records = execute_commands(repo, example_commands) if example_commands else (True, [])
        if not test_commands:
            blockers.append("no_executable_behavior_test_command")
        if not build_commands:
            blockers.append("no_reproducible_build_command")
        if not runtime_commands:
            blockers.append("no_primary_runtime_or_operate_command")
        if not test_ok:
            blockers.append("behavior_tests_failed")
        if not build_ok:
            blockers.append("build_failed")
        if not runtime_ok:
            blockers.append("primary_runtime_failed")
        if not example_ok:
            blockers.append("example_execution_failed")

        if purpose:
            deploy_ok, deploy_records, deploy_blockers = deployment_evidence(repo, purpose)
        else:
            deploy_ok, deploy_records, deploy_blockers = False, [], ["deployment_applicability_unknown"]
        blockers.extend(deploy_blockers)

        material = [cap for cap in capabilities if cap.get("material") is True]
        implemented = [cap for cap in material if cap.get("state") in {"WORKING", "PARTIAL"}]
        verified = [
            cap for cap in material
            if cap.get("state") == "WORKING" and isinstance(cap.get("verification"), list) and cap.get("verification")
        ]
        open_gaps = [
            gap for gap in gaps
            if isinstance(gap, dict) and str(gap.get("state") or "").upper() in MATERIAL_OPEN_STATES
        ]
        # Gap manifests may omit state because capability state is authoritative.
        if not open_gaps:
            open_ids = {
                str(cap.get("id")) for cap in material if str(cap.get("state")) in MATERIAL_OPEN_STATES
            }
            open_gaps = [gap for gap in gaps if str(gap.get("capability_id")) in open_ids]

        status = infer_status(
            purpose=purpose,
            capabilities=capabilities,
            gaps=open_gaps,
            blockers=blockers,
            test_ok=test_ok,
            build_ok=build_ok,
            runtime_ok=runtime_ok,
            deploy_ok=deploy_ok,
        )

        source_sha = plumbing.source_tree_sha(repo)
        result = CrystallizationResult(
            repository=repo_meta.full_name,
            status=status.value,
            canonical_identity=str((purpose or {}).get("canonical_identity") or repo_meta.full_name),
            purpose=str((purpose or {}).get("purpose") or ""),
            outcome=str((purpose or {}).get("intended_outcome") or ""),
            system_kind=str((purpose or {}).get("system_kind") or ""),
            capability_count=len(material),
            implemented_capability_count=len(implemented),
            verified_capability_count=len(verified),
            remaining_gap_count=len(open_gaps),
            branch=branch,
            source_sha=source_sha,
            proof_artifacts=[
                str(MACHINE_DIR / "purpose-manifest.json"),
                str(MACHINE_DIR / "capability-manifest.json"),
                str(MACHINE_DIR / "gap-matrix.json"),
            ],
            build_result="PASS" if build_ok else "FAIL",
            test_result="PASS" if test_ok else "FAIL",
            runtime_result="PASS" if runtime_ok else "FAIL",
            deployment_result="PASS" if deploy_ok else "FAIL",
            unresolved_gaps=[str(gap.get("capability_id") or gap) for gap in open_gaps],
            blockers=sorted(set(blockers)),
            worker_tail=(worker.stdout + worker.stderr)[-2000:],
        )
        evidence = VerificationEvidence(
            build=build_records,
            tests=test_records,
            runtime=runtime_records,
            deployment=deploy_records,
            examples=example_records,
        )
        receipt_path = write_outer_receipt(repo, repo_meta, result, evidence)
        result.proof_artifacts.append(str(receipt_path.relative_to(repo)))

        changed = plumbing.changed_files(repo)
        if changed:
            run(["git", "add", "-A"], cwd=repo, timeout=60, check=True)
            run(["git", "commit", "-m", f"crystallize: {repo_meta.name} purpose-complete work unit"], cwd=repo, timeout=180, check=True)
            if push:
                run(["git", "push", "-u", "origin", branch, "--force-with-lease"], cwd=repo, timeout=600, check=True)
                if create_pr:
                    result.pr_url = open_pr(repo, repo_meta, branch, result)
        elif status is RepoStatus.CRYSTALLIZED:
            # A pre-existing repo may already satisfy the mandate; still truthfully record no delta.
            result.proof_artifacts.append("NO_SOURCE_DELTA_REQUIRED")
        return result
    except Exception as exc:
        return CrystallizationResult(
            repository=repo_meta.full_name,
            status=RepoStatus.BROKEN.value,
            branch=branch,
            blockers=[repr(exc)],
        )


def save_estate_ledger(results: list[CrystallizationResult]) -> Path:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    payload = {
        "schema": "glaciereq.crystallization-estate-ledger.v1",
        "mandate": "CRYSTALLIZATION-MANDATE@1.0",
        "generated_at": now(),
        "total_repositories": len(results),
        "inspected_repositories": sum(result.status != RepoStatus.UNKNOWN.value for result in results),
        "crystallized_repositories": counts.get(RepoStatus.CRYSTALLIZED.value, 0),
        "canonicalized_successors": counts.get(RepoStatus.CANONICALIZED_SUCCESSOR.value, 0),
        "intentionally_archived": counts.get(RepoStatus.INTENTIONALLY_ARCHIVED.value, 0),
        "broken_remaining": counts.get(RepoStatus.BROKEN.value, 0),
        "incomplete_remaining": counts.get(RepoStatus.INCOMPLETE.value, 0),
        "unknown_remaining": counts.get(RepoStatus.UNKNOWN.value, 0),
        "deployed_systems": sum(result.deployment_result == "PASS" for result in results),
        "verified_capabilities": sum(result.verified_capability_count for result in results),
        "unresolved_failures": [
            {"repository": result.repository, "status": result.status, "blockers": result.blockers, "gaps": result.unresolved_gaps}
            for result in results
            if result.status not in {
                RepoStatus.CRYSTALLIZED.value,
                RepoStatus.CANONICALIZED_SUCCESSOR.value,
                RepoStatus.INTENTIONALLY_ARCHIVED.value,
            }
        ],
        "results": [asdict(result) for result in results],
    }
    payload["estate_complete"] = (
        payload["unknown_remaining"] == 0
        and payload["broken_remaining"] == 0
        and payload["incomplete_remaining"] == 0
        and payload["crystallized_repositories"] + payload["canonicalized_successors"] + payload["intentionally_archived"] == len(results)
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = STATE_ROOT / f"ledger-{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (STATE_ROOT / "latest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_priority(path: Path | None) -> set[str]:
    if not path:
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("repositories") or raw.get("repos") or raw.get("priority") or []
    else:
        items = []
    out: set[str] = set()
    for item in items:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict):
            value = item.get("repository") or item.get("repo") or item.get("name")
            if isinstance(value, str):
                out.add(value)
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crystallize every accessible repository according to purpose")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--repo", action="append", default=[])
    p.add_argument("--priority-file", type=Path)
    p.add_argument("--limit", type=int, default=0, help="0 means every selected repository; limit is logistics only")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--worker-cmd", default=os.environ.get("CRYSTALLIZATION_WORKER_CMD"))
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--no-pr", action="store_true")
    p.add_argument("--list", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    explicit = load_priority(args.priority_file)
    repos = discover_accessible_repositories()
    if args.repo:
        wanted = set(args.repo)
        repos = [repo for repo in repos if repo.full_name in wanted or repo.name in wanted]
    repos.sort(key=lambda repo: target_priority(repo, explicit))
    if args.limit > 0:
        repos = repos[: args.limit]
    if args.list:
        print(json.dumps([asdict(repo) for repo in repos], indent=2))
        return 0
    if not repos:
        print("no repositories selected", file=sys.stderr)
        return 2

    results: list[CrystallizationResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                crystallize_one,
                repo,
                root=args.root,
                worker_cmd=args.worker_cmd,
                push=not args.no_push,
                create_pr=not args.no_pr,
            ): repo
            for repo in repos
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"[{result.status}] {result.repository} capabilities={result.verified_capability_count}/{result.capability_count} gaps={result.remaining_gap_count}",
                flush=True,
            )

    results.sort(key=lambda result: result.repository.lower())
    ledger = save_estate_ledger(results)
    unresolved = [
        result for result in results
        if result.status not in {
            RepoStatus.CRYSTALLIZED.value,
            RepoStatus.CANONICALIZED_SUCCESSOR.value,
            RepoStatus.INTENTIONALLY_ARCHIVED.value,
        }
    ]
    print(f"crystallized={sum(result.status == RepoStatus.CRYSTALLIZED.value for result in results)}/{len(results)}")
    print(f"unresolved={len(unresolved)}")
    print(f"ledger={ledger}")
    return 0 if not unresolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
