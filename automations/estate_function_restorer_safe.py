#!/usr/bin/env python3
"""Source-preserving steering shell for ``estate_function_restorer``.

The legacy restorer has strong purpose recovery, behavioral/adversarial proof,
build verification, and source-bound repair receipts. This shell keeps those
mechanisms intact while replacing the two dangerous transport primitives:

1. date-reused repair branches that can be locally deleted/restarted; and
2. remote ``--force-with-lease`` updates.

Safe transport uses one stable continuation branch per repository, migrates the
newest legacy dated branch forward once, preserves local-ahead clean work,
checkpoints every previous remote head, allows only descendant normal pushes,
and verifies exact remote readback. Divergence is a hard stop, not a rewrite.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import estate_function_restorer as legacy

_ORIGINAL_RUN = legacy.run
_ORIGINAL_SAVE_RUN = legacy.save_run
_RECEIPTS: list[dict] = []
_RECEIPTS_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:64]


def repair_branch_name(repo_name: str) -> str:
    return f"restore/function-{_slug(repo_name)}"


def checkpoint_branch(branch: str, source_sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("checkpoint_source_sha_invalid")
    name = branch.removeprefix("restore/function-")
    return f"restore-checkpoints/{_slug(name)}/{source_sha[:12]}"


def _remote_head(repo: Path, branch: str) -> str | None:
    result = _ORIGINAL_RUN(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=repo,
        timeout=120,
        check=True,
    )
    line = result.stdout.strip()
    if not line:
        return None
    sha = line.split()[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("remote_head_invalid")
    return sha


def _local_head(repo: Path, branch: str) -> str | None:
    exists = _ORIGINAL_RUN(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        timeout=30,
        check=False,
    )
    if exists.returncode != 0:
        return None
    result = _ORIGINAL_RUN(["git", "rev-parse", branch], cwd=repo, timeout=30, check=True)
    return result.stdout.strip()


def _head(repo: Path) -> str:
    result = _ORIGINAL_RUN(["git", "rev-parse", "HEAD"], cwd=repo, timeout=30, check=True)
    sha = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("local_head_invalid")
    return sha


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = _ORIGINAL_RUN(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        timeout=60,
        check=False,
    )
    return result.returncode == 0


def _latest_legacy_remote(repo: Path, repo_name: str) -> tuple[str, str] | None:
    slug = _slug(repo_name)
    result = _ORIGINAL_RUN(
        [
            "git", "for-each-ref",
            "--format=%(refname:short) %(objectname)",
            f"refs/remotes/origin/restore/function-*-{slug}",
        ],
        cwd=repo,
        timeout=60,
        check=True,
    )
    pattern = re.compile(
        rf"^origin/(restore/function-(\d{{8}})-{re.escape(slug)}) ([0-9a-f]{{40}})$"
    )
    found: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        match = pattern.match(line.strip())
        if match:
            found.append((match.group(1), match.group(3)))
    if not found:
        return None
    found.sort(key=lambda row: row[0])
    return found[-1]


def prepare_branch(repo: Path, target: legacy.RepoTarget) -> str:
    """Continue verified repair history without deleting local branch gain."""
    branch = repair_branch_name(target.name)
    remote = _remote_head(repo, branch)
    legacy_remote = None if remote else _latest_legacy_remote(repo, target.name)
    start = remote or (legacy_remote[1] if legacy_remote else None)
    if start is None:
        start = _remote_head(repo, target.default_branch)
    if start is None:
        raise RuntimeError("repair_default_branch_missing")

    local = _local_head(repo, branch)
    if local is None:
        _ORIGINAL_RUN(
            ["git", "checkout", "-b", branch, start],
            cwd=repo,
            timeout=60,
            check=True,
        )
        return branch

    if local == start:
        _ORIGINAL_RUN(["git", "checkout", branch], cwd=repo, timeout=60, check=True)
        return branch
    if _is_ancestor(repo, start, local):
        # Clean local branch contains unpublished work. Keep it.
        _ORIGINAL_RUN(["git", "checkout", branch], cwd=repo, timeout=60, check=True)
        return branch
    if _is_ancestor(repo, local, start):
        _ORIGINAL_RUN(["git", "checkout", branch], cwd=repo, timeout=60, check=True)
        _ORIGINAL_RUN(["git", "merge", "--ff-only", start], cwd=repo, timeout=60, check=True)
        return branch
    raise RuntimeError("repair_local_remote_diverged_refusing_reset")


def _record_transport(receipt: dict) -> None:
    with _RECEIPTS_LOCK:
        _RECEIPTS.append(receipt)


def _checkpoint(repo: Path, branch: str, remote_sha: str) -> str:
    checkpoint = checkpoint_branch(branch, remote_sha)
    existing = _remote_head(repo, checkpoint)
    if existing:
        if existing != remote_sha:
            raise RuntimeError("repair_checkpoint_collision")
        return checkpoint
    _ORIGINAL_RUN(
        ["git", "push", "origin", f"{remote_sha}:refs/heads/{checkpoint}"],
        cwd=repo,
        timeout=600,
        check=True,
    )
    if _remote_head(repo, checkpoint) != remote_sha:
        raise RuntimeError("repair_checkpoint_readback_mismatch")
    return checkpoint


def safe_run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    check: bool = False,
):
    """Intercept only legacy repair force-pushes; delegate all other commands."""
    args = list(argv)
    is_legacy_force = (
        cwd is not None
        and len(args) >= 6
        and args[:4] == ["git", "push", "-u", "origin"]
        and "--force-with-lease" in args
    )
    if not is_legacy_force:
        return _ORIGINAL_RUN(args, cwd=cwd, timeout=timeout, check=check)

    branch = next(
        (
            value for value in args[4:]
            if not value.startswith("-") and value != "origin"
        ),
        None,
    )
    if not branch:
        raise RuntimeError("repair_force_push_branch_unresolved")

    local_sha = _head(cwd)
    remote_before = _remote_head(cwd, branch)
    checkpoint = None
    if remote_before:
        checkpoint = _checkpoint(cwd, branch, remote_before)
        if not _is_ancestor(cwd, remote_before, local_sha):
            receipt = {
                "schema": "glaciereq.estate-function-repair-transport.v2",
                "state": "DIVERGENCE_REFUSED",
                "branch": branch,
                "remote_before": remote_before,
                "local_head": local_sha,
                "checkpoint_branch": checkpoint,
                "force_push": False,
                "observed_at": _now(),
            }
            _record_transport(receipt)
            raise RuntimeError("repair_remote_diverged_refusing_force_push")

    result = _ORIGINAL_RUN(
        ["git", "push", "-u", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=cwd,
        timeout=timeout,
        check=check,
    )
    if result.returncode != 0:
        return result
    remote_after = _remote_head(cwd, branch)
    if remote_after != local_sha:
        raise RuntimeError("repair_remote_readback_mismatch")
    _record_transport(
        {
            "schema": "glaciereq.estate-function-repair-transport.v2",
            "state": "PUSHED_AND_READ_BACK",
            "branch": branch,
            "remote_before": remote_before,
            "remote_after": remote_after,
            "local_head": local_sha,
            "checkpoint_branch": checkpoint,
            "force_push": False,
            "push_mode": "NORMAL_DESCENDANT_ONLY",
            "observed_at": _now(),
        }
    )
    return result


def save_run(results: list[legacy.RepairResult]) -> Path:
    path = _ORIGINAL_SAVE_RUN(results)
    legacy.STATE_ROOT.mkdir(parents=True, exist_ok=True)
    with _RECEIPTS_LOCK:
        receipts = list(_RECEIPTS)
    payload = {
        "schema": "glaciereq.estate-function-repair-transport-run.v2",
        "generated_at": _now(),
        "receipt_count": len(receipts),
        "force_push_count": 0,
        "receipts": receipts,
    }
    transport = legacy.STATE_ROOT / "transport-latest.json"
    transport.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def install_source_preserving_steering() -> None:
    legacy.repair_branch_name = repair_branch_name
    legacy.prepare_branch = prepare_branch
    legacy.run = safe_run
    legacy.save_run = save_run


def main(argv: Sequence[str] | None = None) -> int:
    install_source_preserving_steering()
    return legacy.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
