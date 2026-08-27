"""Data-only plugin types materialise on enable.

`skill`, `flow_template` and `tool_pack` need no code loading at all -- they are
manifest data that has to become domain rows, exactly like `department_template`
already did. Enabling is the trigger because that is where consent happens.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.capas.lifecycle import disable_plugin, enable_plugin
from oc8.capas.service import install_plugin
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


SKILL_MANIFEST: dict[str, Any] = {
    "name": "acme.invoice-skill",
    "version": "1.0.0",
    "type": "skill",
    "trust": "first_party",
    "skill_template": {
        "name": "Invoice check",
        "description": "Checks invoices against the PO.",
        "category": "finance",
        "instruction": "Compare the invoice total to the purchase order.",
        "requires_tools": ["erp.read"],
        "guardrails": ["Never approve above 10k without a human."],
    },
}

FLOW_MANIFEST: dict[str, Any] = {
    "name": "acme.onboarding-flow",
    "version": "1.0.0",
    "type": "flow_template",
    "trust": "first_party",
    "flow_template": {
        "name": "Onboarding",
        "spec": {"steps": [{"id": "welcome", "kind": "task"}]},
    },
}

TOOLPACK_MANIFEST: dict[str, Any] = {
    "name": "acme.odoo-tools",
    "version": "1.0.0",
    "type": "tool_pack",
    "trust": "first_party",
    "tool_pack": {
        "connections": [
            {
                "name": "Odoo (acme)",
                "server_url": "odoo-mcp",
                "transport": "stdio",
                "scopes": ["internal"],
                "config": {"args": ["--readonly"]},
            }
        ]
    },
}


async def _install_and_enable(
    session: AppSessionFactory, tenant: uuid.UUID, manifest: dict[str, Any]
) -> uuid.UUID:
    async with session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=manifest)
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=version.capa_id,
            granted_permissions=list(version.permissions),
        )
        return version.capa_id


# ---------------------------------------------------------------- skill


async def test_enabling_a_skill_plugin_creates_a_real_skill(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _install_and_enable(app_session, tenant, SKILL_MANIFEST)
    async with app_session(tenant) as db:
        skill = (
            await db.execute(select(m.Skill).where(m.Skill.name == "Invoice check"))
        ).scalar_one()
        assert skill.tenant_id == tenant
        assert skill.origin == "store"
        assert skill.current_version_id is not None
        version = await db.get(m.SkillVersion, skill.current_version_id)
        assert version is not None
        assert version.definition["instruction"].startswith("Compare the invoice")
        assert version.definition["requires"]["tools"] == ["erp.read"]


async def test_the_materialised_skill_is_loadable_at_runtime(
    app_session: AppSessionFactory,
) -> None:
    """A row that renders in a list is not a skill. It has to parse."""
    from oc8.skills.schema import parse_definition

    tenant = uuid.uuid4()
    await _install_and_enable(app_session, tenant, SKILL_MANIFEST)
    async with app_session(tenant) as db:
        version = (await db.execute(select(m.SkillVersion))).scalars().first()
        assert version is not None
        parsed = parse_definition(version.definition)
        assert parsed.instruction
        assert [r.tool for r in parsed.requires_tools] == ["erp.read"]


async def test_a_skill_plugin_is_scoped_to_the_enabling_tenant(
    app_session: AppSessionFactory,
) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _install_and_enable(app_session, tenant_a, SKILL_MANIFEST)
    async with app_session(tenant_b) as db:
        rows = (await db.execute(select(m.Skill))).scalars().all()
        assert not [s for s in rows if s.name == "Invoice check"]


async def test_re_enabling_does_not_duplicate_the_skill(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    capa_id = await _install_and_enable(app_session, tenant, SKILL_MANIFEST)
    async with app_session(tenant) as db:
        await enable_plugin(db, tenant_id=tenant, capa_id=capa_id, granted_permissions=[])
    async with app_session(tenant) as db:
        rows = (
            (await db.execute(select(m.Skill).where(m.Skill.name == "Invoice check")))
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_disabling_keeps_the_materialised_rows(
    app_session: AppSessionFactory,
) -> None:
    """Toggling a plugin off must not delete work built on top of it. Removal is
    a separate, deliberate act."""
    tenant = uuid.uuid4()
    capa_id = await _install_and_enable(app_session, tenant, SKILL_MANIFEST)
    async with app_session(tenant) as db:
        await disable_plugin(db, tenant_id=tenant, capa_id=capa_id, reason="test")
    async with app_session(tenant) as db:
        rows = (
            (await db.execute(select(m.Skill).where(m.Skill.name == "Invoice check")))
            .scalars()
            .all()
        )
        assert len(rows) == 1


# ---------------------------------------------------------------- flow


async def test_enabling_a_flow_plugin_creates_a_flow_and_version(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _install_and_enable(app_session, tenant, FLOW_MANIFEST)
    async with app_session(tenant) as db:
        flow = (await db.execute(select(m.Flow).where(m.Flow.name == "Onboarding"))).scalar_one()
        assert flow.current_version_id is not None
        version = await db.get(m.FlowVersion, flow.current_version_id)
        assert version is not None
        assert version.spec["steps"][0]["id"] == "welcome"
        assert version.semver == "1.0.0"


async def test_re_enabling_a_flow_plugin_does_not_duplicate(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    capa_id = await _install_and_enable(app_session, tenant, FLOW_MANIFEST)
    async with app_session(tenant) as db:
        await enable_plugin(db, tenant_id=tenant, capa_id=capa_id, granted_permissions=[])
    async with app_session(tenant) as db:
        assert len((await db.execute(select(m.FlowVersion))).scalars().all()) == 1


# ---------------------------------------------------------------- tool pack


async def test_enabling_a_tool_pack_creates_a_disconnected_mcp_connection(
    app_session: AppSessionFactory,
) -> None:
    """The connection is created UNCONNECTED: a plugin may describe a server,
    but only an operator pressing Test may declare it reachable."""
    tenant = uuid.uuid4()
    await _install_and_enable(app_session, tenant, TOOLPACK_MANIFEST)
    async with app_session(tenant) as db:
        conn = (
            await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)"))
        ).scalar_one()
        assert conn.transport == "stdio"
        assert conn.server_url == "odoo-mcp"
        assert conn.scopes == ["internal"]
        assert conn.connected is False
        assert conn.health == {}


async def test_re_enabling_a_tool_pack_does_not_duplicate(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    capa_id = await _install_and_enable(app_session, tenant, TOOLPACK_MANIFEST)
    async with app_session(tenant) as db:
        await enable_plugin(db, tenant_id=tenant, capa_id=capa_id, granted_permissions=[])
    async with app_session(tenant) as db:
        rows = (
            (await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)")))
            .scalars()
            .all()
        )
        assert len(rows) == 1


async def test_upgrading_a_tool_pack_carries_its_own_declarations_onto_a_live_connection(
    app_session: AppSessionFactory,
) -> None:
    """A connection must not stay frozen at the version that created it.

    focus_spec and value_spec are the neutral seams -- how the core reads a
    call's record and its monetary value without knowing the vendor. Adding an
    entity to them is the normal way to fix a blank live log, and it fixes
    nothing at all if an already-installed tenant never sees the change.
    """
    tenant = uuid.uuid4()
    capa_id = await _install_and_enable(app_session, tenant, TOOLPACK_MANIFEST)

    upgraded = json.loads(json.dumps(TOOLPACK_MANIFEST))
    upgraded["version"] = "1.1.0"
    upgraded["tool_pack"]["connections"][0]["config"]["focus_spec"] = {
        "labels": {"helpdesk.ticket": "Ticket"}
    }
    upgraded["tool_pack"]["connections"][0]["config"]["outward_tools"] = ["post_message"]
    upgraded["tool_pack"]["connections"][0]["scopes"] = {"read": ["get_record"]}
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=upgraded)
        await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])
    assert capa_id == version.capa_id

    async with app_session(tenant) as db:
        conn = (
            await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)"))
        ).scalar_one()
        assert conn.config["focus_spec"] == {"labels": {"helpdesk.ticket": "Ticket"}}
        # The guard that stops a duplicate customer message is useless if a
        # plugin declaring it cannot reach an installation that already exists.
        assert conn.config["outward_tools"] == ["post_message"]
        assert conn.scopes == {"read": ["get_record"]}


async def test_a_tool_pack_set_up_for_two_departments_still_enables(
    app_session: AppSessionFactory,
) -> None:
    """One plugin, one connection NAME, several rows -- the setup form creates
    one per department. Reading that back as one-or-none turned the second
    department into a 500 on every enable, and left every row unrefreshed."""
    tenant = uuid.uuid4()
    await _install_and_enable(app_session, tenant, TOOLPACK_MANIFEST)
    async with app_session(tenant) as db:
        first = (
            await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)"))
        ).scalar_one()
        db.add(
            m.McpConnection(
                tenant_id=tenant,
                department_id=uuid.uuid4(),
                name=first.name,
                server_url=first.server_url,
                transport=first.transport,
                scopes=first.scopes,
                config=dict(first.config),
                connected=True,
                health={},
            )
        )
        await db.commit()

    upgraded = json.loads(json.dumps(TOOLPACK_MANIFEST))
    upgraded["version"] = "1.1.0"
    upgraded["tool_pack"]["connections"][0]["config"]["focus_spec"] = {"labels": {"a": "A"}}
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=upgraded)
        await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])

    async with app_session(tenant) as db:
        rows = (
            (await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)")))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert all(r.config["focus_spec"] == {"labels": {"a": "A"}} for r in rows)


async def test_upgrading_a_tool_pack_leaves_what_the_operator_configured_alone(
    app_session: AppSessionFactory,
) -> None:
    """The URL, the database and the credentials belong to the operator who set
    them up. A plugin upgrade that reset them would take a tenant offline."""
    tenant = uuid.uuid4()
    await _install_and_enable(app_session, tenant, TOOLPACK_MANIFEST)
    async with app_session(tenant) as db:
        conn = (
            await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)"))
        ).scalar_one()
        conn.config = {**conn.config, "env": {"ODOO_URL": "http://operator.example"}}
        await db.commit()

    upgraded = json.loads(json.dumps(TOOLPACK_MANIFEST))
    upgraded["version"] = "1.1.0"
    upgraded["tool_pack"]["connections"][0]["config"]["env"] = {"ODOO_URL": "http://from-plugin"}
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=upgraded)
        await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])

    async with app_session(tenant) as db:
        conn = (
            await db.execute(select(m.McpConnection).where(m.McpConnection.name == "Odoo (acme)"))
        ).scalar_one()
        assert conn.config["env"] == {"ODOO_URL": "http://operator.example"}


# ---------------------------------------------------------------- safety


async def test_a_plugin_of_a_type_with_no_data_block_enables_cleanly(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _install_and_enable(
        app_session,
        tenant,
        {"name": "acme.bare", "version": "1.0.0", "type": "skill", "trust": "first_party"},
    )
    async with app_session(tenant) as db:
        assert (await db.execute(select(m.Skill))).scalars().all() == []


async def test_a_malformed_skill_block_does_not_break_enabling(
    app_session: AppSessionFactory,
) -> None:
    """A plugin whose data cannot be materialised must not leave the tenant with
    a half-enabled plugin -- the enable itself is what matters."""
    tenant = uuid.uuid4()
    await _install_and_enable(
        app_session,
        tenant,
        {
            "name": "acme.broken-skill",
            "version": "1.0.0",
            "type": "skill",
            "trust": "first_party",
            "skill_template": {"name": "No instruction", "instruction": "   "},
        },
    )
    async with app_session(tenant) as db:
        inst = (await db.execute(select(m.CapaInstallation))).scalars().one()
        assert inst.status == "enabled"
        assert (await db.execute(select(m.Skill))).scalars().all() == []
