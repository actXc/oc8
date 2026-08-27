"""A runtime_adapter plugin can ship its own implementation.

The lifecycle for runtimes was already complete -- install, enable, tenant-scoped
resolution, §8.7 capability negotiation. Only the final step was closed: the
implementation lookup was a dict of two built-ins keyed by plugin name, so a
third-party runtime resolved and then died on "has no implementation".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import disable_plugin, enable_plugin
from oc8.capas.service import install_plugin
from oc8.config import get_settings
from oc8.runtime.registry import (
    RuntimeResolutionError,
    is_runtime_executable,
    resolve_runtime,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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


async def _agent_with_runtime(
    session: AppSessionFactory, tenant: uuid.UUID, *, enable: bool = True
) -> tuple[uuid.UUID, Any]:
    from oc8 import models as m

    found = find_plugin("demo_runtime")
    assert found is not None and found.manifest is not None, "fixture not discovered"

    async with session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=found.manifest)
        plugin_id = version.capa_id
        if enable:
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=plugin_id,
                granted_permissions=list(version.permissions),
            )
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Runner",
            status="stopped",
            narrowing={},
            definition={},
            runtime_ref=str(plugin_id),
        )
        db.add(agent)
        await db.flush()
        return plugin_id, agent


async def test_a_plugin_supplied_runtime_resolves_for_the_enabling_tenant(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    _plugin_id, agent = await _agent_with_runtime(app_session, tenant)
    async with app_session(tenant) as db:
        runtime = await resolve_runtime(db, tenant_id=tenant, agent=agent)
    assert type(runtime).__name__ == "DemoRuntime"


async def test_the_built_in_runtimes_still_resolve(app_session: AppSessionFactory) -> None:
    """Opening the registry must not displace the built-ins."""
    from oc8 import models as m

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="Default",
            status="stopped",
            narrowing={},
            definition={},
        )
        db.add(agent)
        await db.flush()
        runtime = await resolve_runtime(db, tenant_id=tenant, agent=agent)
    assert type(runtime).__name__ == "Oc8AgentRuntime"
    assert is_runtime_executable("oc8.agent-runtime")


async def test_another_tenant_cannot_use_the_contributed_runtime(
    app_session: AppSessionFactory,
) -> None:
    """The module is imported process-wide once tenant A enables it. Tenant B
    pointing an agent at that plugin id must still fail closed."""
    from oc8 import models as m

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    plugin_id, _agent = await _agent_with_runtime(app_session, tenant_a)

    async with app_session(tenant_b) as db:
        dept = m.Department(tenant_id=tenant_b, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        foreign = m.Agent(
            tenant_id=tenant_b,
            department_id=dept.id,
            name="Sneaky",
            status="stopped",
            narrowing={},
            definition={},
            runtime_ref=str(plugin_id),
        )
        db.add(foreign)
        await db.flush()
        with pytest.raises(RuntimeResolutionError):
            await resolve_runtime(db, tenant_id=tenant_b, agent=foreign)


async def test_installed_but_not_enabled_does_not_resolve(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    _plugin_id, agent = await _agent_with_runtime(app_session, tenant, enable=False)
    async with app_session(tenant) as db:
        with pytest.raises(RuntimeResolutionError):
            await resolve_runtime(db, tenant_id=tenant, agent=agent)


async def test_a_disabled_runtime_stops_resolving(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    plugin_id, agent = await _agent_with_runtime(app_session, tenant)
    async with app_session(tenant) as db:
        await disable_plugin(db, tenant_id=tenant, capa_id=plugin_id, reason="test")
    async with app_session(tenant) as db:
        with pytest.raises(RuntimeResolutionError):
            await resolve_runtime(db, tenant_id=tenant, agent=agent)


async def test_a_runtime_plugin_that_contributes_nothing_fails_clearly(
    app_session: AppSessionFactory,
) -> None:
    """A manifest can claim type runtime_adapter without shipping an
    implementation. That must be a clear error, not a silent fallback to the
    default runtime -- the agent was explicitly pointed elsewhere."""
    from oc8 import models as m

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        version = await install_plugin(
            db,
            tenant_id=tenant,
            manifest_data={
                "name": "acme.empty-runtime",
                "version": "1.0.0",
                "type": "runtime_adapter",
                "trust": "first_party",
            },
        )
        await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])
        dept = m.Department(tenant_id=tenant, name="Ops", frame={})
        db.add(dept)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=dept.id,
            name="A",
            status="stopped",
            narrowing={},
            definition={},
            runtime_ref=str(version.capa_id),
        )
        db.add(agent)
        await db.flush()
        with pytest.raises(RuntimeResolutionError):
            await resolve_runtime(db, tenant_id=tenant, agent=agent)
