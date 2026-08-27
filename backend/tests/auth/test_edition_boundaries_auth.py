"""Tests for edition boundary checks specific to auth (WP-E).

Verifies that:
- Dev-only endpoints are properly gated with _dev_only() checks
- Community frontend has no SaaS control-plane imports
- Auth providers don't import SaaS modules
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_edition_boundaries_check_passes() -> None:
    """Verify the edition boundary check script passes."""
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "check_edition_boundaries.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Boundary check failed:\n{result.stderr}"
    assert "Edition boundary checks passed" in result.stdout


def test_dev_endpoints_have_dev_only_guard() -> None:
    """Verify dev endpoints call _dev_only() for production gating."""
    repo_root = Path(__file__).resolve().parents[3]
    auth_file = repo_root / "backend" / "src" / "oc8" / "api" / "v1" / "auth.py"
    content = auth_file.read_text()

    # Each dev endpoint should call _dev_only()
    dev_endpoints = [
        ("dev_login", "/auth/dev-login"),
        ("dev_tenants", "/auth/dev-tenants"),
        ("dev_members", "/auth/dev-members"),
        ("create_dev_tenant", "/auth/dev-tenants"),  # POST variant
    ]

    for func_name, endpoint_path in dev_endpoints:
        # Find the function definition
        func_start = content.find(f"async def {func_name}(")
        assert func_start != -1, f"Function {func_name} not found"

        # Find the next function definition to get the body
        next_def = content.find("\nasync def ", func_start + 1)
        next_decorator = content.find("\n@router", func_start + 1)

        end_pos = min(p for p in [next_def, next_decorator] if p > func_start)
        func_body = content[func_start:end_pos]

        assert "_dev_only()" in func_body, f"{func_name} ({endpoint_path}) missing _dev_only() guard"


def test_auth_provider_no_saas_imports() -> None:
    """Verify auth providers don't import SaaS control-plane."""
    repo_root = Path(__file__).resolve().parents[3]
    provider_file = repo_root / "backend" / "src" / "oc8" / "auth" / "provider.py"
    content = provider_file.read_text()

    forbidden = ["oc8_cloud_controlplane", "oc8_cloud", "saas.control_plane"]
    for module in forbidden:
        assert module not in content, f"Auth provider imports forbidden module: {module}"
