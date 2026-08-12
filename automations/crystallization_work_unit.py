#!/usr/bin/env python3
"""Resumable one-repository CRYSTALLIZATION-MANDATE work unit.

Unlike the first repository-worker transport, this executable makes repository
truth and process health separate by construction:

- valid work-unit outcomes (CRYSTALLIZED / INCOMPLETE / BROKEN / etc.) exit 0;
- protocol/infrastructure failures exit non-zero;
- repeated generations continue the existing remote crystallization branch
  instead of resetting to default-branch ancestry and erasing earlier gains.

The implementation command remains operator-controlled through
`CRYSTALLIZATION_IMPLEMENTER_CMD`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import crystallization_repo_worker as core

VALID_OUTCOMES = {
    "UNKNOWN",
    "DISCOVERED",
    "UNDERSTOOD",
    "BROKEN",
    "INCOMPLETE",
    "FUNCTIONAL",
    "COMPLETE",
    "DEPLOYED",
    "CRYSTALLIZED",
    "CANONICALIZED_SUCCESSOR",
    "INTENTIONALLY_ARCHIVED",
}


def _remote_branch_exists(repo: Path, branch: str) -> bool:
    result = core.run(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=repo, timeout=120)
    return result["returncode"] == 0 and bool(result["stdout"].strip())


def ensure_continuation_checkout(repository: str, default_branch: str, root: Path) -> tuple[Path, str, str]:
    name = repository.split("/", 1)[1]
    repo = root / name
    root.mkdir(parents=True, exist_ok=True)
    if repo.exists():
        if not (repo / ".git").is_dir():
            raise RuntimeError("workspace_path_not_git_repo")
        status = core.run(["git", "status", "--porcelain"], cwd=repo, timeout=60)
        core.require_success(status, "git_status")
        if status["stdout"].strip():
            raise RuntimeError("workspace_dirty_refusing_to_overwrite")
        core.require_success(core.run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=900), "git_fetch")
    else:
        core.require_success(core.run(["gh", "repo", "clone", repository, str(repo)], timeout=1200), "gh_clone")
        core.require_success(core.run(["git", "fetch", "origin", "--prune"], cwd=repo, timeout=900), "git_fetch")

    branch = core.safe_branch(repository)
    if _remote_branch_exists(repo, branch):
        core.require_success(
            core.run(["git", "checkout", "-B", branch, f"origin/{branch}"], cwd=repo, timeout=120),
            "git_checkout_existing_crystallization",
        )
        ancestry = "CONTINUE_REMOTE_CRYSTALLIZATION"
    else:
        core.require_success(
            core.run(["git", "checkout", "-B", default_branch, f"origin/{default_branch}"], cwd=repo, timeout=120),
            "git_checkout_default",
        )
        core.require_success(
            core.run(["git", "checkout", "-B", branch, f"origin/{default_branch}"], cwd=repo, timeout=120),
            "git_checkout_new_crystallization",
        )
        ancestry = "START_FROM_DEFAULT_BRANCH"
    return repo, branch, ancestry


def _safe_model(repo: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return core.validate_crystallization_model(repo), None
    except Exception as exc:
        return None, f"{type(exc).__name__}:{exc}"


def _proof(repo: Path, model: Mapping[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    plan = model["plan"]
    test_ok, test_receipts = core.execute_commands(repo, plan["test_commands"], "test")
    build_ok, build_receipts = core.execute_commands(repo, plan["build_commands"], "build")
    runtime_ok, runtime_receipts = core.execute_commands(repo, plan["runtime_commands"], "runtime")
    deployment_result, deployment_receipt = core.deployment_proof(repo, plan["naturally_deployable"])
    proof = {
        "test_ok": test_ok,
        "build_ok": build_ok,
        "runtime_ok": runtime_ok,
        "tests": test_receipts,
        "build": build_receipts,
        "runtime": runtime_receipts,
        "proof_digest": core._digest(
            {
                "tests": test_receipts,
                "build": build_receipts,
                "runtime": runtime_receipts,
                "deployment": deployment_receipt,
            }
        ),
    }
    return proof, deployment_result, deployment_receipt


def process(payload: Mapping[str, Any]) -> dict[str, Any]:
    repository = core.validate_repo_name(payload.get("repository"))
    default_branch = payload.get("default_branch", "main")
    if not isinstance(default_branch, str) or not default_branch.strip():
        raise ValueError("default_branch_invalid")
    root = Path(os.environ.get("CRYSTALLIZATION_WORKSPACE_ROOT", "/data/crystallization-repos")).resolve()
    push = bool(payload.get("push", True))
    open_pr = bool(payload.get("open_pr", True))

    repo, branch, ancestry = ensure_continuation_checkout(repository, default_branch, root)
    before = core.run(["git", "rev-parse", "HEAD"], cwd=repo)
    core.require_success(before, "git_initial_head")
    initial_head = before["stdout"].strip()

    implementer = core.invoke_implementer(repo, repository)
    worker_tail = (implementer["stdout"] + implementer["stderr"])[-4000:]
    if implementer["returncode"] != 0:
        # The implementation attempt ran and failed. That is repository/work-unit
        # truth, not a Swarm transport failure.
        return {
            "repository": repository,
            "status": "INCOMPLETE",
            "branch": branch,
            "ancestry": ancestry,
            "initial_head_sha": initial_head,
            "reason": "IMPLEMENTER_FAILED",
            "remaining_gap_count": None,
            "worker_tail": worker_tail,
        }

    source_sha = core.commit_functional_delta(repo)
    model, model_error = _safe_model(repo)
    if model is None:
        pr_url = core.maybe_push_and_pr(
            repo,
            repository,
            branch,
            default_branch,
            push=push,
            open_pr=open_pr,
            status="BROKEN",
        )
        return {
            "repository": repository,
            "status": "BROKEN",
            "branch": branch,
            "ancestry": ancestry,
            "initial_head_sha": initial_head,
            "verified_source_sha": source_sha,
            "reason": "CRYSTALLIZATION_MODEL_INVALID",
            "model_error": model_error,
            "remaining_gap_count": None,
            "pr_url": pr_url,
            "worker_tail": worker_tail,
        }

    try:
        proof, deployment_result, deployment_receipt = _proof(repo, model)
        proof_error = None
    except Exception as exc:
        proof = {
            "test_ok": False,
            "build_ok": False,
            "runtime_ok": False,
            "tests": [],
            "build": [],
            "runtime": [],
            "proof_digest": None,
        }
        deployment_result = "FAIL"
        deployment_receipt = None
        proof_error = f"{type(exc).__name__}:{exc}"

    open_gaps = list(model["open_material_capabilities"])
    if proof_error is not None or not proof["test_ok"] or not proof["build_ok"] or not proof["runtime_ok"] or deployment_result == "FAIL":
        status = "BROKEN"
    elif open_gaps:
        status = "INCOMPLETE"
    elif model["plan"]["naturally_deployable"] and deployment_result != "PASS":
        status = "INCOMPLETE"
    else:
        status = "CRYSTALLIZED"

    receipt_head = None
    if status == "CRYSTALLIZED":
        receipt_head = core.write_completion_receipt(
            repo,
            repository,
            model,
            source_sha,
            proof,
            deployment_result,
        )

    pr_url = core.maybe_push_and_pr(
        repo,
        repository,
        branch,
        default_branch,
        push=push,
        open_pr=open_pr,
        status=status,
    )
    result = {
        "repository": repository,
        "status": status,
        "branch": branch,
        "ancestry": ancestry,
        "initial_head_sha": initial_head,
        "verified_source_sha": source_sha,
        "receipt_head_sha": receipt_head,
        "capability_count": model["capability_count"],
        "material_capability_count": model["material_capability_count"],
        "working_material_capability_count": model["working_material_capability_count"],
        "remaining_gap_count": len(open_gaps),
        "remaining_gaps": open_gaps,
        "proof": proof,
        "proof_error": proof_error,
        "deployment_result": deployment_result,
        "deployment_receipt": deployment_receipt,
        "pr_url": pr_url,
        "worker_tail": worker_tail,
    }
    if result["status"] not in VALID_OUTCOMES:
        raise RuntimeError("work_unit_status_internal_error")
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("task_payload_must_be_object")
        result = process(payload)
        print(json.dumps(result, sort_keys=True))
        # IMPORTANT: truthful repository status is data. A valid work-unit
        # result always exits 0; only protocol/infrastructure failure retries at
        # the Swarm transport layer.
        return 0
    except Exception as exc:
        print(
            json.dumps({"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"}, sort_keys=True),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
