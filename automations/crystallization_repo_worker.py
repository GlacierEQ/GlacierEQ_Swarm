#!/usr/bin/env python3
"""One-repository CRYSTALLIZATION-MANDATE worker.

Protocol: read one JSON task payload from stdin and emit one JSON result to
stdout. The outer Swarm owns scheduling/retry/persistence. This worker owns the
repository work unit:

  recover intent -> build full capability model -> implement -> execute proof
  -> deploy proof where natural -> branch/PR -> truthful status

The implementation agent is operator-configured via
`CRYSTALLIZATION_IMPLEMENTER_CMD`; task submitters cannot replace that command.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

OWNER = "GlacierEQ"
OPEN_STATES = {"MISSING", "BROKEN", "PARTIAL", "UNKNOWN"}
TERMINAL_STATES = {"CRYSTALLIZED", "CANONICALIZED_SUCCESSOR", "INTENTIONALLY_ARCHIVED"}
COMMAND_GROUPS = ("test_commands", "build_commands", "runtime_commands")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
    input_text: str | None = None,
) -> dict[str, Any]:
    proc = subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "argv": list(argv),
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def require_success(result: Mapping[str, Any], label: str) -> None:
    if int(result.get("returncode", 1)) != 0:
        tail = (str(result.get("stdout", "")) + str(result.get("stderr", "")))[-3000:]
        raise RuntimeError(f"{label}_failed:{tail}")


def validate_repo_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("repository_missing")
    value = value.strip()
    if "/" not in value:
        value = f"{OWNER}/{value}"
    owner, name = value.split("/", 1)
    if owner != OWNER or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("repository_invalid")
    return value


def safe_branch(repo_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo_name.split("/", 1)[1])[:45]
    return f"crystallize/{stamp}-{slug}"


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"{label}_missing:{path.as_posix()}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label}_must_be_object")
    return value


def validate_command(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}_invalid")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError(f"{label}_invalid")
        out.append(item)
    return out


def validate_execution_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for group in COMMAND_GROUPS:
        rows = plan.get(group)
        if not isinstance(rows, list):
            raise ValueError(f"{group}_missing")
        normalized[group] = [validate_command(row, group) for row in rows]
    if not normalized["test_commands"]:
        raise ValueError("test_commands_empty")
    if not normalized["runtime_commands"]:
        raise ValueError("runtime_commands_empty")
    deployable = plan.get("naturally_deployable")
    if not isinstance(deployable, bool):
        raise ValueError("naturally_deployable_missing")
    normalized["naturally_deployable"] = deployable
    return normalized


def validate_crystallization_model(repo: Path) -> dict[str, Any]:
    root = repo / "machine" / "crystallization"
    purpose = load_json(root / "purpose-manifest.json", "purpose_manifest")
    capabilities = load_json(root / "capability-manifest.json", "capability_manifest")
    gaps = load_json(root / "gap-matrix.json", "gap_matrix")
    plan = validate_execution_plan(
        load_json(root / "execution-plan.json", "execution_plan")
    )

    for key in ("purpose", "problem", "intended_outcome", "system_kind"):
        if not isinstance(purpose.get(key), str) or not purpose[key].strip():
            raise ValueError(f"purpose_manifest_{key}_missing")
    if purpose.get("naturally_deployable") is not plan["naturally_deployable"]:
        raise ValueError("deployment_intent_mismatch")

    rows = capabilities.get("capabilities")
    if not isinstance(rows, list) or not rows:
        raise ValueError("capability_inventory_empty")
    ids: set[str] = set()
    open_material: list[dict[str, Any]] = []
    material_count = 0
    working_material = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("capability_row_invalid")
        cap_id = row.get("id")
        state = row.get("state")
        material = row.get("material")
        if not isinstance(cap_id, str) or not cap_id.strip() or cap_id in ids:
            raise ValueError("capability_id_invalid_or_duplicate")
        ids.add(cap_id)
        if not isinstance(material, bool):
            raise ValueError(f"capability_material_invalid:{cap_id}")
        if state not in {
            "WORKING",
            "PARTIAL",
            "BROKEN",
            "MISSING",
            "UNKNOWN",
            "OBSOLETE",
        }:
            raise ValueError(f"capability_state_invalid:{cap_id}")
        if material:
            material_count += 1
            if state == "WORKING":
                working_material += 1
            elif state in OPEN_STATES:
                open_material.append(row)

    gap_rows = gaps.get("gaps")
    if not isinstance(gap_rows, list):
        raise ValueError("gap_matrix_rows_invalid")
    gap_ids = set()
    for row in gap_rows:
        if not isinstance(row, dict) or not isinstance(row.get("capability_id"), str):
            raise ValueError("gap_row_invalid")
        cap_id = row["capability_id"]
        if cap_id in gap_ids:
            raise ValueError("duplicate_gap_id")
        gap_ids.add(cap_id)
    expected_gaps = {row["id"] for row in open_material}
    if gap_ids != expected_gaps:
        raise ValueError(
            f"gap_matrix_mismatch:expected={sorted(expected_gaps)}:actual={sorted(gap_ids)}"
        )

    return {
        "purpose": purpose,
        "capabilities": capabilities,
        "gaps": gaps,
        "plan": plan,
        "capability_count": len(rows),
        "material_capability_count": material_count,
        "working_material_capability_count": working_material,
        "open_material_capabilities": sorted(expected_gaps),
    }


def execute_commands(
    repo: Path, commands: Iterable[list[str]], label: str
) -> tuple[bool, list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    okay = True
    for argv in commands:
        result = run(argv, cwd=repo, timeout=1800)
        receipt = {
            "argv": argv,
            "returncode": result["returncode"],
            "stdout_sha256": hashlib.sha256(
                result["stdout"].encode("utf-8")
            ).hexdigest(),
            "stderr_sha256": hashlib.sha256(
                result["stderr"].encode("utf-8")
            ).hexdigest(),
            "stdout_tail": result["stdout"][-2000:],
            "stderr_tail": result["stderr"][-2000:],
        }
        receipts.append(receipt)
        if result["returncode"] != 0:
            okay = False
            break
    return okay, receipts


def deployment_proof(repo: Path, required: bool) -> tuple[str, dict[str, Any] | None]:
    path = repo / "machine" / "crystallization" / "deployment-receipt.json"
    if not required:
        return "NOT_APPLICABLE", None
    receipt = load_json(path, "deployment_receipt")
    required_fields = (
        "result",
        "target",
        "endpoint_or_artifact",
        "smoke_command",
        "smoke_returncode",
        "health",
        "readiness",
        "logs_or_run_ref",
        "rollback_mechanism",
    )
    for key in required_fields:
        if key not in receipt:
            raise ValueError(f"deployment_receipt_{key}_missing")
    if receipt["result"] != "PASS" or int(receipt["smoke_returncode"]) != 0:
        return "FAIL", receipt
    if receipt["health"] != "PASS" or receipt["readiness"] != "PASS":
        return "FAIL", receipt
    return "PASS", receipt


def ensure_checkout(
    repository: str, default_branch: str, root: Path
) -> tuple[Path, str]:
    name = repository.split("/", 1)[1]
    repo = root / name
    root.mkdir(parents=True, exist_ok=True)
    if repo.exists():
        if not (repo / ".git").is_dir():
            raise RuntimeError("workspace_path_not_git_repo")
        dirty = run(["git", "status", "--porcelain"], cwd=repo, timeout=60)
        require_success(dirty, "git_status")
        if dirty["stdout"].strip():
            raise RuntimeError("workspace_dirty_refusing_to_overwrite")
        require_success(
            run(["git", "fetch", "origin", "--prune"], cwd=repo), "git_fetch"
        )
    else:
        require_success(
            run(["gh", "repo", "clone", repository, str(repo)], timeout=900), "gh_clone"
        )
    require_success(
        run(["git", "checkout", default_branch], cwd=repo), "git_checkout_default"
    )
    require_success(
        run(["git", "reset", "--hard", f"origin/{default_branch}"], cwd=repo),
        "git_reset_default",
    )
    branch = safe_branch(repository)
    existing = run(["git", "branch", "--list", branch], cwd=repo)
    require_success(existing, "git_branch_list")
    if existing["stdout"].strip():
        require_success(
            run(["git", "branch", "-D", branch], cwd=repo), "git_branch_delete_local"
        )
    require_success(
        run(["git", "checkout", "-b", branch, f"origin/{default_branch}"], cwd=repo),
        "git_checkout_crystallize",
    )
    return repo, branch


def implementer_prompt(repository: str) -> str:
    return f"""CRYSTALLIZATION-MANDATE repository work unit: {repository}

