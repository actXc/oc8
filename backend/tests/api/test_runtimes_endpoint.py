"""`GET /runtimes`: the read side of agent-runtime selection (§8.7), task 4
of the agent-runtime-selection slice.

Consumed by frontend task 6's `useRuntimes()`. Every assertion here checks a
SPECIFIC id's presence/absence/shape rather than the list's length or
membership as a whole -- ACME_TENANT_ID is shared across the whole suite with
no per-test rollback (see tests/conftest.py), so other tests' plugins may be
sitting in the same tenant's rows by the time this one runs.
"""

from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.api.v1 import runtimes as runtimes_module
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_VIEWER
from oc8.capas.lifecycle import disable_plugin, enable_plugin
from oc8.capas.service import install_plugin
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, *, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


def _h(tenant: uuid.UUID, *, role: str = "org_admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant, role=role)}"}


async def _install_runtime(
    db: AsyncSession,
    tenant: uuid.UUID,
    *,
    name: str,
    enabled: bool,
) -> uuid.UUID:
    # Unique semver suffix per call -- ACME_TENANT_ID is shared across the
    # whole suite; see the identical note in tests/api/test_agents_runtime.py.
    version = await install_plugin(
        db,
        tenant_id=tenant,
        manifest_data={
            "name": name,
            "version": f"1.0.0+{uuid.uuid4().hex[:8]}",
            "type": "runtime_adapter",
            "trust": "community",
            "label": "Nice Label",
            "summary": "A nice runtime.",
            "capabilities": ["skills"],
        },
    )
    if enabled:
        await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])
    else:
        # disable_plugin lazily creates the installation row (status
        # "installed") and immediately flips it to "disabled" -- a plugin
        # that was never enabled at all.
        await disable_plugin(db, tenant_id=tenant, capa_id=version.capa_id)
    return version.capa_id


async def test_runtimes_always_has_exactly_two_builtins_with_one_default(
    app_session: AppSessionFactory,
) -> None:
    """Both built-ins (in-process and isolated) are always present and
    independently selectable now -- not one entry whose behavior is a hidden
    tenant-wide toggle. Exactly one of the two carries `isDefault=True`: the
    one an agent with no explicit `runtime_ref` actually gets (mirrors
    `agent_isolation`, default False -> in-process)."""
    tenant = uuid.uuid4()
    async with app_session(tenant):
        pass

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/v1/runtimes", headers=_h(tenant))
            assert resp.status_code == 200, resp.text
            body = resp.json()
            builtin_ids = {"builtin:in-process", "builtin:isolated"}
            builtins = [r for r in body if r["id"] in builtin_ids]
            assert {r["id"] for r in builtins} == builtin_ids
            assert all(r["available"] for r in builtins)

            defaults = [r for r in body if r["isDefault"]]
            assert len(defaults) == 1
            assert defaults[0]["id"] == "builtin:in-process"


async def test_runtimes_lists_enabled_plugin_for_owning_tenant_only(
    app_session: AppSessionFactory,
) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    async with app_session(tenant_a) as db:
        plugin_id = await _install_runtime(db, tenant_a, name="oc8.echo-runtime-stub", enabled=True)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            owner_resp = await client.get("/api/v1/runtimes", headers=_h(tenant_a))
            owner_ids = [r["id"] for r in owner_resp.json()]
            assert str(plugin_id) in owner_ids

            other_resp = await client.get("/api/v1/runtimes", headers=_h(tenant_b))
            other_ids = [r["id"] for r in other_resp.json()]
            assert str(plugin_id) not in other_ids


async def test_runtimes_excludes_disabled_plugin(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        plugin_id = await _install_runtime(db, tenant, name="oc8.echo-runtime-stub", enabled=False)

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/v1/runtimes", headers=_h(tenant))
            ids = [r["id"] for r in resp.json()]
            assert str(plugin_id) not in ids


async def test_runtimes_marks_unloadable_plugin_unavailable(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        # A name that is neither in the closed built-in map nor discoverable
        # on disk: install_plugin only writes DB rows, it never materialises
        # plugin code, so this is exactly what "installed a runtime and the
        # implementation isn't there" looks like.
        plugin_id = await _install_runtime(
            db, tenant, name="oc8.nonexistent-runtime-stub", enabled=True
        )

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.get("/api/v1/runtimes", headers=_h(tenant))
            entry = next(r for r in resp.json() if r["id"] == str(plugin_id))
            assert entry["available"] is False
            assert entry["unavailableReason"]


async def test_runtimes_requires_agent_view_permission(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant):
        pass

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            # "member" holds the empty permission set tenant-wide (§0.A): no
            # seat, no grant.
            resp = await client.get("/api/v1/runtimes", headers=_h(tenant, role="member"))
            assert resp.status_code == 403, resp.text


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


async def test_runtimes_reachable_by_a_seat_scoped_caller_with_no_tenant_wide_grant(
    app_session: AppSessionFactory,
) -> None:
    """`GET /runtimes` must gate the same way `GET /agents`/`GET /agents/{id}`
    do (`require_departmental(perm(AGENT, VIEW))`), not the tenant-wide-only
    `require_permission` -- a seat holder who can open the agent detail page
    must not 403 on the endpoint that page's runtime panel depends on."""
    tenant = uuid.uuid4()
    seated_subject = "sales-seat"
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb", frame={})
        db.add(sales)
        await db.flush()

        member = m.OrgMember(
            tenant_id=tenant,
            subject=seated_subject,
            subject_uuid=_subject_uuid(seated_subject),
        )
        db.add(member)
        await db.flush()
        db.add(
            m.OrgMemberDepartment(
                tenant_id=tenant,
                member_id=member.id,
                department_id=sales.id,
                seat_role=SEAT_VIEWER,
            )
        )
        await db.flush()

    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            # "member" role holds nothing tenant-wide (§0.A): this 200 is the
            # SEAT doing the work and nothing else.
            token = get_identity_provider().mint(
                tenant_id=tenant, subject=seated_subject, role="member"
            )
            resp = await client.get(
                "/api/v1/runtimes", headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 200, resp.text


async def test_builtin_entries_capabilities_and_default_flag_pinned_per_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The in-process and isolated built-ins do NOT share a capability list
    (see the module docstring / `BUILTIN_IN_PROCESS_CAPABILITIES` /
    `BUILTIN_ISOLATED_CAPABILITIES` in oc8.runtime.registry):
    `oc8.isolated_shell` never reaches `oc8.agent.engine.maybe_checkpoint`,
    so the isolated built-in must not claim "checkpoints". Both are ALWAYS
    present regardless of the setting; only which one carries
    `is_default=True` moves with it."""

    class _Settings:
        agent_isolation = False

    monkeypatch.setattr(runtimes_module, "get_settings", lambda: _Settings())
    entries = runtimes_module._builtin_entries()
    by_name = {e.name: e for e in entries}
    assert by_name["oc8.agent-runtime"].capabilities == ["checkpoints", "skills"]
    assert by_name["oc8.agent-runtime-isolated"].capabilities == ["skills"]
    assert by_name["oc8.agent-runtime"].is_default is True
    assert by_name["oc8.agent-runtime-isolated"].is_default is False

    class _IsolatedSettings:
        agent_isolation = True

    monkeypatch.setattr(runtimes_module, "get_settings", lambda: _IsolatedSettings())
    entries = runtimes_module._builtin_entries()
    by_name = {e.name: e for e in entries}
    assert by_name["oc8.agent-runtime"].is_default is False
    assert by_name["oc8.agent-runtime-isolated"].is_default is True
