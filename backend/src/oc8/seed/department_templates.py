"""Built-in department_template bundles seeded into every tenant (B2 §13/§16).
Instantiate via POST /plugins/{id}/instantiate-department."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.capas.service import install_plugin

SALES_DEPARTMENT_TEMPLATE: dict[str, object] = {
    "name": "sales-department",
    "version": "1.0.0",
    "type": "department_template",
    "trust": "first_party",
    "department_template": {
        "frame": {"tools": {}, "kbs": [], "memory": {}},
        "agents": [
            {
                "name": "Head of Sales",
                "role_title": "Sales Lead",
                "mission": "Own the sales pipeline and coordinate the team.",
                "is_team_lead": True,
                "persona": "# Head of Sales\nDecisive, coaches the reps, owns the number.",
                "skills": ["crm", "forecasting"],
            },
            {
                "name": "Account Executive",
                "role_title": "AE",
                "mission": "Close inbound and outbound opportunities.",
                "reports_to": "Head of Sales",
                "persona": "# Account Executive\nConsultative closer.",
                "skills": ["crm"],
            },
            {
                "name": "SDR",
                "role_title": "Sales Development Rep",
                "mission": "Qualify leads and book meetings.",
                "reports_to": "Head of Sales",
                "persona": "# SDR\nHigh-activity prospecting.",
                "skills": ["crm"],
            },
        ],
    },
}


async def seed_department_templates(db: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Install the built-in department templates for a tenant (idempotent: a
    re-seed would otherwise collide with uq_capa_tenant_name / a duplicate
    CapaVersion, so this skips the install if the plugin already exists for
    this tenant). Add/flush only — never commits; the caller owns the commit."""
    existing = (
        await db.execute(
            select(m.Capa).where(m.Capa.name == "sales-department", m.Capa.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    await install_plugin(db, tenant_id=tenant_id, manifest_data=SALES_DEPARTMENT_TEMPLATE)
