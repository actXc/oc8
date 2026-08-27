from __future__ import annotations

import uuid

import pytest

from oc8.capas.lifecycle import disable_plugin, enable_plugin
from oc8.capas.registry import (
    CORE_CAPABILITIES,
    PluginCapabilityRegistry,
    provided_capabilities,
)
from oc8.capas.service import DependencyError, install_plugin
from oc8.constants import ACME_TENANT_ID, GLOBEX_TENANT_ID
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def test_core_capabilities_present() -> None:
    reg = PluginCapabilityRegistry()
    for cap in CORE_CAPABILITIES:
        assert reg.has(cap)


def test_plugin_provided_capabilities() -> None:
    assert PluginCapabilityRegistry().missing(["crm"]) == ["crm"]
    assert PluginCapabilityRegistry(["crm"]).missing(["crm"]) == []


def test_a_versioned_capability_satisfies_a_bare_dependency() -> None:
    """`integration:odoo` provides `integration`, which is what `depends` names."""
    assert PluginCapabilityRegistry(["integration:odoo"]).missing(["integration"]) == []


def test_supports_is_fail_closed() -> None:
    reg = PluginCapabilityRegistry()
    # runtime declares checkpoints but not skills -> "skills" unmet
    unmet = reg.supports(["checkpoints", "concurrent_tasks:3"], ["checkpoints", "skills"])
    assert unmet == ["skills"]


async def _install(db: object, tenant: uuid.UUID, name: str, **extra: object) -> object:
    return await install_plugin(
        db,  # type: ignore[arg-type]
        tenant_id=tenant,
        manifest_data={"name": name, "version": "1.0.0", "type": "core_extension", **extra},
    )


async def test_one_tenants_provider_does_not_satisfy_anothers_dependency(
    app_session: AppSessionFactory,
) -> None:
    """Same defect class as the hook-registry cross-tenant bug: a capability one
    tenant provides must never satisfy another tenant's `depends`.

    This used to rest on the registry being keyed per tenant in memory. The
    database is the authority now, so what enforces it is RLS on the query --
    which is stronger, because it cannot be bypassed by a process restart or by
    a code path that forgets to pass a tenant id.
    """
    tenant_a = uuid.UUID(str(ACME_TENANT_ID))
    tenant_b = uuid.UUID(str(GLOBEX_TENANT_ID))
    cap = f"crm-isolation-{uuid.uuid4().hex[:8]}"

    async with app_session(tenant_a) as db:
        provider = await _install(db, tenant_a, f"acme.provider-{cap}", capabilities=[cap])
        await enable_plugin(
            db, tenant_id=tenant_a, capa_id=provider.capa_id, granted_permissions=[]
        )
        await db.commit()

    async with app_session(tenant_b) as db:
        with pytest.raises(DependencyError):
            await _install(db, tenant_b, f"globex.needs-{cap}", depends=[cap])

    async with app_session(tenant_a) as db:
        version = await _install(db, tenant_a, f"acme.needs-{cap}", depends=[cap])
        assert version.semver == "1.0.0"


async def test_a_provider_enabled_in_the_database_survives_a_restart(
    app_session: AppSessionFactory,
) -> None:
    """Live, 2026-07-27: odoo_mcp was enabled in the database, the control plane
    had since been restarted, and installing a plugin that depends on
    `integration:odoo` failed with "unmet capabilities" -- for a provider sitting
    right there, enabled. The registry that answered the question lived in the
    process; the installations it described did not. Nothing to clear here any
    more: a fresh process reads the same rows.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        provider = await _install(db, tenant, "acme.odoo-bridge", capabilities=["integration:odoo"])
        await enable_plugin(db, tenant_id=tenant, capa_id=provider.capa_id, granted_permissions=[])
        await db.commit()

    async with app_session(tenant) as db:
        version = await _install(db, tenant, "acme.support-dept", depends=["integration:odoo"])
        assert version.semver == "1.0.0"


async def test_a_capability_only_declared_as_provides_counts_too(
    app_session: AppSessionFactory,
) -> None:
    """A manifest can announce a capability two ways: the `capabilities` list
    (which is also a column) and `provides` (which lives only inside the manifest
    JSON). The old in-memory path registered BOTH, so reading only the column
    would have quietly narrowed what satisfies a dependency.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        provider = await _install(db, tenant, "acme.provides-only", provides=["billing-export"])
        await enable_plugin(db, tenant_id=tenant, capa_id=provider.capa_id, granted_permissions=[])
        await db.commit()

    async with app_session(tenant) as db:
        assert "billing-export" in await provided_capabilities(db)
        version = await _install(db, tenant, "acme.needs-billing", depends=["billing-export"])
        assert version.semver == "1.0.0"


async def test_a_disabled_provider_stops_satisfying_a_dependency(
    app_session: AppSessionFactory,
) -> None:
    """The half that keeps the check honest: disabling the provider must take the
    grant away again, or "enabled" would mean nothing."""
    tenant = uuid.uuid4()
    cap = f"reporting-{uuid.uuid4().hex[:8]}"
    async with app_session(tenant) as db:
        provider = await _install(db, tenant, f"acme.reporting-{cap}", capabilities=[cap])
        await enable_plugin(db, tenant_id=tenant, capa_id=provider.capa_id, granted_permissions=[])
        plugin_id = provider.capa_id
        await db.commit()

    # A fresh session: the RLS GUC is transaction-local, so continuing to use one
    # after a commit runs unbound and every lookup misses.
    async with app_session(tenant) as db:
        await disable_plugin(db, tenant_id=tenant, capa_id=plugin_id)
        await db.commit()

    async with app_session(tenant) as db:
        with pytest.raises(DependencyError):
            await _install(db, tenant, f"acme.needs-{cap}", depends=[cap])


async def test_a_capability_no_one_provides_is_still_refused(
    app_session: AppSessionFactory,
) -> None:
    """Reading the database must not turn the check into a rubber stamp."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(DependencyError):
            await _install(db, tenant, "acme.needs-nothing-real", depends=["integration:nope"])
