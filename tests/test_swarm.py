"""Test suite for GlacierEQ Swarm infrastructure & toolbelt Doctor."""
import unittest
from pathlib import Path

class TestGlacierEQSwarm(unittest.TestCase):

    def test_toolbelt_and_state_exist(self):
        root = Path(__file__).parent.parent
        toolbelt_path = root / "toolbelt"
        state_path = root / "state"
        
        self.assertTrue(toolbelt_path.exists())
        self.assertTrue(state_path.exists())

if __name__ == "__main__":
    unittest.main()
