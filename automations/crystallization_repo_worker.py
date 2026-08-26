#!/usr/bin/env python3
"""One-repository CRYSTALLIZATION-MANDATE worker with source-preserving transport.

Protocol: read one JSON task payload from stdin and emit one JSON result to
stdout. The outer Swarm owns scheduling/retry/persistence. This worker owns the
repository work unit:

  recover intent -> model capabilities -> implement -> execute proof
  -> deployment proof where natural -> source-preserving branch/PR -> truth

Transport law:
- one stable continuation branch per repository;
- migrate forward from the newest legacy dated crystallization branch once;
- never delete useful local branch state merely to reset automation;
- checkpoint the exact previous remote head before every update;
- push only when the new head descends from the observed remote head;
- never force-push a crystallization branch;
- read back the exact remote SHA before claiming transmission success.
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
VALID_OUTCOMES = {
    "UNKNOWN", "DISCOVERED", "UNDERSTOOD", "BROKEN", "INCOMPLETE",
    "FUNCTIONAL", "COMPLETE", "DEPLOYED", *TERMINAL_STATES,
}
COMMAND_GROUPS = ("test_commands", "build_commands", "runtime_commands")
_LAST_TRANSPORT_RECEIPT: dict[str, Any] | None = None
_LAST_CHECKOUT_ANCESTRY = "UNKNOWN"


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


def _repo_slug(repository: str) -> str:
    value = validate_repo_name(repository)
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.split("/", 1)[1]).strip("-")[:64]


def safe_branch(repo_name: str) -> str:
    """Stable continuation branch. Generation truth lives in commits/ledger, not dates."""
    return f"crystallize/{_repo_slug(repo_name)}"


def checkpoint_branch(repo_name: str, source_sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("checkpoint_source_sha_invalid")
    return f"crystallize-checkpoints/{_repo_slug(repo_name)}/{source_sha[:12]}"


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
    output: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise ValueError(f"{label}_invalid")
        output.append(item)
    return output


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
    plan = validate_execution_plan(load_json(root / "execution-plan.json", "execution_plan"))

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
        if state not in {"WORKING", "PARTIAL", "BROKEN", "MISSING", "UNKNOWN", "OBSOLETE"}:
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
    gap_ids: set[str] = set()
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
            "group": label,
            "argv": argv,
            "returncode": result["returncode"],
            "stdout_sha256": hashlib.sha256(result["stdout"].encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(result["stderr"].encode("utf-8")).hexdigest(),
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
        "result", "target", "endpoint_or_artifact", "smoke_command",
        "smoke_returncode", "health", "readiness", "logs_or_run_ref",
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


def _git_stdout(repo: Path, argv: Sequence[str], label: str) -> str:
    result = run(["git", *argv], cwd=repo, timeout=180)
    require_success(result, label)
    return result["stdout"].strip()


def _remote_head(repo: Path, branch: str) -> str | None:
    result = run(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=repo,
        timeout=180,
    )
    require_success(result, "git_ls_remote")
    line = result["stdout"].strip()
    if not line:
        return None
    sha = line.split()[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("remote_head_invalid")
    return sha


def _local_branch_exists(repo: Path, branch: str) -> bool:
    result = run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo)
    return result["returncode"] == 0


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = run(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo)
    return result["returncode"] == 0


def _latest_legacy_remote_branch(repo: Path, repository: str) -> tuple[str, str] | None:
    slug = _repo_slug(repository)
    result = run(
        ["git", "for-each-ref", "--format=%(refname:short) %(objectname)",
         f"refs/remotes/origin/crystallize/*-{slug}"],
        cwd=repo,
        timeout=60,
    )
    require_success(result, "git_list_legacy_crystallization")
    candidates: list[tuple[str, str]] = []
    pattern = re.compile(rf"^origin/(crystallize/(\d{{8}})-{re.escape(slug)}) ([0-9a-f]{{40}})$")
    for line in result["stdout"].splitlines():
        match = pattern.match(line.strip())
        if match:
            candidates.append((match.group(1), match.group(3)))
    if not candidates:
        return None
    candidates.sort(key=lambda row: row[0])
    return candidates[-1]


def ensure_checkout_with_ancestry(
    repository: str, default_branch: str, root: Path
) -> tuple[Path, str, str]:
    """Return a clean continuation checkout without discarding unique local work."""
    global _LAST_CHECKOUT_ANCESTRY
    repository = validate_repo_name(repository)
    name = repository.split("/", 1)[1]
    repo = root / name
    root.mkdir(parents=True, exist_ok=True)

    if repo.exists():
        if not (repo / ".git").is_dir():
            raise RuntimeError("workspace_path_not_git_repo")
        status = run(["git", "status", "--porcelain"], cwd=repo, timeout=60)
        require_success(status, "git_status")
        if status["stdout"].strip():
            raise RuntimeError("workspace_dirty_refusing_to_overwrite")
        require_success(run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=900), "git_fetch")
    else:
        require_success(run(["gh", "repo", "clone", repository, str(repo)], timeout=1200), "gh_clone")
        require_success(run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=900), "git_fetch")

    default_remote = _remote_head(repo, default_branch)
    if not default_remote:
        raise RuntimeError("default_branch_remote_missing")

    branch = safe_branch(repository)
    stable_remote = _remote_head(repo, branch)
    legacy = None if stable_remote else _latest_legacy_remote_branch(repo, repository)
    start_sha = stable_remote or (legacy[1] if legacy else default_remote)
    ancestry = (
        "CONTINUE_STABLE_CRYSTALLIZATION"
        if stable_remote
        else "MIGRATE_LEGACY_CRYSTALLIZATION"
        if legacy
        else "START_FROM_DEFAULT_BRANCH"
    )

    if _local_branch_exists(repo, branch):
        local_sha = _git_stdout(repo, ["rev-parse", branch], "git_local_branch_head")
        if local_sha == start_sha:
            require_success(run(["git", "checkout", branch], cwd=repo, timeout=120), "git_checkout_continuation")
        elif _is_ancestor(repo, start_sha, local_sha):
            require_success(run(["git", "checkout", branch], cwd=repo, timeout=120), "git_checkout_local_ahead")
            ancestry += "+LOCAL_AHEAD_PRESERVED"
        elif _is_ancestor(repo, local_sha, start_sha):
            require_success(run(["git", "checkout", branch], cwd=repo, timeout=120), "git_checkout_local_behind")
            require_success(run(["git", "merge", "--ff-only", start_sha], cwd=repo, timeout=120), "git_fast_forward_local")
            ancestry += "+LOCAL_FAST_FORWARDED"
        else:
            raise RuntimeError("local_remote_crystallization_diverged_refusing_reset")
    else:
        require_success(
            run(["git", "checkout", "-b", branch, start_sha], cwd=repo, timeout=120),
            "git_checkout_new_stable_crystallization",
        )

    _LAST_CHECKOUT_ANCESTRY = ancestry
    return repo, branch, ancestry


def ensure_checkout(repository: str, default_branch: str, root: Path) -> tuple[Path, str]:
    repo, branch, _ancestry = ensure_checkout_with_ancestry(repository, default_branch, root)
    return repo, branch


def implementer_prompt(repository: str) -> str:
    return f"""CRYSTALLIZATION-MANDATE repository work unit: {repository}

