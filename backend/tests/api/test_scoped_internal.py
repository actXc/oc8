from __future__ import annotations

import uuid

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory


def _plugin_token(tenant: uuid.UUID, scopes: list[str]) -> str:
    return get_identity_provider().mint(
        tenant_id=tenant, subject="plugin:p1", role="plugin", kind="plugin", scopes=scopes
    )


async def test_scoped_endpoint_enforced(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        task = m.Task(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            title="scoped",
            state="backlog",
        )
        db.add(task)
        await db.flush()
        task_id = str(task.id)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            ok = await client.get(
                f"/api/v1/capas/internal/tasks/{task_id}",
                headers={"Authorization": f"Bearer {_plugin_token(tenant, ['api:tasks.read'])}"},
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["id"] == task_id
            assert body["title"] == "scoped"
            assert body["status"] == "backlog"

            denied = await client.get(
                f"/api/v1/capas/internal/tasks/{task_id}",
                headers={"Authorization": f"Bearer {_plugin_token(tenant, [])}"},
            )
            assert denied.status_code == 403


async def test_scoped_endpoint_non_plugin_principal_bypasses_scope_check(
    app_session: AppSessionFactory,
) -> None:
    """A non-plugin principal (e.g. operator) isn't subject to scope gating."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        task = m.Task(
            tenant_id=tenant,
            department_id=uuid.uuid4(),
            title="operator visible",
            state="backlog",
        )
        db.add(task)
        await db.flush()
        task_id = str(task.id)

    operator_token = get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get(
                f"/api/v1/capas/internal/tasks/{task_id}",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
            assert r.status_code == 200, r.text
