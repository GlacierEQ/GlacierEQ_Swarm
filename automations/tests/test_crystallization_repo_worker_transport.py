from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "crystallization_repo_worker_transport.py"
spec = importlib.util.spec_from_file_location("crystallization_repo_worker_transport", MODULE)
transport = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = transport
assert spec.loader is not None
spec.loader.exec_module(transport)


class Completed:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeStdin:
    def __init__(self, value: bytes) -> None:
        self.buffer = io.BytesIO(value)


class CrystallizationTransportTests(unittest.TestCase):
    def test_parser_accepts_truthful_incomplete_result(self):
        value = transport._parse_single_object(
            json.dumps(
                {
                    "repository": "GlacierEQ/example",
                    "status": "INCOMPLETE",
                    "remaining_gap_count": 3,
                }
            )
        )
        self.assertEqual(value["status"], "INCOMPLETE")

    def test_parser_accepts_broken_result_as_repository_truth(self):
        value = transport._parse_single_object(
            json.dumps({"repository": "GlacierEQ/example", "status": "BROKEN"})
        )
        self.assertEqual(value["status"], "BROKEN")

    def test_parser_rejects_worker_protocol_error_status(self):
        with self.assertRaisesRegex(ValueError, "repository_worker_status_invalid"):
            transport._parse_single_object(
                json.dumps({"repository": "GlacierEQ/example", "status": "ERROR"})
            )

    def test_nonterminal_child_exit_two_becomes_successful_transport(self):
        child = Completed(
            2,
            json.dumps(
                {
                    "repository": "GlacierEQ/example",
                    "status": "INCOMPLETE",
                    "remaining_gap_count": 2,
                }
            ).encode("utf-8"),
            b"repository remains incomplete",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "stdin", FakeStdin(b'{"repository":"GlacierEQ/example"}')),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
            patch.object(transport.subprocess, "run", return_value=child),
        ):
            code = transport.main()
        self.assertEqual(code, 0)
        value = json.loads(stdout.getvalue())
        self.assertEqual(value["status"], "INCOMPLETE")
        self.assertEqual(value["worker_transport"]["child_returncode"], 2)
        self.assertEqual(stderr.getvalue(), "")

    def test_invalid_child_stdout_remains_transport_failure(self):
        child = Completed(3, b"not-json", b"Traceback")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "stdin", FakeStdin(b'{"repository":"GlacierEQ/example"}')),
            patch.object(sys, "stdout", stdout),
            patch.object(sys, "stderr", stderr),
            patch.object(transport.subprocess, "run", return_value=child),
        ):
            code = transport.main()
        self.assertEqual(code, 4)
        self.assertEqual(stdout.getvalue(), "")
        error = json.loads(stderr.getvalue())
        self.assertEqual(error["status"], "ERROR")
        self.assertEqual(error["child_returncode"], 3)


if __name__ == "__main__":
    unittest.main()
