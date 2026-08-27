"""Tenant-defined human roles: the one place `role` and `role_permission` are written."""

from __future__ import annotations

from oc8.roles.service import (
    RoleRefused,
    RoleSummary,
    assign_role,
    create_role,
    delete_role,
    grants_of,
    holders_of,
    list_roles,
    load_role,
    update_role,
    validated_name,
    validated_permissions,
)

__all__ = [
    "RoleRefused",
    "RoleSummary",
    "assign_role",
    "create_role",
    "delete_role",
    "grants_of",
    "holders_of",
    "list_roles",
    "load_role",
    "update_role",
    "validated_name",
    "validated_permissions",
]
