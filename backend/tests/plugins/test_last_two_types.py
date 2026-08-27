"""The final two inert plugin types: `model_adapter` and `core_extension`.

`model_adapter` follows the connector/runtime pattern: contribute an adapter,
resolve it only for a tenant that enabled the plugin.

`core_extension` is the one whose blocker went stale. Hook registration was
restricted to `community` trust because resolving a trusted plugin's Python
callable "requires entry-point loading, a later sub-project". That loader now
exists, so trusted plugins get in-process handlers. The community half stays
blocked on the sandbox worker, which is still a stub -- that is §8.5, not this.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.capas.lifecycle import disable_plugin, enable_plugin
from oc8.capas.service import install_plugin
from oc8.config import get_settings
from oc8.hooks.executor import InProcessExecutor
from oc8.hooks.registry import get_hook_registry
from oc8.hooks.types import HookCtx
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(FIXTURES))
    get_settings.cache_clear()
    loader.reset_for_tests()
    contributions.reset_for_tests()
    yield
    loader.reset_for_tests()
    contributions.reset_for_tests()
    get_settings.cache_clear()


async def _enable(
    session: AppSessionFactory, tenant: uuid.UUID, name: str, *, enable: bool = True
) -> uuid.UUID:
    found = find_plugin(name)
    assert found is not None and found.manifest is not None, f"fixture {name} not discovered"
    async with session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=found.manifest)
        if enable:
            await enable_plugin(
                db,
                tenant_id=tenant,
                capa_id=version.capa_id,
                granted_permissions=list(version.permissions),
            )
        return version.capa_id


# ============================================================ model_adapter


async def test_a_plugin_provider_is_usable_by_the_enabling_tenant(
    app_session: AppSessionFactory,
) -> None:
    from oc8.modelrouter.registry import resolve_provider

    tenant = uuid.uuid4()
    await _enable(app_session, tenant, "demo_provider")
    async with app_session(tenant) as db:
        entry = await resolve_provider(db, tenant_id=tenant, name="demo-llm")
    assert entry is not None
    assert entry.canonical == "demo-llm"


async def test_a_plugin_provider_alias_resolves(app_session: AppSessionFactory) -> None:
    from oc8.modelrouter.registry import resolve_provider

    tenant = uuid.uuid4()
    await _enable(app_session, tenant, "demo_provider")
    async with app_session(tenant) as db:
        entry = await resolve_provider(db, tenant_id=tenant, name="demo")
    assert entry is not None and entry.canonical == "demo-llm"


async def test_another_tenant_cannot_use_the_plugin_provider(
    app_session: AppSessionFactory,
) -> None:
    """THE test: tenant A enabling it imports the module process-wide; tenant B
    must still not be able to reach it."""
    from oc8.modelrouter.registry import resolve_provider

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _enable(app_session, tenant_a, "demo_provider")
    async with app_session(tenant_a) as db:
        assert await resolve_provider(db, tenant_id=tenant_a, name="demo-llm") is not None
    async with app_session(tenant_b) as db:
        assert await resolve_provider(db, tenant_id=tenant_b, name="demo-llm") is None


async def test_installed_but_not_enabled_provider_does_not_resolve(
    app_session: AppSessionFactory,
) -> None:
    from oc8.modelrouter.registry import resolve_provider

    tenant = uuid.uuid4()
    await _enable(app_session, tenant, "demo_provider", enable=False)
    async with app_session(tenant) as db:
        assert await resolve_provider(db, tenant_id=tenant, name="demo-llm") is None


async def test_built_in_providers_resolve_for_everyone(
    app_session: AppSessionFactory,
) -> None:
    from oc8.modelrouter.registry import resolve_provider

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        for name in ("anthropic", "openai", "ollama", "claude"):
            assert await resolve_provider(db, tenant_id=tenant, name=name) is not None


async def test_configuring_a_model_with_an_unowned_plugin_provider_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    """The write gate is what matters: a tenant must not be able to point a
    ModelConfig at a provider it never enabled."""
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _enable(app_session, tenant_a, "demo_provider")

    def h(t: uuid.UUID) -> dict[str, str]:
        return {
            "Authorization": "Bearer "
            + get_identity_provider().mint(tenant_id=t, subject="op", role="org_admin")
        }

    body = {"provider": "demo-llm", "model": "demo-1", "locality": "cloud"}
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            ok = await c.post("/api/v1/models", json=body, headers=h(tenant_a))
            assert ok.status_code == 201, ok.text
            denied = await c.post("/api/v1/models", json=body, headers=h(tenant_b))
            assert denied.status_code == 422, denied.text


# ============================================================ core_extension


async def test_a_trusted_plugin_registers_an_in_process_hook(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    plugin_id = await _enable(app_session, tenant, "demo_hook")
    handlers = get_hook_registry(tenant).handlers_for("task.before_create")
    mine = [h for h in handlers if h.plugin_id == str(plugin_id)]
    assert len(mine) == 1
    assert mine[0].fn is not None, "a trusted handler must carry a real callable"
    assert isinstance(mine[0].executor, InProcessExecutor)
    assert mine[0].priority == 10


async def test_the_handler_actually_runs(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    await _enable(app_session, tenant, "demo_hook")
    handler = get_hook_registry(tenant).handlers_for("task.before_create")[0]
    ctx = cast("HookCtx", None)  # the demo handler ignores ctx
    result = await InProcessExecutor().run_filter(handler, ctx, {"title": "Ship it"})
    assert result["title"] == "[demo] Ship it"


async def test_a_point_not_declared_in_the_manifest_is_not_registered(
    app_session: AppSessionFactory,
) -> None:
    """The fixture also offers a handler for `run.before_start`, which its
    manifest never declares. Declared AND contributed, or it does not run --
    otherwise a plugin could hook points its consent screen never showed."""
    tenant = uuid.uuid4()
    await _enable(app_session, tenant, "demo_hook")
    assert get_hook_registry(tenant).handlers_for("run.before_start") == []


async def test_hooks_are_scoped_to_the_enabling_tenant(
    app_session: AppSessionFactory,
) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _enable(app_session, tenant_a, "demo_hook")
    assert get_hook_registry(tenant_a).handlers_for("task.before_create")
    assert get_hook_registry(tenant_b).handlers_for("task.before_create") == []


async def test_disabling_unregisters_the_handler(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    plugin_id = await _enable(app_session, tenant, "demo_hook")
    assert get_hook_registry(tenant).handlers_for("task.before_create")
    async with app_session(tenant) as db:
        await disable_plugin(db, tenant_id=tenant, capa_id=plugin_id, reason="test")
    assert get_hook_registry(tenant).handlers_for("task.before_create") == []


async def test_an_untrusted_plugin_never_gets_an_in_process_handler(
    app_session: AppSessionFactory,
) -> None:
    """Community plugins must not be run in-process. They still take the
    sandboxed path, whose worker is a stub -- so they register, but with no
    callable of ours."""
    found = find_plugin("demo_hook")
    assert found is not None and found.manifest is not None
    manifest: dict[str, Any] = dict(found.manifest)
    manifest["trust"] = "community"
    manifest["name"] = "demo_hook_community"

    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        version = await install_plugin(db, tenant_id=tenant, manifest_data=manifest)
        await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=version.capa_id,
            granted_permissions=list(version.permissions),
        )
    handlers = get_hook_registry(tenant).handlers_for("task.before_create")
    assert handlers, "a community plugin still registers, sandboxed"
    assert all(h.fn is None for h in handlers), "community code must never run in-process"
    assert all(not isinstance(h.executor, InProcessExecutor) for h in handlers)
