from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID, role: str = "org_admin") -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)


async def _make_agent(
    app_session: AppSessionFactory, tenant: uuid.UUID, mission: str = ""
) -> uuid.UUID:
    async with app_session(tenant) as db:
        department = m.Department(tenant_id=tenant, name=f"D-{uuid.uuid4().hex}", frame={})
        db.add(department)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=department.id,
            name="Lennart",
            mission=mission,
        )
        db.add(agent)
        await db.flush()
        return agent.id


async def test_admin_can_edit_instructions_and_it_persists(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant, mission="Old instructions")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            updated = await client.patch(
                f"/api/v1/agents/{agent_id}/instructions",
                json={"instructions": "New instructions"},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["mission"] == "New instructions"

            fetched = await client.get(f"/api/v1/agents/{agent_id}", headers=headers)
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["mission"] == "New instructions"


async def test_non_admin_cannot_edit_instructions(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.patch(
                f"/api/v1/agents/{agent_id}/instructions",
                json={"instructions": "Should not land"},
                headers={"Authorization": f"Bearer {_token(tenant, 'member')}"},
            )
            assert response.status_code == 403, response.text


async def test_instruction_edits_are_recorded_as_history_newest_first(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant, mission="v1")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            for text in ("v2", "v3"):
                r = await client.patch(
                    f"/api/v1/agents/{agent_id}/instructions",
                    json={"instructions": text},
                    headers=headers,
                )
                assert r.status_code == 200, r.text

            history = await client.get(
                f"/api/v1/agents/{agent_id}/instructions/history", headers=headers
            )
            assert history.status_code == 200, history.text
            revisions = history.json()["revisions"]
            assert len(revisions) == 2
            # Newest first.
            assert revisions[0]["before"] == "v2"
            assert revisions[0]["after"] == "v3"
            assert revisions[1]["before"] == "v1"
            assert revisions[1]["after"] == "v2"
            assert revisions[0]["by"] == "u"
            body = history.json()
            assert body["totalCount"] == 2
            assert body["nextBeforeSeq"] is None


async def test_instruction_history_paginates_with_a_before_seq_cursor(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = await _make_agent(app_session, tenant, mission="v1")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            for text in ("v2", "v3", "v4"):
                r = await client.patch(
                    f"/api/v1/agents/{agent_id}/instructions",
                    json={"instructions": text},
                    headers=headers,
                )
                assert r.status_code == 200, r.text

            first = await client.get(
                f"/api/v1/agents/{agent_id}/instructions/history",
                params={"limit": 2},
                headers=headers,
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert len(first_body["revisions"]) == 2
            assert first_body["totalCount"] == 3
            assert first_body["revisions"][0]["after"] == "v4"
            assert first_body["revisions"][1]["after"] == "v3"
            cursor = first_body["nextBeforeSeq"]
            assert cursor is not None

            second = await client.get(
                f"/api/v1/agents/{agent_id}/instructions/history",
                params={"limit": 2, "beforeSeq": cursor},
                headers=headers,
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert len(second_body["revisions"]) == 1
            assert second_body["revisions"][0]["after"] == "v2"
            assert second_body["nextBeforeSeq"] is None
            assert second_body["totalCount"] == 3


async def test_instruction_history_is_scoped_to_its_own_agent(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_a = await _make_agent(app_session, tenant, mission="a1")
    agent_b = await _make_agent(app_session, tenant, mission="b1")

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            for agent_id, text in ((agent_a, "a2"), (agent_b, "b2")):
                r = await client.patch(
                    f"/api/v1/agents/{agent_id}/instructions",
                    json={"instructions": text},
                    headers=headers,
                )
                assert r.status_code == 200, r.text

            history_a = await client.get(
                f"/api/v1/agents/{agent_a}/instructions/history", headers=headers
            )
            revisions_a = history_a.json()["revisions"]
            assert len(revisions_a) == 1
            assert revisions_a[0]["after"] == "a2"
