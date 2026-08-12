#!/usr/bin/env python3
"""Estate-wide repository function restorer.

This is the write-capable lane that the estate orchestration was missing.
It operates on isolated repair branches, preserves existing control-plane and
repository history, invokes a real implementation worker, verifies native
function and deployability, writes source-bound receipts, pushes the branch,
and can open a PR. It never edits canonical main directly.

Core law:
    native purpose -> real mechanism -> adversarial proof -> deployable artifact
    -> source-bound receipt -> promotion candidate

The restorer is intentionally fail-closed. A worker can modify a repository,
but the repository is not marked repaired unless native verification passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

OWNER = "GlacierEQ"
DEFAULT_ROOT = Path.home() / "estate-function-repair"
STATE_ROOT = Path.home() / "GlacierEQ_Swarm" / "state" / "estate_function_repair"
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SOURCE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".sql", ".sh", ".yaml", ".yml", ".json", ".toml", ".md",
}
SCaffold_MARKERS = (
    "This leaf is a **scaffold**",
    "## Current scaffold state",
    "Replace the stub mechanism",
    "Implementation is the next agent's job",
    "SCAFFOLD STUB",
)
ADVERSARIAL_NAME = re.compile(
    r"(adversarial|refuse|reject|invalid|denied|failure|edge|tamper|forbid|unsafe|malformed)",
    re.I,
)


@dataclass(frozen=True)
class RepoTarget:
    name: str
    default_branch: str
    visibility: str
    description: str
    pushed_at: str


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class RepairResult:
    repository: str
    status: str
    branch: str | None = None
    source_sha: str | None = None
    test_commands: list[list[str]] | None = None
    build_commands: list[list[str]] | None = None
    behavioral_cases: int = 0
    adversarial_cases: int = 0
    pr_url: str | None = None
    blockers: list[str] | None = None
    worker_tail: str | None = None


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    check: bool = False,
) -> CommandResult:
    p = subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(list(argv), p.returncode, p.stdout or "", p.stderr or "")
    if check and p.returncode != 0:
        tail = (result.stdout + result.stderr)[-3000:]
        raise RuntimeError(f"command failed ({p.returncode}): {list(argv)!r}\n{tail}")
    return result


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"required tool not found: {name}")
    return path


def load_priority_names(path: Path | None) -> set[str]:
    if not path:
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, dict):
        values = raw.get("repositories") or raw.get("repos") or raw.get("priority") or []
    else:
        values = []
    out: set[str] = set()
    for item in values:
        if isinstance(item, str):
            out.add(item.removeprefix(f"{OWNER}/"))
        elif isinstance(item, dict):
            name = item.get("name") or item.get("repository") or item.get("repo")
            if isinstance(name, str):
                out.add(name.removeprefix(f"{OWNER}/"))
    return out


def discover_native_repositories(owner: str = OWNER) -> list[RepoTarget]:
    """Return the live non-fork, non-archived owner estate from GitHub CLI."""
    require_tool("gh")
    result = run(
        [
            "gh", "repo", "list", owner,
            "--limit", "1000",
            "--json", "name,isFork,isArchived,visibility,description,defaultBranchRef,pushedAt",
        ],
        timeout=120,
        check=True,
    )
    payload = json.loads(result.stdout)
    targets: list[RepoTarget] = []
    for row in payload:
        if row.get("isFork") or row.get("isArchived"):
            continue
        name = str(row.get("name") or "")
        if not REPO_NAME_RE.fullmatch(name):
            continue
        branch_ref = row.get("defaultBranchRef") or {}
        default_branch = branch_ref.get("name") or "main"
        targets.append(
            RepoTarget(
                name=name,
                default_branch=default_branch,
                visibility=str(row.get("visibility") or "UNKNOWN"),
                description=str(row.get("description") or ""),
                pushed_at=str(row.get("pushedAt") or ""),
            )
        )
    return targets


def target_priority(target: RepoTarget, priority_names: set[str]) -> tuple[int, str]:
    score = 0
    if target.name in priority_names:
        score += 10_000
    if target.visibility.upper() == "PUBLIC":
        score += 500
    lower = target.name.lower()
    recruiter_prefixes = (
        "openai", "anthropic", "nvidia", "xai", "spacex", "anduril", "palantir",
        "groq", "microsoft", "notion", "vercel", "cloudflare", "supabase",
        "pinecone", "qdrant", "coreweave", "cursor", "linear", "mistral",
    )
    if lower.startswith(recruiter_prefixes):
        score += 300
    if target.description:
        score += 30
    return (-score, target.name.lower())


def ensure_checkout(target: RepoTarget, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    repo = root / target.name
    if repo.exists():
        if not (repo / ".git").is_dir():
            raise RuntimeError(f"path exists but is not git repo: {repo}")
        dirty = run(["git", "status", "--porcelain"], cwd=repo, timeout=30, check=True)
        if dirty.stdout.strip():
            raise RuntimeError("local worktree is dirty; refusing to overwrite human work")
        run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=180, check=True)
    else:
        run(["gh", "repo", "clone", f"{OWNER}/{target.name}", str(repo)], timeout=600, check=True)
    run(["git", "checkout", target.default_branch], cwd=repo, timeout=60, check=True)
    run(["git", "reset", "--hard", f"origin/{target.default_branch}"], cwd=repo, timeout=60, check=True)
    return repo


def repair_branch_name(repo_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_name).strip("-")[:48]
    return f"restore/function-{stamp}-{slug}"


def prepare_branch(repo: Path, target: RepoTarget) -> str:
    branch = repair_branch_name(target.name)
    existing = run(["git", "branch", "--list", branch], cwd=repo, timeout=30, check=True)
    if existing.stdout.strip():
        run(["git", "branch", "-D", branch], cwd=repo, timeout=30, check=True)
    run(["git", "checkout", "-b", branch, f"origin/{target.default_branch}"], cwd=repo, timeout=60, check=True)
    return branch


def native_evidence_paths(repo: Path) -> list[str]:
    candidates = [
        "ISSUE_CONTRACT.md", "TARGET_CONTRACT.md", "README.md", "QUALITY.md",
        "DEV_UP_INSTRUCTIONS.md", "ARCHITECTURE.md", "pyproject.toml", "package.json",
        "Cargo.toml", "go.mod", "Makefile", "Dockerfile", "vercel.json", "fly.toml",
    ]
    found = [p for p in candidates if (repo / p).exists()]
    for extra in ("src", "tests", ".github/workflows", "scripts", "deploy", "docs", "machine"):
        if (repo / extra).exists():
            found.append(extra + "/")
    return found


def worker_prompt(target: RepoTarget, repo: Path) -> str:
    evidence = ", ".join(native_evidence_paths(repo)) or "repository tree + git history"
    return textwrap.dedent(
        f"""
        You are the write-capable implementation worker for GlacierEQ/{target.name}.

        OBJECTIVE
        Restore and deepen this repository until it is a complex, impressive, stable,
        recruiter-demonstrable implementation with real function and a deployable or
        installable runtime. This is not an audit and not a documentation exercise.

        AUTHORITY
        - The existing repository is the source of intent. Recover purpose before editing.
        - Preserve existing useful mechanisms, control-plane integration, architecture,
          tests, receipts, history, and domain-specific ideas. No refactor for novelty.
        - Never replace real domain behavior with generic scaffolding, import smoke tests,
          placeholder receipts, generic operate wrappers, or README theater.
        - Do not weaken or delete gates merely to turn checks green.
        - Do not claim deployment, performance, affiliation, scale, or production use
          unless the repository can actually prove it.

        FIRST: RECOVER THE NATIVE PURPOSE
        Inspect {evidence}. Also inspect git log/history and relevant neighboring files.
        Determine the repo's actual problem, user, central mechanism, unique value,
        failure modes, and recruiter/company relevance. Resolve contradictions in favor
        of the strongest evidence-bearing native intent.

        THEN BUILD, DO NOT JUST DESCRIBE
        1. Implement or deepen the real central mechanism. No TODO core paths.
        2. Add explicit refuse/failure behavior and reason codes where applicable.
        3. Add meaningful deterministic behavioral tests and at least one adversarial,
           malformed, refusal, failure, or edge-path test tied to the real mechanism.
        4. Make the runtime genuinely usable:
           - service/app: reproducible build + health/readiness + Docker/platform deploy
             surface or existing stronger deployment surface;
           - library: reproducible package/build/install + executable smoke example;
           - CLI/tool: install/build + real CLI invocation + failure exit semantics;
           - data/ML system: reproducible pipeline/eval run + artifact contract.
        5. Preserve and strengthen observability, provenance, authority, security and
           control-plane hooks when the repository already has them.
        6. Update README/QUALITY only after code is real, with exact run/demo/deploy steps.
        7. Do not write fake PASS receipts. The outer restorer writes source-bound proof
           only after independent verification.

        RECRUITER BAR
        A reviewer opening this repository should be able to answer, from working code:
        "What difficult problem does this solve? What is technically distinctive? How do
        I run it? How does it fail safely? How is it tested? How can it be deployed?"

        COMPLETION BAR
        Do not stop at analysis. Modify files. Run the strongest native tests/builds you
        can execute. Leave the worktree in a coherent, reviewable state. If the repo has
        no honest deployment mode, build the appropriate installable/deployable mode
        instead of inventing a cloud deployment claim.
        """
    ).strip()


def invoke_worker(target: RepoTarget, repo: Path, *, worker_cmd: str | None) -> CommandResult:
    prompt = worker_prompt(target, repo)
    if worker_cmd:
        argv = worker_cmd.split() + [prompt]
    else:
        require_tool("grok")
        argv = ["grok", "-p", prompt, "--yolo", "--sandbox", "workspace"]
    return run(argv, cwd=repo, timeout=3600, check=False)


def source_tree_sha(repo: Path) -> str:
    rows: list[str] = []
    excluded = {".git", "node_modules", ".venv", "venv", "dist", "build", "target", "__pycache__"}
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or any(part in excluded for part in path.parts):
            continue
        rel = path.relative_to(repo).as_posix()
        if path.suffix not in SOURCE_SUFFIXES and rel not in {"Dockerfile", "Makefile"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{rel}:{digest}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def scaffold_evidence(repo: Path) -> list[str]:
    hits: list[str] = []
    for rel in ("README.md", "DEV_UP_INSTRUCTIONS.md"):
        path = repo / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in SCaffold_MARKERS:
            if marker in text:
                hits.append(f"{rel}:{marker}")
    for path in list((repo / "src").rglob("*.py")) if (repo / "src").is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "SCAFFOLD STUB" in text or "scaffold_allow" in text:
            hits.append(path.relative_to(repo).as_posix())
    return hits


def changed_files(repo: Path) -> list[str]:
    result = run(["git", "status", "--porcelain"], cwd=repo, timeout=30, check=True)
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        paths.append(raw)
    return paths


def infer_test_commands(repo: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    if (repo / "pyproject.toml").exists() or (repo / "pytest.ini").exists() or (repo / "tests").is_dir():
        if shutil.which("python3"):
            if shutil.which("pytest") or "pytest" in (repo / "pyproject.toml").read_text(encoding="utf-8", errors="ignore") if (repo / "pyproject.toml").exists() else False:
                commands.append([sys.executable, "-m", "pytest", "-q"])
            else:
                commands.append([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    if (repo / "Cargo.toml").exists() and shutil.which("cargo"):
        commands.append(["cargo", "test", "--all-targets"])
    if (repo / "go.mod").exists() and shutil.which("go"):
        commands.append(["go", "test", "./..."])
    if (repo / "package.json").exists() and shutil.which("npm"):
        package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        if (package.get("scripts") or {}).get("test"):
            commands.append(["npm", "test", "--", "--runInBand"])
    if not commands and (repo / "Makefile").exists() and shutil.which("make"):
        text = (repo / "Makefile").read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(?m)^test\s*:", text):
            commands.append(["make", "test"])
    return commands


def infer_build_commands(repo: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    if (repo / "Cargo.toml").exists() and shutil.which("cargo"):
        commands.append(["cargo", "build", "--release"])
    if (repo / "go.mod").exists() and shutil.which("go"):
        commands.append(["go", "build", "./..."])
    if (repo / "package.json").exists() and shutil.which("npm"):
        package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
        if (package.get("scripts") or {}).get("build"):
            commands.append(["npm", "run", "build"])
    if (repo / "Dockerfile").exists() and shutil.which("docker"):
        commands.append(["docker", "build", "-t", f"glaciereq-repair-{repo.name}:verify", "."])
    if (repo / "Makefile").exists() and shutil.which("make"):
        text = (repo / "Makefile").read_text(encoding="utf-8", errors="ignore")
        if re.search(r"(?m)^build\s*:", text) and ["make", "build"] not in commands:
            commands.append(["make", "build"])
    return commands


def test_case_counts(repo: Path) -> tuple[int, int]:
    behavioral = 0
    adversarial = 0
    roots = [p for p in (repo / "tests", repo / "test") if p.is_dir()]
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".ts", ".tsx", ".rs", ".go"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            names: list[str] = []
            names.extend(re.findall(r"(?m)^\s*def\s+(test_[A-Za-z0-9_]+)", text))
            names.extend(re.findall(r"\b(?:it|test)\s*\(\s*['\"]([^'\"]+)", text))
            names.extend(re.findall(r"(?m)^\s*fn\s+(test_[A-Za-z0-9_]+)", text))
            for name in names:
                if ADVERSARIAL_NAME.search(name):
                    adversarial += 1
                else:
                    behavioral += 1
    return behavioral, adversarial


def execute_verification(repo: Path, commands: Iterable[list[str]], *, timeout: int = 1200) -> tuple[bool, list[dict]]:
    records: list[dict] = []
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


def deployment_mode(repo: Path) -> str | None:
    if (repo / "Dockerfile").exists() or (repo / "vercel.json").exists() or (repo / "fly.toml").exists():
        return "deployable-service"
    if (repo / "package.json").exists():
        return "buildable-package-or-app"
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        return "installable-python"
    if (repo / "Cargo.toml").exists():
        return "buildable-rust"
    if (repo / "go.mod").exists():
        return "buildable-go"
    if (repo / "Makefile").exists():
        return "make-build"
    return None


def write_receipts(
    repo: Path,
    target: RepoTarget,
    *,
    source_sha: str,
    behavioral_cases: int,
    adversarial_cases: int,
    test_records: list[dict],
    build_records: list[dict],
    mode: str,
) -> None:
    machine = repo / "machine"
    machine.mkdir(parents=True, exist_ok=True)
    implementation = {
        "schema": "glaciereq.implementation-proof.v1",
        "repository": f"{OWNER}/{target.name}",
        "source_sha": source_sha,
        "result": "PASS",
        "scaffold": False,
        "behavioral_cases": behavioral_cases,
        "adversarial_cases": adversarial_cases,
        "verified_at": now(),
        "verification_ref": "machine/estate-function-repair-receipt.json",
    }
    receipt = {
        "schema": "glaciereq.estate-function-repair.v1",
        "repository": f"{OWNER}/{target.name}",
        "source_sha": source_sha,
        "result": "PASS",
        "purpose_recovery": "repository-native evidence + git history",
        "central_mechanism_required": True,
        "scaffold": False,
        "behavioral_cases": behavioral_cases,
        "adversarial_cases": adversarial_cases,
        "test_verification": test_records,
        "deployment_mode": mode,
        "build_or_deploy_verification": build_records,
        "verified_at": now(),
        "nonclaims": [
            "source-bound local verification is not a claim of external production usage",
            "company-targeted repository names do not imply employer affiliation",
        ],
    }
    (machine / "implementation-proof.json").write_text(json.dumps(implementation, indent=2) + "\n", encoding="utf-8")
    (machine / "estate-function-repair-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def create_pr(repo: Path, target: RepoTarget, branch: str) -> str | None:
    result = run(
        [
            "gh", "pr", "create",
            "--repo", f"{OWNER}/{target.name}",
            "--base", target.default_branch,
            "--head", branch,
            "--title", "Restore real repository function and deployability",
            "--body", (
                "Estate function restoration: recovers native purpose, deepens the real central mechanism, "
                "adds behavioral/adversarial proof, verifies build/deployability, and binds implementation "
                "proof to the exact repaired source tree. Existing control-plane and governance work is preserved."
            ),
        ],
        cwd=repo,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("https://github.com/"):
            return line.strip()
    return result.stdout.strip() or None


def repair_one(
    target: RepoTarget,
    *,
    root: Path,
    worker_cmd: str | None,
    push: bool,
    open_pr: bool,
) -> RepairResult:
    branch: str | None = None
    try:
        repo = ensure_checkout(target, root)
        branch = prepare_branch(repo, target)
        worker = invoke_worker(target, repo, worker_cmd=worker_cmd)
        if worker.returncode != 0:
            return RepairResult(
                repository=f"{OWNER}/{target.name}",
                status="WORKER_FAILED",
                branch=branch,
                blockers=[f"worker exit {worker.returncode}"],
                worker_tail=(worker.stdout + worker.stderr)[-3000:],
            )

        changed = changed_files(repo)
        if not changed:
            return RepairResult(
                repository=f"{OWNER}/{target.name}",
                status="NO_FUNCTIONAL_DELTA",
                branch=branch,
                blockers=["worker produced no repository delta"],
                worker_tail=(worker.stdout + worker.stderr)[-3000:],
            )

        scaffolds = scaffold_evidence(repo)
        if scaffolds:
            return RepairResult(
                repository=f"{OWNER}/{target.name}",
                status="SCAFFOLD_REMAINS",
                branch=branch,
                blockers=scaffolds[:20],
                worker_tail=(worker.stdout + worker.stderr)[-3000:],
            )

        behavioral, adversarial = test_case_counts(repo)
        blockers: list[str] = []
        if behavioral < 3:
            blockers.append(f"behavioral test cases < 3 ({behavioral})")
        if adversarial < 1:
            blockers.append(f"adversarial/refuse test cases < 1 ({adversarial})")

        test_commands = infer_test_commands(repo)
        if not test_commands:
            blockers.append("no executable native test command discovered")
        test_ok, test_records = execute_verification(repo, test_commands) if test_commands else (False, [])
        if not test_ok:
            blockers.append("native tests failed")

        mode = deployment_mode(repo)
        if not mode:
            blockers.append("no build/install/deployment surface discovered")
        build_commands = infer_build_commands(repo)
        if not build_commands:
            blockers.append("no executable build/deploy verification command discovered")
        build_ok, build_records = execute_verification(repo, build_commands) if build_commands else (False, [])
        if not build_ok:
            blockers.append("build/deploy verification failed")

        if blockers:
            return RepairResult(
                repository=f"{OWNER}/{target.name}",
                status="VERIFY_FAILED",
                branch=branch,
                test_commands=test_commands,
                build_commands=build_commands,
                behavioral_cases=behavioral,
                adversarial_cases=adversarial,
                blockers=blockers,
                worker_tail=(worker.stdout + worker.stderr)[-3000:],
            )

        source_sha = source_tree_sha(repo)
        write_receipts(
            repo,
            target,
            source_sha=source_sha,
            behavioral_cases=behavioral,
            adversarial_cases=adversarial,
            test_records=test_records,
            build_records=build_records,
            mode=mode or "unknown",
        )
        run(["git", "add", "-A"], cwd=repo, timeout=60, check=True)
        run(
            ["git", "commit", "-m", "restore: real function, proof, and deployability"],
            cwd=repo,
            timeout=120,
            check=True,
        )

        pr_url = None
        if push:
            run(["git", "push", "-u", "origin", branch, "--force-with-lease"], cwd=repo, timeout=600, check=True)
            if open_pr:
                pr_url = create_pr(repo, target, branch)

        return RepairResult(
            repository=f"{OWNER}/{target.name}",
            status="REPAIRED_VERIFIED",
            branch=branch,
            source_sha=source_sha,
            test_commands=test_commands,
            build_commands=build_commands,
            behavioral_cases=behavioral,
            adversarial_cases=adversarial,
            pr_url=pr_url,
            blockers=[],
            worker_tail=(worker.stdout + worker.stderr)[-1500:],
        )
    except Exception as exc:
        return RepairResult(
            repository=f"{OWNER}/{target.name}",
            status="ERROR",
            branch=branch,
            blockers=[repr(exc)],
        )


def save_run(results: list[RepairResult]) -> Path:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = STATE_ROOT / f"run-{stamp}.json"
    payload = {
        "schema": "glaciereq.estate-function-repair-run.v1",
        "generated_at": now(),
        "results": [asdict(r) for r in results],
        "summary": {
            "total": len(results),
            "repaired_verified": sum(r.status == "REPAIRED_VERIFIED" for r in results),
            "not_verified": sum(r.status != "REPAIRED_VERIFIED" for r in results),
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest = STATE_ROOT / "latest.json"
    latest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Restore real function across the GlacierEQ native repo estate")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--repo", action="append", default=[], help="restrict to one or more repository names")
    p.add_argument("--priority-file", type=Path)
    p.add_argument("--limit", type=int, default=0, help="0 = all selected native repos")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--worker-cmd", default=os.environ.get("ESTATE_REPAIR_WORKER_CMD"))
    p.add_argument("--no-push", action="store_true")
    p.add_argument("--no-pr", action="store_true")
    p.add_argument("--list", action="store_true", help="print live selected estate and exit")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    priority = load_priority_names(args.priority_file)
    targets = discover_native_repositories()
    if args.repo:
        wanted = {name.removeprefix(f"{OWNER}/") for name in args.repo}
        targets = [t for t in targets if t.name in wanted]
    targets.sort(key=lambda t: target_priority(t, priority))
    if args.limit > 0:
        targets = targets[: args.limit]

    if args.list:
        print(json.dumps([asdict(t) for t in targets], indent=2))
        return 0

    if not targets:
        print("no repositories selected", file=sys.stderr)
        return 2

    results: list[RepairResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                repair_one,
                target,
                root=args.root,
                worker_cmd=args.worker_cmd,
                push=not args.no_push,
                open_pr=not args.no_pr,
            ): target
            for target in targets
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            blockers = "; ".join(result.blockers or [])
            print(f"[{result.status}] {result.repository} {blockers}", flush=True)

    results.sort(key=lambda r: r.repository.lower())
    receipt = save_run(results)
    repaired = sum(r.status == "REPAIRED_VERIFIED" for r in results)
    print(f"repaired_verified={repaired}/{len(results)}")
    print(f"receipt={receipt}")
    return 0 if repaired == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
