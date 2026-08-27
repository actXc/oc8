from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.config import get_settings
from oc8.seed.model_providers import seed_builtin_model_providers
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_REPO_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _real_plugins_path(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """`opaas_ai_provider` lives in the repo's top-level `capas/`, not the
    fixture dirs other plugin tests point at -- this is the one plugin that
    must resolve for real, since the point of the seed is that it ships with
    oc8 itself."""
    monkeypatch.setenv("OC8_CAPAS_PATH", str(_REPO_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_installs_and_enables_opaas_ai_for_a_new_tenant(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        await seed_builtin_model_providers(s, tenant_id=tenant)
        plugin = (
            await s.execute(
                select(m.Capa).where(
                    m.Capa.tenant_id == tenant, m.Capa.name == "opaas_ai_provider"
                )
            )
        ).scalar_one()
        inst = (
            await s.execute(
                select(m.CapaInstallation).where(
                    m.CapaInstallation.tenant_id == tenant,
                    m.CapaInstallation.capa_id == plugin.id,
                )
            )
        ).scalar_one()
        assert inst.status == "enabled"


async def test_is_idempotent_per_tenant(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        await seed_builtin_model_providers(s, tenant_id=tenant)
        await seed_builtin_model_providers(s, tenant_id=tenant)  # must not raise
        rows = (
            await s.execute(
                select(m.Capa).where(
                    m.Capa.tenant_id == tenant, m.Capa.name == "opaas_ai_provider"
                )
            )
        ).scalars().all()
        assert len(rows) == 1
