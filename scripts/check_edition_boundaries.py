#!/usr/bin/env python3
"""Fail fast when the Community/Enterprise composition boundary erodes.

This is deliberately dependency-free so it can run before package installation
and without access to Docker.  Runtime smoke tests remain a separate CI job.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMUNITY = ROOT / "backend" / "src" / "oc8"
ENTERPRISE = ROOT / "enterprise" / "backend" / "src" / "oc8_enterprise"
SAAS_PACKAGES = [
    ROOT / "saas" / "control-plane" / "src" / "oc8_cloud_controlplane",
    ROOT / "saas" / "billing" / "src" / "oc8_cloud_billing",
    ROOT / "saas" / "fleet" / "src" / "oc8_cloud_fleet",
    ROOT / "saas" / "marketplace" / "src" / "oc8_cloud_marketplace",
]
FORBIDDEN_COMMUNITY = {"oc8_enterprise", "oc8_cloud"}
FORBIDDEN_ENTERPRISE = {"oc8_cloud"}
# SaaS packages may depend on Community APIs via published contracts,
# but must not import Community/Enterprise implementation internals.
# Contracts are expected to be imported from non-internals paths.
FORBIDDEN_SAAS = set()  # Reserved for future contract validation
PRIVATE_FRONTEND_SEGMENTS = {"enterprise", "saas"}

# Auth-specific boundaries (WP-E)
DEV_ONLY_ROUTES = {"/auth/dev-login", "/auth/dev-tenants", "/auth/dev-members"}
SAAS_CONTROL_PLANE_PACKAGES = {"oc8_cloud_controlplane"}


def imported_roots(path: Path) -> list[tuple[int, str]]:
    """Return imported top-level packages without importing project code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name.split(".", 1)[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module.split(".", 1)[0]))
    return found


def check_imports(source: Path, forbidden: set[str], label: str) -> list[str]:
    violations: list[str] = []
    if not source.is_dir():
        return [f"{label} source directory is missing: {source.relative_to(ROOT)}"]
    for path in source.rglob("*.py"):
        for lineno, imported in imported_roots(path):
            if imported in forbidden:
                violations.append(
                    f"{path.relative_to(ROOT)}:{lineno}: {label} imports forbidden {imported}"
                )
    return violations


def check_community_frontend_context() -> list[str]:
    """Ensure the community image has an allowlisted frontend-only context."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = ROOT / "frontend" / "Dockerfile"
    violations: list[str] = []
    frontend_block = re.search(r"^  frontend:\n(?P<body>.*?)(?=^  \w|\Z)", compose, re.MULTILINE | re.DOTALL)
    if not frontend_block:
        return ["docker-compose.yml has no frontend service"]
    body = frontend_block.group("body")
    if not re.search(r"^\s*context:\s*\./frontend\s*$", body, re.MULTILINE):
        violations.append("frontend image must use ./frontend as its build context")
    if not re.search(r"^\s*dockerfile:\s*Dockerfile\s*$", body, re.MULTILINE):
        violations.append("frontend image must use frontend/Dockerfile")
    if not dockerfile.is_file():
        return violations + ["frontend/Dockerfile is missing"]

    # With ./frontend as Docker's context, a COPY source cannot legally escape
    # into enterprise/ or saas/.  Reject a future switch back to a broad context
    # hidden in a parent-relative COPY as a defense in depth.
    for lineno, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.upper().startswith("COPY ") and re.search(r"(?:^|[/\s])\.\.(?:[/\s]|$)", stripped):
            violations.append(f"frontend/Dockerfile:{lineno}: COPY must not use a parent-relative path")
    return violations


def check_community_frontend_imports() -> list[str]:
    """Reject static imports from private edition source trees.

    Docker's narrow build context is the primary isolation boundary.  This
    source-level check gives developers a useful failure before Docker runs and
    covers local bundler configurations that could otherwise alias a private
    directory into the Community app.
    """
    source = ROOT / "frontend" / "src"
    if not source.is_dir():
        return ["Community frontend source directory is missing: frontend/src"]

    violations: list[str] = []
    import_pattern = re.compile(
        r"(?:from\s+|import\s*\(|require\s*\()\s*['\"](?P<specifier>[^'\"]+)['\"]"
    )
    for path in (*source.rglob("*.ts"), *source.rglob("*.tsx")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = import_pattern.search(line)
            if not match:
                continue
            segments = set(re.split(r"[/@:_-]+", match.group("specifier").lower()))
            if segments & PRIVATE_FRONTEND_SEGMENTS:
                violations.append(
                    f"{path.relative_to(ROOT)}:{lineno}: Community frontend imports a private edition"
                )
    return violations


def check_migration_ownership() -> list[str]:
    """Prevent Enterprise revisions from leaking into Community's revision chain."""
    versions = ROOT / "backend" / "migrations" / "versions"
    violations: list[str] = []
    for path in versions.glob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "oc8_enterprise" in content or "enterprise." in content:
            violations.append(
                f"{path.relative_to(ROOT)}: Enterprise migration content belongs to enterprise/migrations"
            )
    return violations


