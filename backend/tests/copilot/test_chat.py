from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.auth import Principal
from oc8.modelrouter.types import CompletionResult, Usage

pytestmark = pytest.mark.asyncio


async def _configured_model(db, tenant_id) -> None:
    db.add(
        m.ModelConfig(
            tenant_id=tenant_id,
            provider="ollama",
            model="configured-test-model",
            locality="local",
        )
    )
    await db.flush()


async def test_chat_serializes_connection_as_label_and_status_only(
    app_session, acme_tenant
) -> None:
    """Adding connection config to a model message would expose a credential."""
    from oc8.copilot.chat import respond_to_copilot_message

    secret = "SENTINEL-CREDENTIAL-MUST-NOT-REACH-MODEL"
    actor = Principal(subject="admin", tenant_id=acme_tenant, role="org_admin")
    captured = []

    async def complete(request):
        captured.append(request)
        return CompletionResult(
            text='{"text":"I can prepare a proposal.","operations":[]}',
            tool_calls=[],
            usage=Usage(),
            stop_reason="stop",
            provider="ollama",
            model="test",
        )

    async with app_session(acme_tenant) as db:
        await _configured_model(db, acme_tenant)
        db.add(
            m.McpConnection(
                tenant_id=acme_tenant,
                name="Payroll connector",
                server_url="https://private.example.test",
                connected=True,
                config={"apiKey": secret},
            )
        )
        await db.flush()
        reply = await respond_to_copilot_message(
            db, actor, "Prepare the payroll connection", complete=complete
        )

    assert reply.proposal_id is None
    wire = "\n".join(message.content for message in captured[0].messages)
    assert "Payroll connector" in wire
    assert '"connected": true' in wire
    assert secret not in wire
    assert "private.example.test" not in wire
    assert "payroll/api-key" not in wire


async def test_chat_creates_a_review_proposal_without_applying_it(app_session, acme_tenant) -> None:
    """Replacing proposal creation with an applier would mutate the agent mission."""
    from oc8.copilot.chat import respond_to_copilot_message

    actor = Principal(subject="admin", tenant_id=acme_tenant, role="org_admin")

    async with app_session(acme_tenant) as db:
        await _configured_model(db, acme_tenant)
        agent = m.Agent(
            tenant_id=acme_tenant,
            department_id=uuid.uuid4(),
            name="Research",
            mission="old mission",
        )
        db.add(agent)
        await db.flush()

        async def proposal_complete(_request):
            return CompletionResult(
                text=(
                    '{"text":"Review this mission.","missingFields":[],"operations":['
                    '{"type":"agent.mission.set","agentId":"'
                    + str(agent.id)
                    + '","mission":"new mission"}]}'
                ),
                tool_calls=[],
                usage=Usage(),
                stop_reason="stop",
                provider="ollama",
                model="test",
            )

        reply = await respond_to_copilot_message(
            db, actor, "Change the research mission", complete=proposal_complete
        )
        assert reply.proposal_id is not None
        assert agent.mission == "old mission"


async def test_chat_redacts_model_reply_before_transcript_storage(app_session, acme_tenant) -> None:
    """Persisting raw model text would turn an upstream credential leak into durable data."""
    from oc8.copilot.chat import respond_to_copilot_message

    secret = "SENTINEL-MODEL-LEAK"
    actor = Principal(subject="admin", tenant_id=acme_tenant, role="org_admin")

    async def complete(_request):
        return CompletionResult(
            text=f'{{"text":"apiKey={secret}","operations":[]}}',
            tool_calls=[],
            usage=Usage(),
            stop_reason="stop",
            provider="ollama",
            model="test",
        )

    async with app_session(acme_tenant) as db:
        await _configured_model(db, acme_tenant)
        reply = await respond_to_copilot_message(db, actor, "Help me", complete=complete)
        events = (
            await db.execute(select(m.AuditEvent).where(m.AuditEvent.category == "copilot_chat"))
        ).scalars().all()

    assert secret not in reply.text
    assert all(secret not in str(event.resource) for event in events)


async def test_copilot_uses_the_flagged_model_over_the_oldest(app_session) -> None:
    """A flagged model should win even when an older, unflagged model also exists."""
    from oc8.copilot.chat import _select_copilot_model

    tenant = uuid.uuid4()

    async with app_session(tenant) as db:
        older = m.ModelConfig(
            tenant_id=tenant, provider="anthropic", model="old", used_by_copilot=False
        )
        newer = m.ModelConfig(
            tenant_id=tenant, provider="openai", model="new", used_by_copilot=True
        )
        db.add_all([older, newer])
        await db.flush()

        selected = await _select_copilot_model(db)

    assert selected is not None
    assert selected.id == newer.id


async def test_copilot_falls_back_to_oldest_when_nothing_is_flagged(app_session) -> None:
    """Installations created before `used_by_copilot` existed must keep working."""
    from oc8.copilot.chat import _select_copilot_model

    tenant = uuid.uuid4()

    async with app_session(tenant) as db:
        older = m.ModelConfig(
            tenant_id=tenant, provider="anthropic", model="old", used_by_copilot=False
        )
        newer = m.ModelConfig(
            tenant_id=tenant, provider="openai", model="new", used_by_copilot=False
        )
        db.add_all([older, newer])
        await db.flush()

        selected = await _select_copilot_model(db)

    assert selected is not None
    assert selected.id == older.id


async def test_chat_refuses_before_model_call_when_no_model_is_configured(app_session) -> None:
    """Falling back to an implicit provider would bypass the configured model path."""
    from oc8.copilot.chat import respond_to_copilot_message

    tenant = uuid.uuid4()
    actor = Principal(subject="admin", tenant_id=tenant, role="org_admin")
    called = False

    async def complete(_request):
        nonlocal called
        called = True
        raise AssertionError("no model request may be made without ModelConfig")

    async with app_session(tenant) as db:
        reply = await respond_to_copilot_message(db, actor, "Help me", complete=complete)

    assert called is False
    assert reply.proposal_id is None
    assert "configured model" in reply.text
