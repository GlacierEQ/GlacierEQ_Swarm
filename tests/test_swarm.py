"""Behavioral tests for the GlacierEQ Swarm orchestration core."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mechanism import SwarmOrchestrator, WorkerStatus, run


class TestGlacierEQSwarm(unittest.TestCase):
    def test_routes_by_capability_and_capacity(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("python-only", ["python"], max_concurrency=2)
        swarm.register_agent("deploy-worker", ["python", "deploy"], max_concurrency=1)
        swarm.submit_task("deploy", ["python", "deploy"], {"release": 1}, priority=10)
        receipt = swarm.dispatch()
        self.assertEqual(receipt["assignments"][0]["worker_id"], "deploy-worker")
        self.assertEqual(swarm.get_status()["active_assignments"], 1)

    def test_priority_dispatches_highest_value_first(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("worker", ["python"], max_concurrency=1)
        swarm.submit_task("low", ["python"], {}, priority=1)
        swarm.submit_task("high", ["python"], {}, priority=100)
        receipt = swarm.dispatch(limit=1)
        self.assertEqual(receipt["assignments"][0]["task_id"], "high")
        self.assertEqual(swarm.get_status()["task_status"]["QUEUED"], 1)

    def test_load_balances_equal_workers_deterministically(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("a", ["python"], max_concurrency=2)
        swarm.register_agent("b", ["python"], max_concurrency=2)
        swarm.submit_task("one", ["python"], {})
        swarm.submit_task("two", ["python"], {})
        receipt = swarm.dispatch()
        assignments = {
            row["task_id"]: row["worker_id"] for row in receipt["assignments"]
        }
        self.assertEqual(assignments, {"one": "a", "two": "b"})

    def test_failed_task_requeues_to_alternate_worker(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("a", ["python"])
        swarm.register_agent("b", ["python"])
        first = swarm.assign_task("task", ["python"], {"n": 1}, max_attempts=3)
        self.assertEqual(first["worker_id"], "a")
        swarm.start_task("task", "a")
        failure = swarm.fail_task("task", "a", "process crashed")
        self.assertEqual(failure["status"], "QUEUED")
        second = swarm.dispatch()
        self.assertEqual(second["assignments"][0]["worker_id"], "b")
        self.assertEqual(second["assignments"][0]["attempt"], 2)

    def test_retry_budget_eventually_dead_letters_task(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("only", ["python"])
        first = swarm.assign_task("task", ["python"], {}, max_attempts=2)
        swarm.fail_task("task", first["worker_id"], "first failure")
        second = swarm.dispatch()["assignments"][0]
        result = swarm.fail_task("task", second["worker_id"], "second failure")
        self.assertEqual(result["status"], "DEAD")
        self.assertFalse(result["retry_available"])
        self.assertEqual(swarm.get_status()["task_status"]["DEAD"], 1)

    def test_unhealthy_worker_requeues_active_work(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("a", ["python"])
        swarm.register_agent("b", ["python"])
        first = swarm.assign_task("task", ["python"], {})
        swarm.start_task("task", first["worker_id"])
        result = swarm.set_worker_status(first["worker_id"], WorkerStatus.UNHEALTHY)
        self.assertEqual(result["requeued_tasks"], ["task"])
        second = swarm.dispatch()["assignments"][0]
        self.assertNotEqual(second["worker_id"], first["worker_id"])

    def test_draining_worker_gets_no_new_work(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("a", ["python"])
        swarm.register_agent("b", ["python"])
        swarm.drain_worker("a")
        assignment = swarm.assign_task("task", ["python"], {})
        self.assertEqual(assignment["worker_id"], "b")

    def test_no_capable_worker_leaves_task_queued(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("python", ["python"])
        assignment = swarm.assign_task("gpu", ["cuda"], {})
        self.assertEqual(assignment["status"], "QUEUED")
        self.assertEqual(assignment["reason"], "no_capable_worker_available")

    def test_telemetry_chain_proves_lifecycle(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("worker", ["python"])
        assignment = swarm.assign_task("task", ["python"], {"n": 1})
        swarm.start_task("task", assignment["worker_id"])
        swarm.complete_task("task", assignment["worker_id"], {"ok": True})
        verification = swarm.verify_telemetry()
        self.assertEqual(verification["status"], "PASS")
        self.assertGreaterEqual(verification["event_count"], 4)
        self.assertEqual(len(verification["telemetry_head"]), 64)

    def test_snapshot_round_trip_preserves_assignments_and_integrity(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("worker", ["python", "tests"], max_concurrency=2)
        assignment = swarm.assign_task("task", ["python"], {"n": 1})
        swarm.start_task("task", assignment["worker_id"])
        snapshot = swarm.snapshot()
        restored = SwarmOrchestrator.from_snapshot(snapshot)
        self.assertEqual(restored.snapshot(), snapshot)
        self.assertEqual(restored.get_status()["active_assignments"], 1)
        self.assertEqual(restored.verify_telemetry()["status"], "PASS")

    def test_snapshot_tamper_is_refused(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("worker", ["python"])
        snapshot = swarm.snapshot()
        tampered = copy.deepcopy(snapshot)
        tampered["workers"][0]["max_concurrency"] = 999
        with self.assertRaisesRegex(ValueError, "snapshot_digest_invalid"):
            SwarmOrchestrator.from_snapshot(tampered)

    def test_duplicate_worker_and_task_ids_are_refused(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("worker", ["python"])
        with self.assertRaisesRegex(ValueError, "worker_already_registered"):
            swarm.register_agent("worker", ["python"])
        swarm.submit_task("task", ["python"], {})
        with self.assertRaisesRegex(ValueError, "task_already_exists"):
            swarm.submit_task("task", ["python"], {})

    def test_heartbeat_must_be_monotonic_and_can_recover_worker_health(self):
        swarm = SwarmOrchestrator()
        swarm.register_agent("worker", ["python"])
        swarm.set_worker_status("worker", WorkerStatus.UNHEALTHY)
        result = swarm.heartbeat("worker", 1)
        self.assertEqual(result["status"], "ACTIVE")
        with self.assertRaisesRegex(ValueError, "heartbeat_sequence_not_monotonic"):
            swarm.heartbeat("worker", 1)

    def test_cold_start_demo_executes_real_swarm_lifecycle(self):
        receipt = run()
        self.assertEqual(receipt["status"], "HEALTHY")
        self.assertEqual(receipt["result"], "swarm_demo_complete")
        self.assertEqual(receipt["jobs"], 1)
        self.assertEqual(receipt["telemetry"]["status"], "PASS")
        self.assertEqual(len(receipt["snapshot_digest"]), 64)


if __name__ == "__main__":
    unittest.main()
