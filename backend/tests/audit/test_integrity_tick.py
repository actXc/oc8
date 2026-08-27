from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from tests.conftest import AppSessionFactory

from oc8.audit.chain import append_event
from oc8.audit.integrity import get_checkpoint, run_integrity_tick

pytestmark = pytest.mark.asyncio


async def test_tick_verifies_tenants_with_events(
    app_session: AppSessionFactory, pg_url: str
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        # list_active_tenant_ids() reads organization.id, so the tenant must
        # exist as an organization row for the tick to discover it. There is
        # no tenant_id column on organization -- id IS the tenant id -- and
        # slug/tier/region/settings/onboarding_status are NOT NULL (tier/region/settings/onboarding_status'
        # defaults are Python/ORM-side only, so a raw-SQL insert must supply
        # them explicitly). id and slug are bound as separate params (both
        # derived from the same uuid, as text) since asyncpg can't infer one
        # type for a param used as both uuid and text.
        await s.execute(
            sa.text(
                "INSERT INTO organization (id, slug, name, tier, region, settings, onboarding_status) "
                "VALUES (:id, :slug, 'tick-test', 'standard', 'eu', '{}'::jsonb, 'pending') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": str(tenant), "slug": str(tenant)},
        )
        for i in range(2):
            await append_event(
                s,
                tenant_id=tenant,
                actor_type="system",
                actor_id=None,
                category="tool",
                action=f"a.{i}",
            )

    verified = await run_integrity_tick()
    assert verified >= 1

    async with app_session(tenant) as s:
        cp = await get_checkpoint(s, tenant)
    assert cp is not None
    assert cp.status == "ok"
    assert cp.last_seq > 0
