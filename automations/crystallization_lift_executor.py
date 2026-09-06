#!/usr/bin/env python3
"""Safer, uplift-directed transmission for the CRYSTALLIZATION-MANDATE executor.

This preserves the legacy executor's purpose reconstruction, implementation,
test/build/runtime/deployment verification, and PR machinery while replacing
two steering behaviors:
1. reuse/overwrite of same-day crystallization branches;
2. unranked estate actuation disconnected from crawl-derived uplift signals.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import crystallization_executor as engine

DEFAULT_CODE_LIFT_LANES = {
    "LIFT_IMPLEMENTATION_GAPS",
    "VERIFY_RUNTIME_AND_LIFT",
}

_original_run = engine.run


def guarded_run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    check: bool = False,
):
    """Preserve ordinary commands but remove branch-history overwrite semantics."""
    command = list(argv)
    if len(command) >= 2 and command[0] == "git" and command[1] == "push":
        command = [
            arg for arg in command
            if arg not in {"--force", "--force-with-lease", "-f"}
        ]
    if (
        len(command) >= 3
        and command[0] == "git"
        and command[1] == "branch"
        and command[2] == "-D"
    ):
        raise RuntimeError(
            "forced local branch deletion is disabled by crystallization lift transmission"
        )
    return _original_run(command, cwd=cwd, timeout=timeout, check=check)


def source_bound_branch(repo: Path, meta: engine.RepoMeta) -> str:
    """Create a unique branch bound to the exact observed default-branch head."""
    source = _original_run(
        ["git", "rev-parse", f"origin/{meta.default_branch}"],
        cwd=repo,
        timeout=30,
        check=True,
    ).stdout.strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", meta.name)[:40]
    branch = f"crystallize/lift-{stamp}-{slug}-{source[:10]}"
    _original_run(
        ["git", "checkout", "-b", branch, f"origin/{meta.default_branch}"],
        cwd=repo,
        timeout=60,
        check=True,
    )
    return branch


def select_lift_repositories(
    digest: dict[str, Any],
    *,
    lanes: set[str] | None = None,
    max_repositories: int = 0,
) -> list[str]:
    if digest.get("schema") != "glaciereq.crystallization-uplift-digest.v1":
        raise ValueError("unsupported uplift digest schema")
    queue = digest.get("queue")
    if not isinstance(queue, list):
        raise ValueError("uplift digest queue must be a list")
    allowed = DEFAULT_CODE_LIFT_LANES if lanes is None else lanes
    selected: list[str] = []
    seen: set[str] = set()
    for item in queue:
        if not isinstance(item, dict) or item.get("lane") not in allowed:
            continue
        repository = item.get("repository")
        if (
            not isinstance(repository, str)
            or "/" not in repository
            or repository in seen
        ):
            continue
        seen.add(repository)
        selected.append(repository)
        if max_repositories > 0 and len(selected) >= max_repositories:
            break
    return selected


def parse_args(argv: Sequence[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--uplift-digest", type=Path)
    parser.add_argument("--max-lift", type=int, default=0)
    parser.add_argument("--include-lane", action="append", default=[])
    parser.add_argument("--help", action="store_true")
    args, remaining = parser.parse_known_args(argv)
    return args, remaining


def main(argv: Sequence[str] | None = None) -> int:
    args, remaining = parse_args(argv)
    if args.help:
        print(
            "crystallization_lift_executor.py [--uplift-digest PATH] "
            "[--max-lift N] [--include-lane LANE] [legacy executor args...]"
        )
        return 0
    if args.max_lift < 0:
        print("--max-lift must be non-negative", file=sys.stderr)
        return 2

    engine.run = guarded_run
    engine.prepare_branch = source_bound_branch

    if args.uplift_digest:
        try:
            payload = json.loads(args.uplift_digest.read_text(encoding="utf-8"))
            lanes = set(args.include_lane) if args.include_lane else None
            selected = select_lift_repositories(
                payload,
                lanes=lanes,
                max_repositories=args.max_lift,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"uplift digest rejected: {exc}", file=sys.stderr)
            return 2
        if not selected:
            print("no code-lift repositories selected by uplift digest")
            return 0
        for repository in selected:
            remaining.extend(["--repo", repository])

    return engine.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
