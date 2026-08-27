from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from oc8.auth import Principal

pytestmark = pytest.mark.asyncio


def _actor(tenant: uuid.UUID) -> Principal:
    return Principal(subject="operator-1", tenant_id=tenant, role="org_admin")


async def test_proposal_applies_once_and_redacts_secret_from_durable_state(
    app_session, acme_tenant
) -> None:
    """Removing the capability allowlist would persist a submitted secret."""
    from oc8.copilot.proposals import create_proposal

    secret = "SENTINEL-DO-NOT-PERSIST"
    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        with pytest.raises(ValueError) as rejected:
            await create_proposal(
                db,
                actor,
                [
                    {
                        "type": "integration.prepare",
                        "integrationId": str(uuid.uuid4()),
                        "secret": secret,
                    }
                ],
            )
        assert secret not in str(rejected.value)


async def test_mission_proposal_applies_once_and_expires_when_target_changes(
    app_session, acme_tenant
) -> None:
    """Removing the revision comparison would apply a superseded proposal."""
    from oc8.copilot.proposals import ProposalRejected, apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        agent = m.Agent(
            tenant_id=acme_tenant,
            department_id=uuid.uuid4(),
            name="Copilot target",
            mission="old",
        )
        db.add(agent)
        await db.flush()
        proposal = await create_proposal(
            db,
            actor,
            [{"type": "agent.mission.set", "agentId": str(agent.id), "mission": "new"}],
        )
        result = await apply_proposal(db, proposal.id, actor)
        assert result.status == "applied"
        assert agent.mission == "new"
        with pytest.raises(ProposalRejected):
            await apply_proposal(db, proposal.id, actor)

        stale = await create_proposal(
            db,
            actor,
            [{"type": "agent.mission.set", "agentId": str(agent.id), "mission": "stale"}],
        )
        agent.mission = "changed outside proposal"
        await db.flush()
        with pytest.raises(ProposalRejected):
            await apply_proposal(db, stale.id, actor)
        assert stale.status == "expired"
