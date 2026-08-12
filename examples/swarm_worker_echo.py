#!/usr/bin/env python3
"""Minimal fixed-command worker used to prove the Swarm execution contract."""
from __future__ import annotations

import hashlib
import json
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        result = {
            "status": "OK",
            "worker": "echo",
            "payload": payload,
            "payload_sha256": hashlib.sha256(body).hexdigest(),
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": f"{type(exc).__name__}:{exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
