from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


AUTOMATIONS = Path(__file__).resolve().parents[1] / "automations"
sys.path.insert(0, str(AUTOMATIONS))
SCRIPT = AUTOMATIONS / "crystallization_lift_executor.py"
SPEC = importlib.util.spec_from_file_location("crystallization_lift_executor", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_select_lift_repositories_uses_code_lift_lanes_and_ranked_order():
    digest = {
        "schema": "glaciereq.crystallization-uplift-digest.v1",
        "queue": [
            {"repository": "GlacierEQ/a", "lane": "LIFT_IMPLEMENTATION_GAPS"},
            {"repository": "GlacierEQ/b", "lane": "VERIFY_ARCHIVE_OR_SUCCESSOR"},
            {"repository": "GlacierEQ/c", "lane": "VERIFY_RUNTIME_AND_LIFT"},
        ],
    }

    assert MODULE.select_lift_repositories(digest) == ["GlacierEQ/a", "GlacierEQ/c"]
    assert MODULE.select_lift_repositories(digest, max_repositories=1) == ["GlacierEQ/a"]


def test_guarded_run_removes_force_push_semantics(monkeypatch):
    observed = []

    def fake_run(argv, **kwargs):
        observed.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(MODULE, "_original_run", fake_run)
    MODULE.guarded_run(
        ["git", "push", "-u", "origin", "branch", "--force-with-lease"],
        cwd=Path("."),
        check=True,
    )

    assert observed == [["git", "push", "-u", "origin", "branch"]]


def test_guarded_run_rejects_forced_branch_deletion(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "_original_run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    try:
        MODULE.guarded_run(["git", "branch", "-D", "crystallize/old"])
    except RuntimeError as exc:
        assert "forced local branch deletion" in str(exc)
    else:
        raise AssertionError("forced branch deletion should be rejected")


def test_source_bound_branch_never_deletes_existing_branch(monkeypatch):
    observed = []

    def fake_run(argv, **kwargs):
        observed.append(list(argv))
        if argv[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="0123456789abcdef\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(MODULE, "_original_run", fake_run)
    meta = SimpleNamespace(default_branch="main", name="sample-repo")
    branch = MODULE.source_bound_branch(Path("."), meta)

    assert branch.startswith("crystallize/lift-")
    assert branch.endswith("-sample-repo-0123456789")
    assert not any(command[:3] == ["git", "branch", "-D"] for command in observed)
