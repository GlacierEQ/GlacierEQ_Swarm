from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

AUTOMATIONS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTOMATIONS))
MODULE = AUTOMATIONS / "crystallization_executor.py"
spec = importlib.util.spec_from_file_location("crystallization_executor", MODULE)
crystal = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crystal
assert spec.loader is not None
spec.loader.exec_module(crystal)


def working_cap(cap_id: str = "core") -> dict:
    return {
        "id": cap_id,
        "class": "core_domain_logic",
        "description": "real material behavior",
        "material": True,
        "state": "WORKING",
        "implementation_paths": ["src/core.py"],
        "verification": ["tests/test_core.py::test_behavior"],
        "consumer": "runtime",
    }


class CrystallizationExecutorTests(unittest.TestCase):
    def test_crystallized_requires_zero_gaps_and_all_proof(self):
        status = crystal.classify(
            purpose={"purpose": "solve the thing"},
            capabilities=[working_cap()],
            gaps=[],
            blockers=[],
            test_ok=True,
            build_ok=True,
            runtime_ok=True,
            deploy_ok=True,
        )
        self.assertEqual(status, crystal.RepoStatus.CRYSTALLIZED)

    def test_green_ci_cannot_hide_missing_material_capability(self):
        cap = working_cap("missing-integration")
        cap["state"] = "MISSING"
        status = crystal.classify(
            purpose={"purpose": "solve the thing"},
            capabilities=[cap],
            gaps=[{"capability_id": "missing-integration", "required_work": "build it"}],
            blockers=[],
            test_ok=True,
            build_ok=True,
            runtime_ok=True,
            deploy_ok=True,
        )
        self.assertEqual(status, crystal.RepoStatus.INCOMPLETE)

    def test_failed_behavior_is_broken_even_if_build_is_green(self):
        status = crystal.classify(
            purpose={"purpose": "solve the thing"},
            capabilities=[working_cap()],
            gaps=[],
            blockers=[],
            test_ok=False,
            build_ok=True,
            runtime_ok=True,
            deploy_ok=True,
        )
        self.assertEqual(status, crystal.RepoStatus.BROKEN)

    def test_naturally_deployable_system_without_deployment_stops_at_complete(self):
        status = crystal.classify(
            purpose={"purpose": "serve users"},
            capabilities=[working_cap()],
            gaps=[],
            blockers=[],
            test_ok=True,
            build_ok=True,
            runtime_ok=True,
            deploy_ok=False,
        )
        self.assertEqual(status, crystal.RepoStatus.COMPLETE)

    def test_manifest_validator_requires_gap_for_every_open_material_capability(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            machine = repo / crystal.MACHINE
            machine.mkdir(parents=True)
            (machine / "purpose-manifest.json").write_text(
                json.dumps({
                    "canonical_identity": "demo",
                    "purpose": "demonstrate real behavior",
                    "problem": "a real problem",
                    "intended_outcome": "an executable outcome",
                    "consumers": ["human"],
                    "system_kind": "cli",
                    "naturally_deployable": False,
                    "lineage": {"predecessors": [], "successors": [], "duplicates": [], "canonical_successor": None},
                    "evidence": ["src/core.py"],
                }),
                encoding="utf-8",
            )
            missing = working_cap("missing")
            missing["state"] = "MISSING"
            (machine / "capability-manifest.json").write_text(
                json.dumps({"capabilities": [missing]}),
                encoding="utf-8",
            )
            (machine / "gap-matrix.json").write_text(json.dumps({"gaps": []}), encoding="utf-8")
            _, _, _, blockers = crystal.validate_manifests(repo)
            self.assertIn("gap_matrix_omits:missing", blockers)

    def test_non_deployable_library_does_not_need_fake_deployment_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            ok, result, records, blockers = crystal.deployment_proof(
                Path(td),
                {"naturally_deployable": False, "system_kind": "library"},
            )
            self.assertTrue(ok)
            self.assertEqual(result, "NOT_APPLICABLE")
            self.assertFalse(blockers)
            self.assertTrue(records[0]["not_applicable"])

    def test_deployable_service_requires_real_smoke_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            ok, result, _, blockers = crystal.deployment_proof(
                repo,
                {"naturally_deployable": True, "system_kind": "api_service"},
            )
            self.assertFalse(ok)
            self.assertEqual(result, "FAIL")
            self.assertIn("deployment_receipt_missing", blockers)

    def test_fake_scaffold_marker_blocks_terminal_truth(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "README.md").write_text("## Current scaffold state\n", encoding="utf-8")
            hits = crystal.scan_fake_completion(repo)
            self.assertTrue(any("Current scaffold state" in hit for hit in hits))


if __name__ == "__main__":
    unittest.main()