def check_saas_scaffold() -> list[str]:
    """Verify SaaS boundary scaffold exists and is properly isolated.

    Each SaaS subpackage must:
    - Have an __init__.py with a health() function
    - Have tests/test_import.py to verify isolation
    - Not import Community or Enterprise implementation modules
    """
    violations: list[str] = []

    # Verify scaffold directories exist
    for pkg in SAAS_PACKAGES:
        if not pkg.is_dir():
            violations.append(f"SaaS package scaffold missing: {pkg.relative_to(ROOT)}")
            continue

        # Verify __init__.py exists
        init_file = pkg / "__init__.py"
        if not init_file.is_file():
            violations.append(f"SaaS package missing __init__.py: {pkg.relative_to(ROOT)}")
            continue

        # Verify health() function is defined
        content = init_file.read_text(encoding="utf-8")
        if "def health()" not in content:
            violations.append(f"SaaS package missing health() function: {pkg.relative_to(ROOT)}")

    # Verify parent saas/ directory has README and pyproject.toml
    saas_root = ROOT / "saas"
    if not (saas_root / "README.md").is_file():
        violations.append("saas/README.md is missing")
    if not (saas_root / "pyproject.toml").is_file():
        violations.append("saas/pyproject.toml is missing")

    return violations


def check_dev_endpoint_gating() -> list[str]:
    """Verify dev-only endpoints are properly gated with _dev_only() checks.

    WP-E requirement: dev endpoints (/auth/dev-login, /auth/dev-tenants, /auth/dev-members)
    must return 404 in production mode (is_dev=False).

    This checks that each dev route calls _dev_only() which raises HTTPException(404).
    """
    violations: list[str] = []
    auth_file = COMMUNITY / "api" / "v1" / "auth.py"

    if not auth_file.is_file():
        return [f"Auth endpoint file missing: {auth_file.relative_to(ROOT)}"]

    content = auth_file.read_text(encoding="utf-8")

    # Use regex to find dev endpoints and check for _dev_only guard
    # Pattern: @router.post/get("/auth/dev-*") followed by async def and _dev_only call
    dev_routes = [
        r'@router\.(?:post|get)\s*\(\s*"/auth/dev-login"',
        r'@router\.(?:post|get)\s*\(\s*"/auth/dev-tenants"',
        r'@router\.(?:post|get)\s*\(\s*"/auth/dev-members"',
    ]

    for pattern in dev_routes:
        # Find the route decorator
        match = re.search(pattern, content)
        if match:
            # Find the function definition after this decorator
            # Look for the next function def and check if it contains _dev_only()
            after_decorator = content[match.end() :]
            func_match = re.search(
                r"async\s+def\s+(\w+)\s*\([^)]*\)\s*(?:->.*?):\s*(.*?)(?=\n(?:async\s+def|@router|def\s+\w+\s*\(|$))",
                after_decorator,
                re.DOTALL,
            )
            if func_match:
                func_body = func_match.group(2)
                if "_dev_only()" not in func_body:
                    route_match = re.search(r'"/auth/dev-\w+"', pattern)
                    if route_match:
                        route = route_match.group(0).strip('"')
                        violations.append(
                            f"{auth_file.relative_to(ROOT)}: {route} endpoint missing _dev_only() guard"
                        )

    return violations


