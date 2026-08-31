#!/usr/bin/env python3
"""Compatibility entrypoint for the source-preserving estate restorer.

Source-preserving transport is now native to estate_function_restorer.
This module remains so existing operators and automation that invoke the
historical estate_function_restorer_safe.py path continue to work, but it
contains no second transport implementation and performs no monkey-patching.
"""
from __future__ import annotations

from typing import Sequence

import estate_function_restorer as core

repair_branch_name = core.repair_branch_name
checkpoint_branch = core.checkpoint_branch
prepare_branch = core.prepare_branch
push_repair_branch = core.push_repair_branch


def main(argv: Sequence[str] | None = None) -> int:
    return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
