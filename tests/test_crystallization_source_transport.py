from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automations"))

import crystallization_repo_worker as worker
import crystallization_work_unit as work_unit


class CrystallizationSourceTransportTests(unittest.TestCase):
    def test_continuation_branch_is_stable_not_date_addressed(self):
        self.assertEqual(worker.safe_branch("GlacierEQ/alpha"), "crystallize/alpha")
        self.assertEqual(worker.safe_branch("GlacierEQ/alpha"), worker.safe_branch("GlacierEQ/alpha"))
        self.assertNotRegex(worker.safe_branch("GlacierEQ/alpha"), r"\d{8}")

    def test_checkpoint_is_bound_to_exact_prior_head(self):
        first = worker.checkpoint_branch("GlacierEQ/alpha", "a" * 40)
        second = worker.checkpoint_branch("GlacierEQ/alpha", "b" * 40)
        self.assertEqual(first, "crystallize-checkpoints/alpha/aaaaaaaaaaaa")
        self.assertNotEqual(first, second)

    def test_work_unit_delegates_checkout_to_shared_transport(self):
        expected = (Path("/tmp/example"), "crystallize/example", "CONTINUE_STABLE_CRYSTALLIZATION")
        with patch.object(worker, "ensure_checkout_with_ancestry", return_value=expected) as delegated:
            actual = work_unit.ensure_continuation_checkout(
                "GlacierEQ/example", "main", Path("/tmp/root")
            )
        self.assertEqual(actual, expected)
        delegated.assert_called_once_with("GlacierEQ/example", "main", Path("/tmp/root"))

    def test_push_uses_normal_descendant_transport_and_exact_readback(self):
        repo = Path("/tmp/repo")
        old = "a" * 40
        new = "b" * 40
        remote_values = iter([old, new])
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return {"argv": list(argv), "returncode": 0, "stdout": "", "stderr": ""}

        with (
            patch.object(worker, "_git_stdout", return_value=new),
            patch.object(worker, "_remote_head", side_effect=lambda *_args: next(remote_values)),
            patch.object(worker, "_ensure_remote_checkpoint", return_value="crystallize-checkpoints/example/aaaaaaaaaaaa"),
            patch.object(worker, "_is_ancestor", return_value=True),
            patch.object(worker, "run", side_effect=fake_run),
        ):
            pr_url = worker.maybe_push_and_pr(
                repo,
                "GlacierEQ/example",
                "crystallize/example",
                "main",
                push=True,
                open_pr=False,
                status="INCOMPLETE",
            )

        self.assertIsNone(pr_url)
        flattened = " ".join(" ".join(call) for call in calls)
        self.assertIn("git push -u origin HEAD:refs/heads/crystallize/example", flattened)
        self.assertNotIn("--force", flattened)
        receipt = worker.get_last_transport_receipt()
        self.assertEqual(receipt["state"], "PUSHED_AND_READ_BACK")
        self.assertEqual(receipt["remote_before"], old)
        self.assertEqual(receipt["remote_after"], new)
        self.assertFalse(receipt["force_push"])

    def test_divergence_is_refused_after_checkpoint_without_push(self):
        repo = Path("/tmp/repo")
        old = "a" * 40
        new = "b" * 40
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return {"argv": list(argv), "returncode": 0, "stdout": "", "stderr": ""}

        with (
            patch.object(worker, "_git_stdout", return_value=new),
            patch.object(worker, "_remote_head", return_value=old),
            patch.object(worker, "_ensure_remote_checkpoint", return_value="crystallize-checkpoints/example/aaaaaaaaaaaa"),
            patch.object(worker, "_is_ancestor", return_value=False),
            patch.object(worker, "run", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(RuntimeError, "diverged_refusing_force_push"):
                worker.maybe_push_and_pr(
                    repo,
                    "GlacierEQ/example",
                    "crystallize/example",
                    "main",
                    push=True,
                    open_pr=False,
                    status="BROKEN",
                )

        flattened = " ".join(" ".join(call) for call in calls)
        self.assertNotIn("git push -u origin", flattened)
        receipt = worker.get_last_transport_receipt()
        self.assertEqual(receipt["state"], "DIVERGENCE_REFUSED")
        self.assertFalse(receipt["force_push"])


if __name__ == "__main__":
    unittest.main()
