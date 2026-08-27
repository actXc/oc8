# backend/tests/models/test_capa_models.py
from __future__ import annotations

import uuid
from typing import get_args

from oc8 import models as m
from oc8.capas.manifest import PluginType
from oc8.constants import ACME_TENANT_ID
from tests.conftest import AppSessionFactory


async def test_every_manifest_plugin_type_is_a_db_insertable_type(
    app_session: AppSessionFactory,
) -> None:
    """oc8.capas.manifest.PluginType and Capa's ck_capa_type constraint are
    two lists that must agree, and nothing else ties them together: discovery
    validates a manifest against the first without ever touching a database, so a
    type the second does not know about still parses -- and then 500s the first
    time anyone actually installs it. That happened for real with
    `approval_channel` (telegram_approvals, whatsapp_approvals): valid manifests,
    unusable plugins, until migration 0049. This asserts the two lists agree so
    the next type added to one and not the other fails here, not on install."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        for plugin_type in get_args(PluginType):
            p = m.Capa(tenant_id=tenant, name=f"probe.{plugin_type}", type=plugin_type)
            db.add(p)
            await db.flush()


async def test_plugin_roundtrip(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        p = m.Capa(tenant_id=tenant, name="acme.hello", type="core_extension")
        db.add(p)
        await db.flush()
        v = m.CapaVersion(
            tenant_id=tenant,
            capa_id=p.id,
            semver="1.0.0",
            manifest={"name": "acme.hello", "version": "1.0.0", "type": "core_extension"},
            artifact_hash=b"\x00" * 32,
            permissions=["hooks:task.before_create"],
            capabilities=["streaming"],
            entry_points={"backend": "acme_hello:setup"},
        )
        inst = m.CapaInstallation(tenant_id=tenant, capa_id=p.id, status="installed")
        db.add_all([v, inst])
        await db.flush()
        assert v.permissions == ["hooks:task.before_create"]
        assert inst.failure_count == 0
