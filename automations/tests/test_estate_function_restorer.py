from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "estate_function_restorer.py"
spec = importlib.util.spec_from_file_location("estate_function_restorer", MODULE)
restorer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = restorer
assert spec.loader is not None
spec.loader.exec_module(restorer)


class EstateFunctionRestorerTests(unittest.TestCase):
    def test_scaffold_markers_fail_visible(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "README.md").write_text(
                "## Current scaffold state\n", encoding="utf-8"
            )
            self.assertTrue(restorer.scaffold_evidence(repo))

    def test_source_hash_changes_with_real_source(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            source = repo / "src" / "engine.py"
            source.write_text("def decide(x):\n    return x + 1\n", encoding="utf-8")
            first = restorer.source_tree_sha(repo)
            source.write_text("def decide(x):\n    return x + 2\n", encoding="utf-8")
            second = restorer.source_tree_sha(repo)
            self.assertRegex(first, r"^[0-9a-f]{64}$")
            self.assertNotEqual(first, second)

    def test_behavioral_and_adversarial_tests_are_counted_separately(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "tests").mkdir()
            (repo / "tests" / "test_engine.py").write_text(
                """
def test_happy_path(): pass
def test_second_behavior(): pass
def test_third_behavior(): pass
def test_rejects_invalid_payload(): pass
""",
                encoding="utf-8",
            )
            behavioral, adversarial = restorer.test_case_counts(repo)
            self.assertEqual(behavioral, 3)
            self.assertEqual(adversarial, 1)

    def test_deployment_mode_requires_real_build_surface(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            self.assertIsNone(restorer.deployment_mode(repo))
            (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            self.assertEqual(restorer.deployment_mode(repo), "deployable-service")

    def test_priority_file_accepts_repository_objects(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "priority.json"
            path.write_text(
                json.dumps(
                    {
                        "repositories": [
                            {"repository": "GlacierEQ/openai-tool-authority-matrix"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                restorer.load_priority_names(path),
                {"openai-tool-authority-matrix"},
            )

    def test_native_target_sort_prioritizes_explicit_recruiter_target(self):
        target = restorer.RepoTarget(
            name="openai-tool-authority-matrix",
            default_branch="main",
            visibility="PUBLIC",
            description="authority runtime",
            pushed_at="",
        )
        other = restorer.RepoTarget(
            name="misc-repo",
            default_branch="main",
            visibility="PRIVATE",
            description="",
            pushed_at="",
        )
        priority = {target.name}
        self.assertLess(
            restorer.target_priority(target, priority),
            restorer.target_priority(other, priority),
        )


if __name__ == "__main__":
    unittest.main()
