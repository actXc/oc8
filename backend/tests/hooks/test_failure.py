"""make_on_failure: durably records a hook failure via a fresh tenant_session
(not the caller's, possibly-poisoned, transaction)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8.capas.service import install_plugin
from oc8.constants import ACME_TENANT_ID
from oc8.hooks.failure import make_on_failure
from oc8.models import CapaInstallation
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_make_on_failure_records_via_fresh_session(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        v = await install_plugin(
            db,
            tenant_id=tenant,
            manifest_data={
                "name": "acme.hooks-failure-adapter",
                "version": "1.0.0",
                "type": "core_extension",
                "trust": "community",
            },
        )
        capa_id = v.capa_id

    on_failure = make_on_failure(tenant, {"p1": capa_id})
    await on_failure("p1")
    await on_failure("p1")

    async with app_session(tenant) as db:
        inst = (
            await db.execute(select(CapaInstallation).where(CapaInstallation.capa_id == capa_id))
        ).scalar_one()
        assert inst.failure_count == 2


async def test_make_on_failure_unknown_plugin_id_is_noop(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    on_failure = make_on_failure(tenant, {})
    await on_failure("not-in-lookup")  # must not raise
