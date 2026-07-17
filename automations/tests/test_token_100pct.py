#!/usr/bin/env python3
"""Drive real token_saver_connector + 100pct flipper. No theater."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

CONN = Path.home() / ".grok/skills/path-of-highest-power/.hidden_pistons/token_saver_connector.py"
FLIP = Path.home() / "GlacierEQ_Swarm/automations/token-100pct-savings-flipper.py"
STATE = Path.home() / "GlacierEQ_Swarm/state"


def load_conn():
    spec = importlib.util.spec_from_file_location("tsc", CONN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestTokenSaverConnector(unittest.TestCase):
    def test_measure_savings_honest(self):
        m = load_conn()
        met = m.measure_savings("a" * 1000, "ptr")
        self.assertEqual(met["bytes_in"], 1000)
        self.assertEqual(met["bytes_out"], 3)
        self.assertEqual(met["bytes_saved"], 997)
        self.assertGreater(met["savings_pct"], 99.0)

    def test_pure_pointer_externalizes(self):
        m = load_conn()
        big = "Let me explain everything. " + ("word " * 500)
        out = m.apply_token_saver(big, mode="pure_pointer")
        self.assertIn("[ptr:", out)
        met = m.measure_savings(big, out)
        self.assertGreater(met["savings_pct"], 90.0)
        # blob exists
        self.assertTrue(any(STATE.joinpath("externalized_blobs").glob("ctx_*.txt")))

    def test_microwave_batch_parallel(self):
        m = load_conn()
        p = STATE / "ecosystem_map.json"
        self.assertTrue(p.is_file())
        recs = m.microwave_batch([p], mode="pure_pointer", max_workers=2)
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["ok"])
        self.assertGreater(recs[0]["bytes_saved"], 0)

    def test_route_compute(self):
        m = load_conn()
        self.assertIn("device-stability", m.route_compute("stability"))


class TestFlipper(unittest.TestCase):
    def test_flipper_exit0_and_ledger(self):
        r = subprocess.run([sys.executable, str(FLIP)], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("ptr:", r.stdout)
        last = STATE / "token_100pct_last.json"
        self.assertTrue(last.is_file())
        data = json.loads(last.read_text())
        self.assertGreater(data["totals"]["bytes_in"], 0)
        self.assertGreaterEqual(data["totals"]["savings_pct"], 90.0)
        self.assertTrue((STATE / "token_savings_ledger.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