You are the implementation worker, not the auditor. Inspect the TOTAL repository evidence before changing code: source, full git history, README/docs, tests, issues/PR evidence available locally or through gh, branches/tags, package metadata, API/schema shapes, deployments/configuration, generated artifacts, and neighboring/successor/predecessor evidence discoverable from the repository.

Recover first principles:
- what the repository is;
- why it exists;
- the outcome it exists to produce;
- its complete material capability set, including capabilities implied by purpose even when historical code never finished them.

Then REPAIR FORWARD AND EVOLVE the repository until it is the strongest supportable complete expression of that purpose. Preserve strong mechanisms/history/provenance. Replace dead architecture only when it obstructs function. Never add a language or abstraction for decoration. No placeholders, fake integrations, fake APIs, TODO-as-implementation, mock-as-final, gate theater, README theater, or green-CI-as-completion.

You MUST leave these machine artifacts truthful and synchronized with the implementation:
- machine/crystallization/purpose-manifest.json
- machine/crystallization/capability-manifest.json
- machine/crystallization/gap-matrix.json
- machine/crystallization/execution-plan.json

execution-plan.json schema:
{{
  "naturally_deployable": true_or_false,
  "test_commands": [["executable", "arg", "..."]],
  "build_commands": [["executable", "arg", "..."]],
  "runtime_commands": [["executable", "arg", "..."]]
}}
Commands must execute real behavior without a shell. Include multiple commands when the purpose requires multiple paths.

