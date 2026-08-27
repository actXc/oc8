"""A plugin-contributed connector is usable ONLY by a tenant that installed AND
enabled that plugin. This is the load-bearing property of plugin code loading:
the import is process-global, the entitlement is per tenant."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.config import get_settings
from oc8.knowledge.connectors.base import ConnectorError
from oc8.knowledge.connectors.registry import (
    available_connector_types,
    get_connector,
    resolve_connector,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

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
    found = find_plugin("demo_connector")
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


async def test_core_connectors_resolve_for_any_tenant(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        assert (await resolve_connector(db, tenant_id=tenant, type_id="website")).type_id == (
            "website"
        )
        assert (await resolve_connector(db, tenant_id=tenant, type_id="upload")).type_id == (
            "upload"
        )


async def test_an_enabled_plugin_connector_resolves_for_that_tenant(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    await _install(app_session, tenant, enable=True)
    async with app_session(tenant) as db:
        c = await resolve_connector(db, tenant_id=tenant, type_id="demo")
        assert c.type_id == "demo"


async def test_another_tenant_cannot_resolve_it(app_session: AppSessionFactory) -> None:
    """THE test. Tenant A enables the plugin, which imports the module into this
    process. Tenant B must still not be able to use it."""
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _install(app_session, tenant_a, enable=True)
    async with app_session(tenant_a) as db:
        assert (await resolve_connector(db, tenant_id=tenant_a, type_id="demo")).type_id == "demo"
    async with app_session(tenant_b) as db:
        with pytest.raises(ConnectorError):
            await resolve_connector(db, tenant_id=tenant_b, type_id="demo")


async def test_installed_but_not_enabled_does_not_resolve(
    app_session: AppSessionFactory,
) -> None:
    """Installing is not consent. enable_plugin is where permissions are granted,
    so an installed-but-not-enabled plugin contributes nothing."""
    tenant = uuid.uuid4()
    await _install(app_session, tenant, enable=False)
    async with app_session(tenant) as db:
        with pytest.raises(ConnectorError):
            await resolve_connector(db, tenant_id=tenant, type_id="demo")


async def test_unknown_type_fails_closed(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(ConnectorError):
            await resolve_connector(db, tenant_id=tenant, type_id="does_not_exist")


async def test_available_types_differ_per_tenant(app_session: AppSessionFactory) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _install(app_session, tenant_a, enable=True)
    async with app_session(tenant_a) as db:
        a_types = await available_connector_types(db, tenant_id=tenant_a)
    async with app_session(tenant_b) as db:
        b_types = await available_connector_types(db, tenant_id=tenant_b)
    assert "demo" in a_types
    assert "demo" not in b_types
    assert "website" in a_types and "website" in b_types


async def test_the_sync_registry_never_returns_a_plugin_connector(
    app_session: AppSessionFactory,
) -> None:
    """get_connector() stays core-only. That is not a bypass -- it can only ever
    return connectors every tenant may use -- but it must not become one."""
    tenant = uuid.uuid4()
    await _install(app_session, tenant, enable=True)
    async with app_session(tenant) as db:
        assert (await resolve_connector(db, tenant_id=tenant, type_id="demo")).type_id == "demo"
    with pytest.raises(ConnectorError):
        get_connector("demo")


async def test_a_disabled_plugin_stops_resolving(app_session: AppSessionFactory) -> None:
    from oc8.capas.lifecycle import disable_plugin

    tenant = uuid.uuid4()
    found = find_plugin("demo_connector")
    assert found is not None and found.manifest is not None
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=found.manifest)
        plugin_id = version.capa_id
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=plugin_id,
            granted_permissions=list(version.permissions),
        )
    async with app_session(tenant) as db:
        assert (await resolve_connector(db, tenant_id=tenant, type_id="demo")).type_id == "demo"
    async with app_session(tenant) as db:
        await disable_plugin(db, tenant_id=tenant, capa_id=plugin_id, reason="test")
    async with app_session(tenant) as db:
        with pytest.raises(ConnectorError):
            await resolve_connector(db, tenant_id=tenant, type_id="demo")