def check_community_frontend_saas_isolation() -> list[str]:
    """Verify Community frontend has no SaaS control-plane imports.

    WP-E requirement: Community frontend must not import or reference SaaS
    control-plane, tenant-picker, or multi-tenant UI components.
    """
    source = ROOT / "frontend" / "src"
    violations: list[str] = []

    if not source.is_dir():
        return [f"Community frontend source directory is missing: {source.relative_to(ROOT)}"]

    # Patterns that must not appear in Community frontend source
    forbidden_patterns = [
        r"(?:from\s+|import\s*\(|require\s*\()\s*['\"](?:[^'\"]*[/@])?(?:saas|control.?plane|tenant.?picker)[^'\"]*['\"]",
        r"(?:from\s+|import\s*\(|require\s*\()\s*['\"](?:[^'\"]*[/@])?saas/",
        r"(?:from\s+|import\s*\(|require\s*\()\s*['\"](?:.*control-plane)",
        r"tenant[_-]picker|company[_-]picker|tenant[_-]selector",  # UI component refs
    ]

    import_pattern = re.compile("|".join(forbidden_patterns))

    for path in (*source.rglob("*.ts"), *source.rglob("*.tsx")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if import_pattern.search(line):
                violations.append(
                    f"{path.relative_to(ROOT)}:{lineno}: Community frontend references SaaS/control-plane"
                )

    return violations


def check_auth_provider_boundaries() -> list[str]:
    """Verify auth providers don't import SaaS control-plane code.

    WP-E requirement: `oc8.auth.provider` (Community's identity providers)
    must not depend on SaaS modules.
    """
    violations: list[str] = []
    auth_provider_file = COMMUNITY / "auth" / "provider.py"

    if not auth_provider_file.is_file():
        return []  # No file to check, OK

    for lineno, imported in imported_roots(auth_provider_file):
        if imported in SAAS_CONTROL_PLANE_PACKAGES:
            violations.append(
                f"{auth_provider_file.relative_to(ROOT)}:{lineno}: auth provider imports SaaS control-plane {imported}"
            )

    return violations


def check_saas_control_plane_isolation() -> list[str]:
    """Ensure SaaS control-plane packages don't appear in Community chains.

    WP-E requirement: Community and Enterprise must not import
    oc8_cloud_controlplane.
    """
    violations: list[str] = []

    for source in [COMMUNITY, ENTERPRISE]:
        for path in source.rglob("*.py"):
            for lineno, imported in imported_roots(path):
                if imported in SAAS_CONTROL_PLANE_PACKAGES:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{lineno}: {source.name} imports SaaS control-plane {imported}"
                    )

    return violations


def main() -> int:
    violations = [
        *check_imports(COMMUNITY, FORBIDDEN_COMMUNITY, "Community"),
        *check_imports(ENTERPRISE, FORBIDDEN_ENTERPRISE, "Enterprise"),
        *check_community_frontend_context(),
        *check_community_frontend_imports(),
        *check_migration_ownership(),
        *check_saas_scaffold(),
        # WP-E: Auth-specific boundary checks
        *check_dev_endpoint_gating(),
        *check_community_frontend_saas_isolation(),
        *check_auth_provider_boundaries(),
        *check_saas_control_plane_isolation(),
    ]
    if violations:
        print("Edition boundary violations:", *violations, sep="\n- ", file=sys.stderr)
        return 1
    print("Edition boundary checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
