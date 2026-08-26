from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automations"))

import estate_function_restorer as legacy
import estate_function_restorer_safe as safe


class EstateRestorerSourceTransportTests(unittest.TestCase):
    def setUp(self):
        with safe._RECEIPTS_LOCK:
            safe._RECEIPTS.clear()

    def test_repair_branch_is_stable_not_date_reused(self):
        branch = safe.repair_branch_name("example-repo")
        self.assertEqual(branch, "restore/function-example-repo")
        self.assertNotRegex(branch, r"\d{8}")

    def test_checkpoint_is_bound_to_prior_remote_head(self):
        self.assertEqual(
            safe.checkpoint_branch("restore/function-example-repo", "a" * 40),
            "restore-checkpoints/example-repo/aaaaaaaaaaaa",
        )

    def test_install_replaces_only_transport_steering(self):
        safe.install_source_preserving_steering()
        self.assertIs(legacy.repair_branch_name, safe.repair_branch_name)
        self.assertIs(legacy.prepare_branch, safe.prepare_branch)
        self.assertIs(legacy.run, safe.safe_run)
        self.assertIs(legacy.save_run, safe.save_run)

    def test_force_with_lease_request_becomes_normal_descendant_push(self):
        repo = Path("/tmp/example")
        old = "a" * 40
        new = "b" * 40
        remote_values = iter([old, new])
        calls: list[list[str]] = []

        def original_run(argv, **kwargs):
            calls.append(list(argv))
            return legacy.CommandResult(list(argv), 0, "", "")

        with (
            patch.object(safe, "_ORIGINAL_RUN", side_effect=original_run),
            patch.object(safe, "_head", return_value=new),
            patch.object(safe, "_remote_head", side_effect=lambda *_args: next(remote_values)),
            patch.object(safe, "_checkpoint", return_value="restore-checkpoints/example/aaaaaaaaaaaa"),
            patch.object(safe, "_is_ancestor", return_value=True),
        ):
            result = safe.safe_run(
                ["git", "push", "-u", "origin", "restore/function-example", "--force-with-lease"],
                cwd=repo,
                timeout=600,
                check=True,
            )

        self.assertEqual(result.returncode, 0)
        flattened = " ".join(" ".join(call) for call in calls)
        self.assertIn(
            "git push -u origin HEAD:refs/heads/restore/function-example",
            flattened,
        )
        self.assertNotIn("--force-with-lease", flattened)
        with safe._RECEIPTS_LOCK:
            receipt = safe._RECEIPTS[-1]
        self.assertEqual(receipt["state"], "PUSHED_AND_READ_BACK")
        self.assertFalse(receipt["force_push"])

    def test_divergence_is_refused_after_checkpoint(self):
        repo = Path("/tmp/example")
        with (
            patch.object(safe, "_head", return_value="b" * 40),
            patch.object(safe, "_remote_head", return_value="a" * 40),
            patch.object(safe, "_checkpoint", return_value="restore-checkpoints/example/aaaaaaaaaaaa"),
            patch.object(safe, "_is_ancestor", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "diverged_refusing_force_push"):
                safe.safe_run(
                    ["git", "push", "-u", "origin", "restore/function-example", "--force-with-lease"],
                    cwd=repo,
                    timeout=600,
                    check=True,
                )
        with safe._RECEIPTS_LOCK:
            receipt = safe._RECEIPTS[-1]
        self.assertEqual(receipt["state"], "DIVERGENCE_REFUSED")
        self.assertFalse(receipt["force_push"])


if __name__ == "__main__":
    unittest.main()
