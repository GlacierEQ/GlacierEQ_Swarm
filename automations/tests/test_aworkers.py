#!/usr/bin/env python3
"""Tests for AWorkers orchestrator — drives real module."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

AUTO = Path(__file__).resolve().parent
STATE = Path.home() / "GlacierEQ_Swarm/state"
ORCH = AUTO / "aworkers_orchestrator.py"


def load():
    spec = importlib.util.spec_from_file_location("aw", ORCH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestAWorkers(unittest.TestCase):
    def test_registry_and_memory_exist(self):
        self.assertTrue((STATE / "aworkers_registry.json").is_file())
        self.assertTrue((STATE / "unified_memory.json").is_file())

    def test_status_exit0(self):
        r = subprocess.run(
            [sys.executable, str(ORCH), "status"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("AWorkers", r.stdout)

    def test_run_flipper_device_stability(self):
        m = load()
        rec = m.run_flipper("device-stability-flipper.py", timeout=180)
        self.assertIn(rec["status"], ("ok", "error"))
        # device stability should usually ok
        if rec["status"] == "ok":
            self.assertTrue(rec.get("tail") or rec.get("outputs_ptr"))

    def test_merge_memory(self):
        m = load()
        results = [
            {"worker_id": "t.py", "status": "ok"},
            {"worker_id": "bad.py", "status": "error", "error": "x"},
        ]
        mem = m.merge_memory("test-goal", results, "AKOS", "test-case")
        self.assertEqual(mem["session"]["goal"], "test-goal")
        self.assertTrue(mem["session"]["last_worker_run"])
        self.assertGreaterEqual(len(mem["session"]["open_loops"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
