from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import Principal
from oc8.copilot.proposals import create_proposal
from oc8.modelrouter.types import CompletionResult, Usage

pytestmark = pytest.mark.asyncio


async def test_secret_never_reaches_model_proposal_or_audit_on_refusal(app_session) -> None:
    """The Copilot must reject a credential before any model or durable boundary."""
    from oc8.copilot.chat import respond_to_copilot_message

    # A fresh tenant per test, not the shared `acme_tenant` fixture: this test's
    # assertions below (`proposals == []`) are only true for a tenant nobody else
    # has written to. `acme_tenant` is one fixed UUID shared -- and never rolled
    # back -- across the whole suite (see conftest.py's session-scoped Postgres
    # container), so any other test that successfully creates a CopilotProposal
    # under it (e.g. test_proposals.py's mission-proposal test) leaves rows that
    # leak into this query once it runs later in a full-suite ordering. A fresh
    # UUID needs no pre-existing Tenant row -- RLS scopes by the GUC, not a FK --
    # and guarantees this tenant has never been touched by anything else.
    tenant = uuid.uuid4()
    secret = "SENTINEL-COPILOT-SECRET-NEVER-PERSISTS"
    actor = Principal(subject="admin", tenant_id=tenant, role="org_admin")
    model_called = False

    async def complete(_request):
        nonlocal model_called
        model_called = True
        return CompletionResult("{}", [], Usage(), "stop", "ollama", "test")

    async with app_session(tenant) as db:
        reply = await respond_to_copilot_message(db, actor, f"apiKey={secret}", complete=complete)
        with pytest.raises(ValueError) as rejected:
            await create_proposal(
                db,
                actor,
                [{"type": "integration.prepare", "integrationId": "not-a-uuid", "secret": secret}],
            )
        events = (
            await db.execute(select(m.AuditEvent).where(m.AuditEvent.tenant_id == tenant))
        ).scalars().all()
        proposals = (
            await db.execute(
                select(m.CopilotProposal).where(m.CopilotProposal.tenant_id == tenant)
            )
        ).scalars().all()

    durable_values = [str(event.resource) for event in events] + [str(rejected.value)]
    assert model_called is False
    assert reply.proposal_id is None
    assert proposals == []
    assert all(secret not in value for value in durable_values)
