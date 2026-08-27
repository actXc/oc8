from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from oc8.capas.lifecycle import (
    CIRCUIT_BREAKER_THRESHOLD,
    ConsentError,
    QuarantinedError,
    disable_plugin,
    enable_plugin,
    record_failure,
)
from oc8.capas.registry import enabled_capability_registry
from oc8.capas.service import PluginError, install_plugin
from oc8.constants import ACME_TENANT_ID
from oc8.hooks.points import register_core_points
from oc8.hooks.registry import get_hook_registry
from oc8.models import CapaInstallation
from tests.conftest import AppSessionFactory


async def _install(db, tenant: uuid.UUID, perms: list[str], name: str = "acme.hooks-lc"):
    v = await install_plugin(
        db,
        tenant_id=tenant,
        manifest_data={
            "name": name,
            "version": "1.0.0",
            "type": "core_extension",
            "trust": "community",
            "permissions": perms,
            "provides": ["crm"],
        },
    )
    return v


async def test_consent_required(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        v = await _install(db, tenant, ["hooks:task.before_create"])
        with pytest.raises(ConsentError):
            await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
        inst = await enable_plugin(
            db,
            tenant_id=tenant,
            capa_id=v.capa_id,
            granted_permissions=["hooks:task.before_create"],
        )
        assert inst.status == "enabled"


async def test_circuit_breaker(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        v = await _install(db, tenant, [], name="acme.breaker")
        await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
        inst = None
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            inst = await record_failure(db, tenant_id=tenant, capa_id=v.capa_id)
        assert inst is not None and inst.status == "quarantined"


async def test_disable_plugin(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        v = await _install(db, tenant, [], name="acme.disable")
        await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
        inst = await disable_plugin(
            db, tenant_id=tenant, capa_id=v.capa_id, reason="testing disable"
        )
        assert inst.status == "disabled"
        assert inst.disabled_reason == "testing disable"


async def test_disable_plugin_nonexistent_id_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        missing_id = uuid.uuid4()
        before = (await db.execute(select(func.count()).select_from(CapaInstallation))).scalar_one()
        with pytest.raises(PluginError):
            await disable_plugin(db, tenant_id=tenant, capa_id=missing_id, reason="nope")
        after = (await db.execute(select(func.count()).select_from(CapaInstallation))).scalar_one()
        assert after == before


async def test_record_failure_nonexistent_id_raises(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        missing_id = uuid.uuid4()
        before = (await db.execute(select(func.count()).select_from(CapaInstallation))).scalar_one()
        with pytest.raises(PluginError):
            await record_failure(db, tenant_id=tenant, capa_id=missing_id)
        after = (await db.execute(select(func.count()).select_from(CapaInstallation))).scalar_one()
        assert after == before


async def test_enable_community_plugin_registers_sandboxed_hook_handler(
    app_session: AppSessionFactory,
) -> None:
    """Step 7 narrowing: community (sandboxed) `handles` bindings are
    auto-registered into the hook registry on enable, using a
    SandboxedExecutor -- never an in-process fn."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    register_core_points(get_hook_registry(tenant))
    async with app_session(tenant) as db:
        v = await install_plugin(
            db,
            tenant_id=tenant,
            manifest_data={
                "name": "acme.hooks-community",
                "version": "1.0.0",
                "type": "core_extension",
                "trust": "community",
                "permissions": [],
                "handles": [{"point": "task.before_create", "priority": 10}],
            },
        )
        try:
            await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
            matches = [
                h
                for h in get_hook_registry(tenant).handlers_for("task.before_create")
                if h.plugin_id == str(v.capa_id)
            ]
            assert len(matches) == 1
            assert matches[0].priority == 10
            assert matches[0].fn is None  # sandboxed handlers carry no in-process fn
            assert matches[0].executor is not None

            await disable_plugin(db, tenant_id=tenant, capa_id=v.capa_id)
            remaining = [
                h
                for h in get_hook_registry(tenant).handlers_for("task.before_create")
                if h.plugin_id == str(v.capa_id)
            ]
            assert remaining == []
        finally:
            get_hook_registry(tenant).unregister_plugin(str(v.capa_id))


async def test_quarantine_unregisters_hook_handler(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    register_core_points(get_hook_registry(tenant))
    async with app_session(tenant) as db:
        v = await install_plugin(
            db,
            tenant_id=tenant,
            manifest_data={
                "name": "acme.hooks-quarantine",
                "version": "1.0.0",
                "type": "core_extension",
                "trust": "community",
                "permissions": [],
                "handles": [{"point": "task.before_create"}],
            },
        )
        try:
            await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
            assert any(
                h.plugin_id == str(v.capa_id)
                for h in get_hook_registry(tenant).handlers_for("task.before_create")
            )
            inst = None
            for _ in range(CIRCUIT_BREAKER_THRESHOLD):
                inst = await record_failure(db, tenant_id=tenant, capa_id=v.capa_id)
            assert inst is not None and inst.status == "quarantined"
            assert all(
                h.plugin_id != str(v.capa_id)
                for h in get_hook_registry(tenant).handlers_for("task.before_create")
            )
        finally:
            get_hook_registry(tenant).unregister_plugin(str(v.capa_id))


async def test_enable_first_party_plugin_does_not_auto_register_hook(
    app_session: AppSessionFactory,
) -> None:
    """Step 7 narrowing: in-process (first_party/verified) handler
    registration stays test-driven until entry-point loading exists."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    register_core_points(get_hook_registry(tenant))
    async with app_session(tenant) as db:
        v = await install_plugin(
            db,
            tenant_id=tenant,
            manifest_data={
                "name": "acme.hooks-first-party",
                "version": "1.0.0",
                "type": "core_extension",
                "trust": "first_party",
                "permissions": [],
                "handles": [{"point": "task.before_create"}],
            },
        )
        try:
            await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
            assert all(
                h.plugin_id != str(v.capa_id)
                for h in get_hook_registry(tenant).handlers_for("task.before_create")
            )
        finally:
            get_hook_registry(tenant).unregister_plugin(str(v.capa_id))


async def test_enable_plugin_is_idempotent(app_session: AppSessionFactory) -> None:
    """Regression: enable_plugin used to unconditionally append a hook
    handler and increment the capability refcount on every call, so calling
    the enable endpoint twice on an already-enabled installation (e.g.
    re-consent after a permission change) would register a duplicate
    HookHandler (firing twice per dispatch) and double-increment the
    capability refcount (so a single subsequent disable would under-decrement
    it, leaving the capability falsely "still provided"). Calling
    enable_plugin N times must leave exactly one hook registration and one
    capability grant, matching calling it once."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    register_core_points(get_hook_registry(tenant))
    async with app_session(tenant) as db:
        v = await install_plugin(
            db,
            tenant_id=tenant,
            manifest_data={
                "name": "acme.hooks-idempotent",
                "version": "1.0.0",
                "type": "core_extension",
                "trust": "community",
                "permissions": [],
                "provides": ["idempotent-cap"],
                "handles": [{"point": "task.before_create", "priority": 10}],
            },
        )
        try:
            await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
            inst = await enable_plugin(
                db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[]
            )
            assert inst.status == "enabled"

            matches = [
                h
                for h in get_hook_registry(tenant).handlers_for("task.before_create")
                if h.plugin_id == str(v.capa_id)
            ]
            assert len(matches) == 1

            # Enabled twice, so a mechanism that counted registrations would
            # need two disables to let go. Capabilities are read from the
            # installation row per check, so one disable is the whole story --
            # asserted through what actually consumes them.
            assert (await enabled_capability_registry(db)).has("idempotent-cap")
            await disable_plugin(db, tenant_id=tenant, capa_id=v.capa_id)
            assert not (await enabled_capability_registry(db)).has("idempotent-cap")
        finally:
            get_hook_registry(tenant).unregister_plugin(str(v.capa_id))


async def test_quarantined_plugin_cannot_be_silently_reenabled(
    app_session: AppSessionFactory,
) -> None:
    """Regression: enable_plugin used to unconditionally reset failure_count
    to 0, so any caller could un-quarantine a plugin the circuit breaker just
    quarantined simply by calling enable again with valid consent -- silently
    bypassing the breaker."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        v = await _install(db, tenant, [], name="acme.quarantine-guard")
        await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])
        inst = None
        for _ in range(CIRCUIT_BREAKER_THRESHOLD):
            inst = await record_failure(db, tenant_id=tenant, capa_id=v.capa_id)
        assert inst is not None and inst.status == "quarantined"

        with pytest.raises(QuarantinedError):
            await enable_plugin(db, tenant_id=tenant, capa_id=v.capa_id, granted_permissions=[])

        # Still quarantined -- the failed enable attempt must not have
        # touched the installation's state.
        after = (
            await db.execute(select(CapaInstallation).where(CapaInstallation.capa_id == v.capa_id))
        ).scalar_one()
        assert after.status == "quarantined"
