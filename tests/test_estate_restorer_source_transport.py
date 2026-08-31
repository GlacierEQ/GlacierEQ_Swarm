from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automations"))

import estate_function_restorer as core
import estate_function_restorer_safe as compat


class EstateRestorerSourceTransportTests(unittest.TestCase):
    def setUp(self):
        with core._TRANSPORT_LOCK:
            core._TRANSPORT_RECEIPTS.clear()

    def test_repair_branch_is_stable_not_date_reused(self):
        branch = core.repair_branch_name("example-repo")
        self.assertEqual(branch, "restore/function-example-repo")
        self.assertNotRegex(branch, r"\d{8}")

    def test_checkpoint_is_bound_to_prior_remote_head(self):
        self.assertEqual(
            core.checkpoint_branch("restore/function-example-repo", "a" * 40),
            "restore-checkpoints/example-repo/aaaaaaaaaaaa",
        )

    def test_compat_entrypoint_delegates_to_native_safe_engine(self):
        self.assertIs(compat.repair_branch_name, core.repair_branch_name)
        self.assertIs(compat.checkpoint_branch, core.checkpoint_branch)
        self.assertIs(compat.prepare_branch, core.prepare_branch)
        self.assertIs(compat.push_repair_branch, core.push_repair_branch)
        with patch.object(core, "main", return_value=7) as main:
            self.assertEqual(compat.main(["--list"]), 7)
            main.assert_called_once_with(["--list"])

    def test_descendant_push_uses_normal_push_and_readback(self):
        repo = Path("/tmp/example")
        old = "a" * 40
        new = "b" * 40
        remote_values = iter([old, new])
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return core.CommandResult(list(argv), 0, "", "")

        with (
            patch.object(core, "run", side_effect=fake_run),
            patch.object(core, "_head", return_value=new),
            patch.object(core, "_remote_head", side_effect=lambda *_args: next(remote_values)),
            patch.object(core, "_checkpoint", return_value="restore-checkpoints/example/aaaaaaaaaaaa"),
            patch.object(core, "_is_ancestor", return_value=True),
        ):
            receipt = core.push_repair_branch(repo, "restore/function-example")

        flattened = " ".join(" ".join(call) for call in calls)
        self.assertIn(
            "git push -u origin HEAD:refs/heads/restore/function-example",
            flattened,
        )
        self.assertNotIn("--force-with-lease", flattened)
        self.assertEqual(receipt["state"], "PUSHED_AND_READ_BACK")
        self.assertFalse(receipt["force_push"])

    def test_divergence_is_refused_after_checkpoint(self):
        repo = Path("/tmp/example")
        with (
            patch.object(core, "_head", return_value="b" * 40),
            patch.object(core, "_remote_head", return_value="a" * 40),
            patch.object(core, "_checkpoint", return_value="restore-checkpoints/example/aaaaaaaaaaaa"),
            patch.object(core, "_is_ancestor", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "diverged_refusing_force_push"):
                core.push_repair_branch(repo, "restore/function-example")
        with core._TRANSPORT_LOCK:
            receipt = core._TRANSPORT_RECEIPTS[-1]
        self.assertEqual(receipt["state"], "DIVERGENCE_REFUSED")
        self.assertFalse(receipt["force_push"])

    def test_raw_source_contains_no_force_push_transport(self):
        source = (ROOT / "automations" / "estate_function_restorer.py").read_text(encoding="utf-8")
        self.assertNotIn("--force-with-lease", source)


if __name__ == "__main__":
    unittest.main()
