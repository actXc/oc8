from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import disable_plugin, enable_plugin
from oc8.capas.service import install_plugin
from oc8.config import get_settings
from oc8.credentials.registry import (
    CredentialTypeNotFound,
    get_credential_type,
    list_credential_types,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

# Mirrors tests/knowledge/test_connector_tenant_scope.py's fixture plugin
# convention exactly, for the same enabled-Capa security boundary: a
# `credential_type` a tenant's Capa never installed-and-enabled must never be
# reachable, any more than a connector would be.
FIXTURES = Path(__file__).resolve().parents[1] / "plugins" / "fixtures"


@pytest.fixture(autouse=True)
def _plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(FIXTURES))
    get_settings.cache_clear()
    loader.reset_for_tests()
    contributions.reset_for_tests()
    yield
    loader.reset_for_tests()
    contributions.reset_for_tests()
    get_settings.cache_clear()


async def _install(session: AppSessionFactory, tenant: uuid.UUID, *, enable: bool) -> None:
    found = find_plugin("demo_credential_type")
    assert found is not None and found.manifest is not None
    async with session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=found.manifest)
        if enable:
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=version.capa_id,
                granted_permissions=list(version.permissions),
            )


async def test_unknown_type_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(CredentialTypeNotFound):
            await get_credential_type(db, tenant_id=tenant, name="not_a_real_type")


async def test_list_returns_a_list(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        types = await list_credential_types(db, tenant_id=tenant)
        assert isinstance(types, list)


async def test_an_enabled_capas_credential_type_is_reachable(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _install(app_session, tenant, enable=True)
    async with app_session(tenant) as db:
        spec = await get_credential_type(db, tenant_id=tenant, name="demo_cred")
        assert spec.name == "demo_cred"
        names = [t.name for t in await list_credential_types(db, tenant_id=tenant)]
        assert "demo_cred" in names


async def test_another_tenant_without_the_capa_cannot_reach_it(
    app_session: AppSessionFactory,
) -> None:
    """THE test. Tenant A enables the plugin, which imports/parses its
    manifest into this process. Tenant B, who never installed it, must still
    not be able to reach its credential_type."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _install(app_session, tenant_a, enable=True)
    async with app_session(tenant_a) as db:
        assert (await get_credential_type(db, tenant_id=tenant_a, name="demo_cred")).name == (
            "demo_cred"
        )
    async with app_session(tenant_b) as db:
        with pytest.raises(CredentialTypeNotFound):
            await get_credential_type(db, tenant_id=tenant_b, name="demo_cred")
        names = [t.name for t in await list_credential_types(db, tenant_id=tenant_b)]
        assert "demo_cred" not in names


async def test_installed_but_not_enabled_is_not_reachable(
    app_session: AppSessionFactory,
) -> None:
    """Installing is not consent -- enable_plugin is where permissions are
    granted, so an installed-but-not-enabled Capa's credential_types
    contribute nothing, exactly like its connectors would."""
    tenant = uuid.uuid4()
    await _install(app_session, tenant, enable=False)
    async with app_session(tenant) as db:
        with pytest.raises(CredentialTypeNotFound):
            await get_credential_type(db, tenant_id=tenant, name="demo_cred")


async def test_a_disabled_capa_stops_surfacing_its_credential_type(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    found = find_plugin("demo_credential_type")
    assert found is not None and found.manifest is not None
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=found.manifest)
        capa_id = version.capa_id
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=capa_id,
            granted_permissions=list(version.permissions),
        )
    async with app_session(tenant) as db:
        assert (await get_credential_type(db, tenant_id=tenant, name="demo_cred")).name == (
            "demo_cred"
        )
    async with app_session(tenant) as db:
        await disable_plugin(db, tenant_id=tenant, capa_id=capa_id, reason="test")
    async with app_session(tenant) as db:
        with pytest.raises(CredentialTypeNotFound):
            await get_credential_type(db, tenant_id=tenant, name="demo_cred")
