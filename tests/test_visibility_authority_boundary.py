from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "automations" / "repo-public-promotion-flipper.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "repo_visibility_flipper", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_template_is_compatibility_not_authority() -> None:
    module = load_module()
    bridge = module.AKOS_MD
    assert "Project direction: **OPERATOR**" in bridge
    assert "compatibility != control" in bridge
    assert "persistence != authority" in bridge
    assert "Canonical architecture" not in bridge
    assert "do not fork truth" not in bridge


def test_source_does_not_require_akos_for_visibility_legitimacy() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert "homepage_akos" not in text
    assert "pro_akos_pack" not in text
    assert "AKOS.md (or will write)" not in text
    assert "Canonical architecture" not in text
    assert "LEGAL_ABSOLUTE_PRIVATE" not in text
    assert "machine_visibility_authority" in text
    assert "readiness_is_visibility_authorization" in text


def test_force_never_overrides_sensitive_or_secret_safety(monkeypatch) -> None:
    module = load_module()

    monkeypatch.setattr(
        module,
        "evaluate_repo",
        lambda name: {
            "name": name,
            "observed": True,
            "readiness": {"sensitive_content_risk_hint": True},
            "secret_hits": [],
            "public_readiness": False,
        },
    )
    sensitive = module.promote("example", force=True)
    assert sensitive["promoted"] is False
    assert (
        sensitive["reason"]
        == "sensitive_repository_requires_dedicated_disclosure_review"
    )

    monkeypatch.setattr(
        module,
        "evaluate_repo",
        lambda name: {
            "name": name,
            "observed": True,
            "readiness": {"sensitive_content_risk_hint": False},
            "secret_hits": ["x.py: token"],
            "public_readiness": False,
        },
    )
    secret = module.promote("example", force=True)
    assert secret["promoted"] is False
    assert secret["reason"] == "secret_scan_not_clean"
