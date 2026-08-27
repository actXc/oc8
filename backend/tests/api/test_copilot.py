from __future__ import annotations

import base64
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


async def test_copilot_rejects_secret_input_without_echoing_it() -> None:
    """Changing request validation to expose pydantic input would leak secrets."""
    secret = "SENTINEL-DO-NOT-ECHO"
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    token = get_identity_provider().mint(tenant_id=tenant, subject="admin", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(
                "/api/v1/copilot/proposals",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "operations": [
                        {
                            "type": "integration.prepare",
                            "integrationId": str(uuid.uuid4()),
                            "secret": secret,
                        }
                    ]
                },
            )
    assert response.status_code == 422
    assert secret not in response.text


async def test_copilot_proposal_creation_requires_manage_permission() -> None:
    """Removing the route gate would let an ordinary member stage configuration."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    token = get_identity_provider().mint(tenant_id=tenant, subject="member", role="member")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(
                "/api/v1/copilot/proposals",
                headers={"Authorization": f"Bearer {token}"},
                json={"operations": []},
            )
    assert response.status_code == 403


async def test_copilot_chat_returns_only_safe_reply_shape_and_never_echoes_bad_input() -> None:
    """Returning parser details would echo an operator-submitted credential."""
    secret = "SENTINEL-CHAT-INPUT"
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    token = get_identity_provider().mint(tenant_id=tenant, subject="admin", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(
                "/api/v1/copilot/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": {"secret": secret}},
            )
    assert response.status_code == 422
    assert secret not in response.text


async def test_copilot_reject_transitions_draft_without_applying(
    app_session: AppSessionFactory, acme_tenant: uuid.UUID
) -> None:
    """Replacing rejection with apply would execute the submitted mission change."""
    from oc8.auth import Principal
    from oc8.copilot.proposals import create_proposal

    agent_id: uuid.UUID
    async with app_session(acme_tenant) as db:
        agent = m.Agent(
            tenant_id=acme_tenant,
            department_id=uuid.uuid4(),
            name="Reject target",
            mission="unchanged",
        )
        db.add(agent)
        await db.flush()
        proposal = await create_proposal(
            db,
            Principal(subject="admin", tenant_id=acme_tenant, role="org_admin"),
            [{"type": "agent.mission.set", "agentId": str(agent.id), "mission": "new"}],
        )
        proposal_id = proposal.id
        agent_id = agent.id

    token = get_identity_provider().mint(tenant_id=acme_tenant, subject="admin", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(
                f"/api/v1/copilot/proposals/{proposal_id}/reject",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "rejected"

    async with app_session(acme_tenant) as db:
        reloaded = await db.get(m.Agent, agent_id)
        assert reloaded is not None
        assert reloaded.mission == "unchanged"


async def test_copilot_apply_cannot_schedule_a_subscription_backed_agent(
    app_session: AppSessionFactory, acme_tenant: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Copilot is a FOURTH way a trigger gets created, and it must obey the
    same manual-only rule as `POST /agents/{id}/triggers`.

    `trigger.create` used to call `oc8.triggers.service.create_trigger`
    directly, which was unguarded -- so an applied proposal could put a cron
    schedule on an agent backed by a personal ChatGPT subscription, the exact
    thing the whole feature exists to prevent. The guard now lives inside
    `create_trigger` itself, so this path is covered by construction.
    """
    from oc8 import config
    from oc8.auth import Principal
    from oc8.copilot.proposals import create_proposal
    from oc8.credentials.service import create_credential

    monkeypatch.setattr(
        config.get_settings(),
        "secret_kek",
        base64.b64encode(bytes(range(32))).decode(),
        raising=False,
    )

    async with app_session(acme_tenant) as db:
        cred = await create_credential(
            db,
            tenant_id=acme_tenant,
            name=f"cg-{uuid.uuid4().hex[:8]}",
            credential_type="openai_chatgpt_subscription",
            field_values={"oauth_connection_id": str(uuid.uuid4())},
        )
        mc = m.ModelConfig(
            tenant_id=acme_tenant,
            provider="openai_chatgpt",
            model="gpt-5",
            credential_id=cred.id,
        )
        db.add(mc)
        await db.flush()
        agent = m.Agent(
            tenant_id=acme_tenant,
            department_id=uuid.uuid4(),
            name="Copilot trigger target",
            model_config_id=mc.id,
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id
        proposal = await create_proposal(
            db,
            Principal(subject="admin", tenant_id=acme_tenant, role="org_admin"),
            [
                {
                    "type": "trigger.create",
                    "agentId": str(agent_id),
                    "kind": "cron",
                    "taskText": "nightly sweep",
                    "cronExpression": "0 * * * *",
                }
            ],
        )
        proposal_id = proposal.id

    token = get_identity_provider().mint(tenant_id=acme_tenant, subject="admin", role="org_admin")
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            response = await client.post(
                f"/api/v1/copilot/proposals/{proposal_id}/apply",
                headers={"Authorization": f"Bearer {token}"},
            )
    # The Copilot's own rejection convention: InvalidOperation -> rejected
    # proposal -> 409, value-free.
    assert response.status_code == 409, response.text

    # And the trigger must not exist -- the flushed row is rolled back with the
    # rest of the failed request.
    async with app_session(acme_tenant) as db:
        from oc8.triggers.service import list_triggers_for_agent

        assert await list_triggers_for_agent(db, agent_id=agent_id) == []
