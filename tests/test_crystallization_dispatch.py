from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "automations"))

from crystallization_ledger import CrystallizationLedger
import crystallization_swarm_dispatch as dispatch
import crystallization_work_unit as work_unit


class FakeStdin:
    def __init__(self, value: str) -> None:
        self._value = value

    def read(self, *args, **kwargs):
        return self._value


class CrystallizationDispatchTests(unittest.TestCase):
    def inventory(self):
        return [
            {
                "full_name": "GlacierEQ/active",
                "default_branch": "main",
                "private": False,
                "visibility": "public",
                "archived": False,
                "disabled": False,
                "fork": False,
                "description": "active",
                "language": "Python",
            },
            {
                "full_name": "GlacierEQ/archived",
                "default_branch": "main",
                "private": False,
                "visibility": "public",
                "archived": True,
                "disabled": False,
                "fork": False,
                "description": "old",
                "language": "Python",
            },
            {
                "full_name": "GlacierEQ/forked",
                "default_branch": "main",
                "private": False,
                "visibility": "public",
                "archived": False,
                "disabled": False,
                "fork": True,
                "fork_parent": "upstream/project",
                "description": "fork",
                "language": "Python",
            },
        ]

    def test_ledger_never_hides_unresolved_archives_or_forks(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = CrystallizationLedger(Path(td) / "ledger.sqlite3")
            ledger.record_inventory(self.inventory())
            current = ledger.current_ledger()
            self.assertEqual(current["total_repositories"], 3)
            self.assertEqual(current["terminal_repositories"], 0)
            self.assertEqual(current["unresolved_repositories"], 3)
            self.assertFalse(current["estate_complete"])
            by_repo = {row["repository"]: row for row in current["repositories"]}
            self.assertEqual(by_repo["GlacierEQ/archived"]["resolution_reason"], "ARCHIVE_RESOLUTION_REQUIRED")
            self.assertEqual(by_repo["GlacierEQ/forked"]["resolution_reason"], "FORK_LINEAGE_RESOLUTION_REQUIRED")

    def test_terminal_result_advances_only_that_repository(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = CrystallizationLedger(Path(td) / "ledger.sqlite3")
            ledger.record_inventory(self.inventory())
            ledger.record_work_unit(
                "crystallize::active::g0001",
                "GlacierEQ/active",
                1,
                {
                    "repository": "GlacierEQ/active",
                    "status": "CRYSTALLIZED",
                    "remaining_gap_count": 0,
                },
            )
            current = ledger.current_ledger()
            self.assertEqual(current["terminal_repositories"], 1)
            self.assertEqual(current["unresolved_repositories"], 2)
            self.assertFalse(current["estate_complete"])

    def test_incomplete_result_remains_unresolved_and_next_generation_advances(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = CrystallizationLedger(Path(td) / "ledger.sqlite3")
            ledger.record_inventory(self.inventory())
            ledger.record_work_unit(
                "crystallize::active::g0001",
                "GlacierEQ/active",
                1,
                {
                    "repository": "GlacierEQ/active",
                    "status": "INCOMPLETE",
                    "remaining_gap_count": 4,
                },
            )
            self.assertEqual(ledger.next_generation("GlacierEQ/active"), 2)
            current = ledger.current_ledger()
            self.assertEqual(current["status_counts"]["INCOMPLETE"], 1)
            self.assertFalse(current["estate_complete"])

    def test_ledger_refuses_conflicting_result_under_same_task_identity(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = CrystallizationLedger(Path(td) / "ledger.sqlite3")
            ledger.record_inventory(self.inventory())
            first = {"repository": "GlacierEQ/active", "status": "INCOMPLETE"}
            ledger.record_work_unit("crystallize::active::g0001", "GlacierEQ/active", 1, first)
            with self.assertRaisesRegex(ValueError, "ledger_task_result_conflict"):
                ledger.record_work_unit(
                    "crystallize::active::g0001",
                    "GlacierEQ/active",
                    1,
                    {"repository": "GlacierEQ/active", "status": "CRYSTALLIZED"},
                )

    def test_should_submit_never_treats_archive_or_fork_as_resolved(self):
        active, archived, forked = self.inventory()
        self.assertEqual(dispatch.should_submit(active, None), (True, "EXECUTE"))
        self.assertEqual(dispatch.should_submit(archived, None), (False, "ARCHIVE_RESOLUTION_REQUIRED"))
        self.assertEqual(dispatch.should_submit(forked, None), (False, "FORK_LINEAGE_RESOLUTION_REQUIRED"))
        self.assertEqual(
            dispatch.should_submit(active, {"status": "CRYSTALLIZED"}),
            (False, "ALREADY_TERMINAL"),
        )

    def test_task_id_is_generation_addressed(self):
        self.assertEqual(dispatch.task_id("GlacierEQ/a-repo", 1), "crystallize::a-repo::g0001")
        self.assertEqual(dispatch.task_id("GlacierEQ/a-repo", 12), "crystallize::a-repo::g0012")

    def test_valid_incomplete_work_unit_exits_zero_as_truth_not_transport_failure(self):
        payload = {"repository": "GlacierEQ/active"}
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
            patch.object(
                work_unit,
                "process",
                return_value={
                    "repository": "GlacierEQ/active",
                    "status": "INCOMPLETE",
                    "remaining_gap_count": 2,
                },
            ),
        ):
            code = work_unit.main()
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(stderr.getvalue(), "")

    def test_protocol_failure_exits_nonzero(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "stdin", io.StringIO("not-json")),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
        ):
            code = work_unit.main()
        self.assertEqual(code, 3)
        self.assertEqual(stdout.getvalue(), "")
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
