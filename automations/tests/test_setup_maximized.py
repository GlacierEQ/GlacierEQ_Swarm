#!/usr/bin/env python3
"""Tests for maximized setup — drive real shipped flipper entry points.

No theater: imports real modules, runs real functions, asserts durable outputs.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

AUTO = Path(__file__).resolve().parent
STATE = Path("/Users/kcbflux/GlacierEQ_Swarm/state")
AGENTS = Path("/Users/kcbflux/AGENTS.md")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestAgentsMonolith(unittest.TestCase):
    def test_agents_progressive_and_device_pin(self):
        text = AGENTS.read_text()
        for token in ("L0", "L1", "L2", "L3", "L4", "L5", "Token Saver", "always"):
            self.assertIn(token, text)
        self.assertIn("GlacierEQ_Swarm/state", text)
        self.assertIn("Comet", text)
        self.assertIn("Opera Neon", text)
        self.assertNotIn("You are the living embodiment", text)

    def test_state_artifacts_exist(self):
        for rel in (
            "ecosystem_map.json",
            "device_stability_profile.md",
            "agents_monolith_distill.json",
        ):
            p = STATE / rel
            self.assertTrue(p.is_file(), f"missing {p}")


class TestDeviceStabilityFlipper(unittest.TestCase):
    def test_free_kb_returns_positive_int(self):
        mod = load_module("device_stability_flipper", AUTO / "device-stability-flipper.py")
        kb = mod.free_kb()
        self.assertIsInstance(kb, int)
        self.assertGreater(kb, 0)

    def test_main_writes_structured_snapshot(self):
        mod = load_module("device_stability_flipper", AUTO / "device-stability-flipper.py")
        # Run shipped main()
        mod.main()
        out = Path(mod.OUT)
        self.assertTrue(out.is_file(), f"expected snapshot at {out}")
        data = json.loads(out.read_text())
        self.assertIn("ts", data)
        self.assertIn("swap", data)
        self.assertIn("stability_profile", data)
        keep = data["stability_profile"].get("keep_always", [])
        self.assertIn("Comet", keep)
        self.assertIn("Opera Neon", keep)
        advice = " ".join(data["stability_profile"].get("advice", []))
        self.assertNotIn("quit Comet", advice.lower())
        self.assertNotIn("uninstall", advice.lower())
        # second run also succeeds and refreshes ts
        ts1 = data["ts"]
        mod.main()
        data2 = json.loads(out.read_text())
        self.assertIn("ts", data2)
        self.assertTrue(data2["ts"])  # non-empty
        # may equal if same second; still must remain valid JSON with keep pin
        self.assertIn("Comet", data2["stability_profile"]["keep_always"])


class TestFlipperEntrypoints(unittest.TestCase):
    def test_py_compile_all_automations(self):
        pys = sorted(AUTO.glob("*.py"))
        self.assertTrue(pys)
        for p in pys:
            if p.name.startswith("test_"):
                continue
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(p)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, f"py_compile failed {p}: {r.stderr}")

    def test_aeon_and_github_write_state(self):
        aeon = load_module("aeon_moc", AUTO / "aeon-moc-procode-scanner.py")
        gh = load_module("gh_eco", AUTO / "github-ecosystem-analyzer.py")
        aeon.scan()
        gh.analyze()
        self.assertTrue(Path(aeon.OUT_P).is_file())
        self.assertTrue(Path(gh.OUT_P).is_file())
        a = json.loads(Path(aeon.OUT_P).read_text())
        g = json.loads(Path(gh.OUT_P).read_text())
        self.assertIn("ts", a)
        self.assertIn("ts", g)
        self.assertTrue(g.get("ok"), g)


if __name__ == "__main__":
    unittest.main(verbosity=2)
