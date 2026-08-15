#!/usr/bin/env python3
"""Fail-closed truth gate for GlacierEQ Swarm public and machine surfaces."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"PUBLIC_TRUTH_FAIL: {message}")


def main() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    caps = json.loads((ROOT / "machine/capabilities.json").read_text(encoding="utf-8"))
    state = json.loads((ROOT / "machine/excellence-state.json").read_text(encoding="utf-8"))

    require("src/mechanism.py" in readme, "orchestration mechanism missing from public surface")
    require("automations/crystallization_executor.py" in readme, "crystallization executor missing from public surface")
    require("A green build is not completion" in readme, "anti-false-completion boundary missing")

    allowed = {
        "deterministic-capability-aware-task-orchestration",
        "bounded-retry-and-worker-failure-recovery",
        "integrity-chained-orchestration-telemetry",
        "content-addressed-snapshot-and-recovery",
        "purpose-first-repository-crystallization-execution",
        "crystallization-terminal-status-enforcement",
    }
    require(set(caps.get("capabilities", [])) == allowed, "machine capability allowlist drift")
    require("hyper-scaling" not in caps.get("capabilities", []), "hyper-scaling theater restored")
    require(caps.get("production_operational_authority") is False, "production authority must remain false")
    require(caps.get("automatic_estate_completion_authority") is False, "automatic completion authority must remain false")
    require(caps.get("completion_requires_explicit_evidence") is True, "completion evidence requirement missing")

    require(state.get("principal_state") == "FUNCTIONAL_CANDIDATE", "stale discovered/promoted state restored")
    require(state.get("production_operational_authority") is False, "state grants production authority")
    require(
        state.get("gates", {}).get("DETERMINISTIC_PROOF_GREEN", {}).get("status") == "PENDING_CANONICAL_CI",
        "fresh exact-head proof requirement missing",
    )
    prior = state.get("gates", {}).get("PRIOR_EXACT_HEAD_PROOF", {}).get("evidence", {})
    require(prior.get("head_sha") == "21c1fc7009c7f7c618396ab8c333990bd0ffc5db", "prior proof head drift")
    require(prior.get("workflow_run") == 31641972928, "prior proof receipt drift")

    print("PUBLIC_TRUTH_PASS")


if __name__ == "__main__":
    main()
