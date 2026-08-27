"""GET /agents/{id}/workspace/files (+ its /files/{path} sibling): the agent
"Files" tab's backend (§ ad-hoc, see docs/superpowers/plans -- no spec number
yet, this is a straight frontend-gap fill). Covers the three things that
matter: an agent whose runtime never writes a workspace answers `applicable:
false`; one that does gets its most recent run's files listed and read; and a
path-traversal attempt on the file-content route is rejected rather than
walking out of the workspace root."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.capas.lifecycle import enable_plugin
from oc8.capas.service import install_plugin
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


def _h(tenant: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant)}"}


async def _install_opencode_runtime(db: AsyncSession, tenant: uuid.UUID) -> uuid.UUID:
    # Unique semver suffix per call -- ACME_TENANT_ID is shared tenant-wide
    # across the suite (see the identical note in test_agents_runtime.py).
    version = await install_plugin(
        db,
        tenant_id=tenant,
        manifest_data={
            "name": "opencode_runtime",
            "version": f"1.0.0+{uuid.uuid4().hex[:8]}",
            "type": "runtime_adapter",
            "trust": "first_party",
            "capabilities": ["skills"],
        },
    )
    await enable_plugin(db, tenant_id=tenant, capa_id=version.capa_id, granted_permissions=[])
    return version.capa_id


async def test_not_applicable_for_agent_without_a_workspace_runtime(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent = m.Agent(tenant_id=tenant, department_id=uuid.uuid4(), name="A")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get(f"/api/v1/agents/{agent_id}/workspace/files", headers=_h(tenant))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["applicable"] is False
            assert body["files"] == []


async def test_lists_and_reads_the_most_recent_runs_workspace_files(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        plugin_id = await _install_opencode_runtime(db, tenant)
        agent = m.Agent(
            tenant_id=tenant, department_id=uuid.uuid4(), name="A", runtime_ref=str(plugin_id)
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id

        older_run = m.AgentRun(tenant_id=tenant, agent_id=agent_id, state="done")
        db.add(older_run)
        await db.flush()

        newer_run = m.AgentRun(tenant_id=tenant, agent_id=agent_id, state="done")
        db.add(newer_run)
        await db.flush()
        newer_run_id = newer_run.id

    # The older run's directory would prove the "most recent" ordering wrong
    # if the endpoint picked it by mistake -- created deliberately, never read.
    (tmp_path / str(older_run.id)).mkdir()
    (tmp_path / str(older_run.id) / "stale.txt").write_text("should not be listed")

    workspace = tmp_path / str(newer_run_id)
    (workspace / "nested").mkdir(parents=True)
    (workspace / "README.md").write_text("hello workspace")
    (workspace / "nested" / "deep.txt").write_text("deep file content")

    class _FakeSettings:
        runtime_session_root = str(tmp_path)

    monkeypatch.setattr("oc8.api.v1.run.get_settings", lambda: _FakeSettings())

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get(f"/api/v1/agents/{agent_id}/workspace/files", headers=_h(tenant))
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["applicable"] is True
            assert body["runId"] == str(newer_run_id)
            paths = {f["path"] for f in body["files"]}
            assert paths == {"README.md", "nested/deep.txt"}
            readme = next(f for f in body["files"] if f["path"] == "README.md")
            assert readme["size"] == len("hello workspace")

            content = await client.get(
                f"/api/v1/agents/{agent_id}/workspace/files/nested/deep.txt",
                headers=_h(tenant),
            )
            assert content.status_code == 200, content.text
            assert content.json()["content"] == "deep file content"
            assert content.json()["truncated"] is False

            missing = await client.get(
                f"/api/v1/agents/{agent_id}/workspace/files/does-not-exist.txt",
                headers=_h(tenant),
            )
            assert missing.status_code == 404


async def test_path_traversal_is_rejected(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        plugin_id = await _install_opencode_runtime(db, tenant)
        agent = m.Agent(
            tenant_id=tenant, department_id=uuid.uuid4(), name="A", runtime_ref=str(plugin_id)
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id

        run = m.AgentRun(tenant_id=tenant, agent_id=agent_id, state="done")
        db.add(run)
        await db.flush()
        run_id = run.id

    (tmp_path / str(run_id)).mkdir()
    # A real secret OUTSIDE the run's workspace directory -- if the traversal
    # gate has a hole, this is what a "../../secret.txt"-shaped request reads.
    (tmp_path / "secret.txt").write_text("do not leak me")

    class _FakeSettings:
        runtime_session_root = str(tmp_path)

    monkeypatch.setattr("oc8.api.v1.run.get_settings", lambda: _FakeSettings())

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get(
                f"/api/v1/agents/{agent_id}/workspace/files/../secret.txt",
                headers=_h(tenant),
            )
            assert r.status_code == 404, r.text