You are the implementation worker, not the auditor. Inspect total repository
evidence before changing code: source, git history, README/docs, tests, package
metadata, API/schema shapes, deployments/configuration, generated artifacts,
and predecessor/successor clues.

Recover first principles: what the repository is, why it exists, the outcome it
exists to produce, and its complete material capability set. Then repair forward
until it is the strongest supportable complete expression of that purpose.
Preserve strong mechanisms/history/provenance. No placeholders, fake
integrations, fake APIs, TODO-as-implementation, mock-as-final, gate theater,
README theater, or green-CI-as-completion.

Keep these machine artifacts truthful and synchronized:
- machine/crystallization/purpose-manifest.json
- machine/crystallization/capability-manifest.json
- machine/crystallization/gap-matrix.json
- machine/crystallization/execution-plan.json

execution-plan.json must contain naturally_deployable plus executable argv lists
for test_commands, build_commands, and runtime_commands. If naturally
deployable, actual deployment proof belongs in deployment-receipt.json. Never
fabricate it. Do not write the completion receipt; the outer worker independently
executes proof and decides terminal status.
"""


def invoke_implementer(repo: Path, repository: str) -> dict[str, Any]:
    raw = os.environ.get("CRYSTALLIZATION_IMPLEMENTER_CMD")
    if not raw:
        raise RuntimeError("CRYSTALLIZATION_IMPLEMENTER_CMD_missing")
    argv = shlex.split(raw)
    if not argv:
        raise RuntimeError("CRYSTALLIZATION_IMPLEMENTER_CMD_invalid")
    return run(
        [*argv, implementer_prompt(repository)],
        cwd=repo,
        timeout=int(os.environ.get("CRYSTALLIZATION_IMPLEMENTER_TIMEOUT", "7200")),
    )


def commit_functional_delta(repo: Path) -> str:
    status = run(["git", "status", "--porcelain"], cwd=repo)
    require_success(status, "git_status_after_implementation")
    if status["stdout"].strip():
        run(["git", "config", "user.name", os.environ.get("CRYSTALLIZATION_GIT_NAME", "GlacierEQ Crystallization")], cwd=repo)
        run(["git", "config", "user.email", os.environ.get("CRYSTALLIZATION_GIT_EMAIL", "crystallization@local.invalid")], cwd=repo)
        require_success(run(["git", "add", "-A"], cwd=repo), "git_add")
        require_success(run(["git", "commit", "-m", "crystallize: advance repository purpose"], cwd=repo), "git_commit_functional")
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
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    require_success(run(["git", "add", path.relative_to(repo).as_posix()], cwd=repo), "git_add_receipt")
    require_success(run(["git", "commit", "-m", "crystallize: bind completion receipt"], cwd=repo), "git_commit_receipt")
    head = run(["git", "rev-parse", "HEAD"], cwd=repo)
    require_success(head, "git_receipt_head")
    return head["stdout"].strip()


def _ensure_remote_checkpoint(repo: Path, repository: str, remote_sha: str) -> str:
    checkpoint = checkpoint_branch(repository, remote_sha)
    existing = _remote_head(repo, checkpoint)
    if existing:
        if existing != remote_sha:
            raise RuntimeError("crystallization_checkpoint_collision")
        return checkpoint
    require_success(
        run(
            ["git", "push", "origin", f"{remote_sha}:refs/heads/{checkpoint}"],
            cwd=repo,
            timeout=900,
        ),
        "git_push_checkpoint",
    )
    readback = _remote_head(repo, checkpoint)
    if readback != remote_sha:
        raise RuntimeError("crystallization_checkpoint_readback_mismatch")
    return checkpoint


def get_last_transport_receipt() -> dict[str, Any] | None:
    return dict(_LAST_TRANSPORT_RECEIPT) if _LAST_TRANSPORT_RECEIPT else None


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
    """Transmit a continuation branch without history rewrite, then read it back."""
    global _LAST_TRANSPORT_RECEIPT
    local_head = _git_stdout(repo, ["rev-parse", "HEAD"], "git_transport_local_head")
    if not re.fullmatch(r"[0-9a-f]{40}", local_head):
        raise RuntimeError("transport_local_head_invalid")

    remote_before = _remote_head(repo, branch)
    checkpoint = None
    if remote_before:
        checkpoint = _ensure_remote_checkpoint(repo, repository, remote_before)
        if not _is_ancestor(repo, remote_before, local_head):
            _LAST_TRANSPORT_RECEIPT = {
                "schema": "glaciereq.crystallization-transport.v2",
                "state": "DIVERGENCE_REFUSED",
                "repository": repository,
                "branch": branch,
                "remote_before": remote_before,
                "local_head": local_head,
                "checkpoint_branch": checkpoint,
                "force_push": False,
                "observed_at": _now(),
            }
            raise RuntimeError("remote_crystallization_diverged_refusing_force_push")

    if not push:
        _LAST_TRANSPORT_RECEIPT = {
            "schema": "glaciereq.crystallization-transport.v2",
            "state": "LOCAL_ONLY",
            "repository": repository,
            "branch": branch,
            "remote_before": remote_before,
            "local_head": local_head,
            "checkpoint_branch": checkpoint,
            "force_push": False,
            "observed_at": _now(),
        }
        return None

    require_success(
        run(["git", "push", "-u", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo, timeout=900),
        "git_push_descendant_only",
    )
    remote_after = _remote_head(repo, branch)
    if remote_after != local_head:
        raise RuntimeError("crystallization_remote_readback_mismatch")

    _LAST_TRANSPORT_RECEIPT = {
        "schema": "glaciereq.crystallization-transport.v2",
        "state": "PUSHED_AND_READ_BACK",
        "repository": repository,
        "branch": branch,
        "default_branch": default_branch,
        "remote_before": remote_before,
        "local_head": local_head,
        "remote_after": remote_after,
        "checkpoint_branch": checkpoint,
        "force_push": False,
        "push_mode": "NORMAL_DESCENDANT_ONLY",
        "observed_at": _now(),
    }

    if not open_pr:
        return None
    title = f"CRYSTALLIZATION: {repository.split('/', 1)[1]} — {status}"
    checkpoint_line = f" Prior remote head preserved at `{checkpoint}`." if checkpoint else ""
    body = (
        "CRYSTALLIZATION-MANDATE work unit. Purpose/capability/gap manifests are the authority. "
        f"Current status: **{status}**. Transmission head `{remote_after}` was read back from origin."
        f"{checkpoint_line} No force-push was used."
    )
    result = run(
        [
            "gh", "pr", "create", "--repo", repository, "--base", default_branch,
            "--head", branch, "--title", title, "--body", body,
        ],
        cwd=repo,
        timeout=180,
    )
    if result["returncode"] != 0:
        lookup = run(
            ["gh", "pr", "view", branch, "--repo", repository, "--json", "url", "--jq", ".url"],
            cwd=repo,
        )
        if lookup["returncode"] == 0:
            url = lookup["stdout"].strip() or None
            if _LAST_TRANSPORT_RECEIPT is not None:
                _LAST_TRANSPORT_RECEIPT["pr_url"] = url
                _LAST_TRANSPORT_RECEIPT["pr_state"] = "EXISTING_PR_REUSED"
            return url
        raise RuntimeError("pr_create_failed:" + (result["stderr"] or result["stdout"])[-1000:])
    url = result["stdout"].strip() or None
    if _LAST_TRANSPORT_RECEIPT is not None:
        _LAST_TRANSPORT_RECEIPT["pr_url"] = url
        _LAST_TRANSPORT_RECEIPT["pr_state"] = "PR_OPENED"
    return url


def _proof(repo: Path, model: Mapping[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    plan = model["plan"]
    test_ok, tests = execute_commands(repo, plan["test_commands"], "test")
    build_ok, builds = execute_commands(repo, plan["build_commands"], "build")
    runtime_ok, runtime = execute_commands(repo, plan["runtime_commands"], "runtime")
    deployment_result, deployment_receipt = deployment_proof(repo, plan["naturally_deployable"])
    proof = {
        "test_ok": test_ok,
        "build_ok": build_ok,
        "runtime_ok": runtime_ok,
        "tests": tests,
        "build": builds,
        "runtime": runtime,
        "proof_digest": _digest(
            {
                "tests": tests,
                "build": builds,
                "runtime": runtime,
                "deployment": deployment_receipt,
            }
        ),
    }
    return proof, deployment_result, deployment_receipt


def process(payload: Mapping[str, Any]) -> dict[str, Any]:
    repository = validate_repo_name(payload.get("repository"))
    default_branch = payload.get("default_branch", "main")
    if not isinstance(default_branch, str) or not default_branch.strip():
        raise ValueError("default_branch_invalid")
    root = Path(os.environ.get("CRYSTALLIZATION_WORKSPACE_ROOT", "/data/crystallization-repos")).resolve()
    push = bool(payload.get("push", True))
    open_pr = bool(payload.get("open_pr", True))
    repo, branch, ancestry = ensure_checkout_with_ancestry(repository, default_branch, root)

    implementer = invoke_implementer(repo, repository)
    worker_tail = (implementer["stdout"] + implementer["stderr"])[-4000:]
    if implementer["returncode"] != 0:
        return {
            "repository": repository,
            "status": "INCOMPLETE",
            "branch": branch,
            "ancestry": ancestry,
            "reason": "IMPLEMENTER_FAILED",
            "worker_tail": worker_tail,
        }

    source_sha = commit_functional_delta(repo)
    model = validate_crystallization_model(repo)
    proof, deployment_result, deployment_receipt = _proof(repo, model)
    open_gaps = list(model["open_material_capabilities"])

    if not proof["test_ok"] or not proof["build_ok"] or not proof["runtime_ok"] or deployment_result == "FAIL":
        status = "BROKEN"
    elif open_gaps:
        status = "INCOMPLETE"
    elif model["plan"]["naturally_deployable"] and deployment_result != "PASS":
        status = "INCOMPLETE"
    else:
        status = "CRYSTALLIZED"

    receipt_head = None
    if status == "CRYSTALLIZED":
        receipt_head = write_completion_receipt(
            repo, repository, model, source_sha, proof, deployment_result
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
        "ancestry": ancestry,
        "verified_source_sha": source_sha,
        "receipt_head_sha": receipt_head,
        "capability_count": model["capability_count"],
        "material_capability_count": model["material_capability_count"],
        "working_material_capability_count": model["working_material_capability_count"],
        "remaining_gap_count": len(open_gaps),
        "remaining_gaps": open_gaps,
        "proof": proof,
        "deployment_result": deployment_result,
        "deployment_receipt": deployment_receipt,
        "transport": get_last_transport_receipt(),
        "pr_url": pr_url,
        "worker_tail": worker_tail,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("task_payload_must_be_object")
        result = process(payload)
        if result.get("status") not in VALID_OUTCOMES:
            raise RuntimeError("work_unit_status_internal_error")
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps({"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"}, sort_keys=True),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