If naturally deployable, deployment is part of completion. Actually deploy through the repository's available deployment mechanism and write machine/crystallization/deployment-receipt.json with: result, target, endpoint_or_artifact, smoke_command, smoke_returncode, health, readiness, logs_or_run_ref, rollback_mechanism. Do not invent any field.

Do not write a completion receipt. This outer worker independently executes the declared proof and decides terminal status.

Do not stop after one repaired function. Complete every material capability implied by the purpose, or leave it explicitly PARTIAL/BROKEN/MISSING in the capability manifest and gap matrix. The only acceptable false-completion count is zero.
"""


def invoke_implementer(repo: Path, repository: str) -> dict[str, Any]:
    raw = os.environ.get("CRYSTALLIZATION_IMPLEMENTER_CMD")
    if not raw:
        raise RuntimeError("CRYSTALLIZATION_IMPLEMENTER_CMD_missing")
    argv = shlex.split(raw)
    if not argv:
        raise RuntimeError("CRYSTALLIZATION_IMPLEMENTER_CMD_invalid")
    prompt = implementer_prompt(repository)
    # The prompt is appended as one argv value. The operator chooses a command
    # that accepts its instruction as the final positional argument.
    return run(
        [*argv, prompt],
        cwd=repo,
        timeout=int(os.environ.get("CRYSTALLIZATION_IMPLEMENTER_TIMEOUT", "7200")),
    )


def commit_functional_delta(repo: Path) -> str:
    status = run(["git", "status", "--porcelain"], cwd=repo)
    require_success(status, "git_status_after_implementation")
    if status["stdout"].strip():
        run(
            [
                "git",
                "config",
                "user.name",
                os.environ.get("CRYSTALLIZATION_GIT_NAME", "GlacierEQ Crystallization"),
            ],
            cwd=repo,
        )
        run(
            [
                "git",
                "config",
                "user.email",
                os.environ.get(
                    "CRYSTALLIZATION_GIT_EMAIL", "crystallization@local.invalid"
                ),
            ],
            cwd=repo,
        )
        require_success(run(["git", "add", "-A"], cwd=repo), "git_add")
        require_success(
            run(
                ["git", "commit", "-m", "crystallize: complete repository purpose"],
                cwd=repo,
            ),
            "git_commit_functional",
        )
    head = run(["git", "rev-parse", "HEAD"], cwd=repo)
    require_success(head, "git_rev_parse")
    value = head["stdout"].strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("functional_head_invalid")
    return value


def write_completion_receipt(
    repo: Path,
    repository: str,
    model: Mapping[str, Any],
    source_sha: str,
    proof: Mapping[str, Any],
    deployment_result: str,
) -> str:
    root = repo / "machine" / "crystallization"
    receipt = {
        "schema": "glaciereq.crystallization-completion.v1",
        "mandate": "CRYSTALLIZATION-MANDATE@1.0",
        "repository_id": repository,
        "canonical_purpose": model["purpose"]["purpose"],
        "intended_outcome": model["purpose"]["intended_outcome"],
        "system_kind": model["purpose"]["system_kind"],
        "verified_source_sha": source_sha,
        "capability_count": model["capability_count"],
        "implemented_capability_count": model["working_material_capability_count"],
        "verified_capability_count": model["working_material_capability_count"],
        "unresolved_gaps": [],
        "build_result": "PASS" if proof["build_ok"] else "FAIL",
        "test_result": "PASS" if proof["test_ok"] else "FAIL",
        "runtime_result": "PASS" if proof["runtime_ok"] else "FAIL",
        "deployment_result": deployment_result,
        "proof_artifacts": [
            "machine/crystallization/purpose-manifest.json",
            "machine/crystallization/capability-manifest.json",
            "machine/crystallization/gap-matrix.json",
            "machine/crystallization/execution-plan.json",
        ],
        "final_status": "CRYSTALLIZED",
        "terminal_truth": True,
        "verified_at": _now(),
    }
    path = root / "completion-receipt.json"
    path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    require_success(
        run(["git", "add", path.relative_to(repo).as_posix()], cwd=repo),
        "git_add_receipt",
    )
    require_success(
        run(["git", "commit", "-m", "crystallize: bind completion receipt"], cwd=repo),
        "git_commit_receipt",
    )
    head = run(["git", "rev-parse", "HEAD"], cwd=repo)
    require_success(head, "git_receipt_head")
    return head["stdout"].strip()


def maybe_push_and_pr(
    repo: Path,
    repository: str,
    branch: str,
    default_branch: str,
    *,
    push: bool,
    open_pr: bool,
    status: str,
) -> str | None:
    if not push:
        return None
    require_success(
        run(
            ["git", "push", "-u", "origin", branch, "--force-with-lease"],
            cwd=repo,
            timeout=900,
        ),
        "git_push",
    )
    if not open_pr:
        return None
    title = f"CRYSTALLIZATION: {repository.split('/', 1)[1]} — {status}"
    body = (
        "CRYSTALLIZATION-MANDATE work unit. Purpose/capability/gap manifests are the authority. "
        f"Current status: **{status}**. No unresolved material gap is hidden by CI or documentation."
    )
    result = run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            default_branch,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo,
        timeout=180,
    )
    if result["returncode"] != 0:
        # Existing PR is not a repository failure; return a discoverable lookup.
        lookup = run(
            [
                "gh",
                "pr",
                "view",
                branch,
                "--repo",
                repository,
                "--json",
                "url",
                "--jq",
                ".url",
            ],
            cwd=repo,
        )
        if lookup["returncode"] == 0:
            return lookup["stdout"].strip() or None
        raise RuntimeError(
            "pr_create_failed:" + (result["stderr"] or result["stdout"])[-1000:]
        )
    return result["stdout"].strip() or None


def process(payload: Mapping[str, Any]) -> dict[str, Any]:
    repository = validate_repo_name(payload.get("repository"))
    default_branch = payload.get("default_branch", "main")
    if not isinstance(default_branch, str) or not default_branch.strip():
        raise ValueError("default_branch_invalid")
    root = Path(
        os.environ.get("CRYSTALLIZATION_WORKSPACE_ROOT", "/data/crystallization-repos")
    ).resolve()
    push = bool(payload.get("push", True))
    open_pr = bool(payload.get("open_pr", True))
    repo, branch = ensure_checkout(repository, default_branch, root)

    implementer = invoke_implementer(repo, repository)
    if implementer["returncode"] != 0:
        return {
            "repository": repository,
            "status": "INCOMPLETE",
            "branch": branch,
            "reason": "IMPLEMENTER_FAILED",
            "worker_tail": (implementer["stdout"] + implementer["stderr"])[-3000:],
        }

    source_sha = commit_functional_delta(repo)
    model = validate_crystallization_model(repo)
    plan = model["plan"]
    test_ok, test_receipts = execute_commands(repo, plan["test_commands"], "test")
    build_ok, build_receipts = execute_commands(repo, plan["build_commands"], "build")
    runtime_ok, runtime_receipts = execute_commands(
        repo, plan["runtime_commands"], "runtime"
    )
    deploy_result, deploy_receipt = deployment_proof(repo, plan["naturally_deployable"])

    proof = {
        "test_ok": test_ok,
        "build_ok": build_ok,
        "runtime_ok": runtime_ok,
        "tests": test_receipts,
        "build": build_receipts,
        "runtime": runtime_receipts,
        "proof_digest": _digest(
            {
                "source_sha": source_sha,
                "tests": test_receipts,
                "build": build_receipts,
                "runtime": runtime_receipts,
                "deployment": deploy_receipt,
            }
        ),
    }

    open_gaps = model["open_material_capabilities"]
    if not test_ok or not build_ok or not runtime_ok or deploy_result == "FAIL":
        status = "BROKEN"
    elif open_gaps:
        status = "INCOMPLETE"
    elif plan["naturally_deployable"] and deploy_result != "PASS":
        status = "INCOMPLETE"
    else:
        status = "CRYSTALLIZED"

    receipt_head = None
    if status == "CRYSTALLIZED":
        receipt_head = write_completion_receipt(
            repo, repository, model, source_sha, proof, deploy_result
        )

    pr_url = maybe_push_and_pr(
        repo,
        repository,
        branch,
        default_branch,
        push=push,
        open_pr=open_pr,
        status=status,
    )
    return {
        "repository": repository,
        "status": status,
        "branch": branch,
        "verified_source_sha": source_sha,
        "receipt_head_sha": receipt_head,
        "capability_count": model["capability_count"],
        "material_capability_count": model["material_capability_count"],
        "working_material_capability_count": model["working_material_capability_count"],
        "remaining_gap_count": len(open_gaps),
        "remaining_gaps": open_gaps,
        "proof": proof,
        "deployment_result": deploy_result,
        "pr_url": pr_url,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("task_payload_must_be_object")
        result = process(payload)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] in TERMINAL_STATES else 2
    except Exception as exc:
        print(
            json.dumps(
                {"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"},
                sort_keys=True,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
