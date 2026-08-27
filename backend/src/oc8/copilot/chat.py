"""Proposal-only Copilot conversation orchestration."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.audit import append_event
from oc8.auth import Principal
from oc8.copilot.capabilities import InvalidOperation
from oc8.copilot.proposals import create_proposal
from oc8.copilot.redaction import is_secret_request, redact_text, redact_value
from oc8.modelrouter.fallback import complete_with_fallback
from oc8.modelrouter.router import get_model_router
from oc8.modelrouter.types import CompletionRequest, CompletionResult, ModelParams, NeutralMessage

_SYSTEM = """You are the oc8 configuration Copilot. You only prepare reviewable
proposals; you never apply, execute, enable, connect, write, or request secrets.
The only supported operations are agent.mission.set, trigger.create, plugin.enable,
and integration.prepare. Return JSON with text, missingFields, and operations.
Operations must use only the published typed fields. If a request needs a credential
or a direct change, refuse it and explain that a human must use the normal setup or
proposal review flow."""

_SECRET_REFUSAL = (
    "I cannot accept, request, or use credential values. Use the normal secret setup flow."
)
_MODEL_UNAVAILABLE = "Copilot needs a configured model before it can prepare a proposal."


@dataclass(frozen=True)
class CopilotReply:
    text: str
    missing_fields: list[str]
    proposal_id: uuid.UUID | None = None


ModelCompleter = Callable[[CompletionRequest], Awaitable[CompletionResult]]


async def configuration_snapshot(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    """The complete Copilot context allowlist; never dereference connection config."""
    agents = (await db.execute(select(m.Agent).where(m.Agent.deleted_at.is_(None)))).scalars()
    connections = (await db.execute(select(m.McpConnection))).scalars()
    integrations = (await db.execute(select(m.Integration))).scalars()
    plugins = (
        await db.execute(
            select(m.Capa.name, m.CapaInstallation.status).join(
                m.CapaInstallation, m.CapaInstallation.capa_id == m.Capa.id
            )
        )
    ).all()
    return {
        "agents": [
            {"id": str(agent.id), "label": agent.name, "status": agent.status} for agent in agents
        ],
        "connections": [
            {"label": connection.name, "connected": connection.connected}
            for connection in connections
        ],
        "integrations": [
            {
                "id": str(integration.id),
                "label": integration.name,
                "connected": integration.connected,
            }
            for integration in integrations
        ],
        "plugins": [{"label": name, "status": status} for name, status in plugins],
    }


async def _persist(db: AsyncSession, actor: Principal, role: str, content: str) -> None:
    """Store only data after the shared redaction pass, in the immutable audit trail."""
    await append_event(
        db,
        tenant_id=actor.tenant_id,
        actor_type="operator",
        actor_id=None,
        category="copilot_chat",
        action=f"message.{role}",
        resource={"content": redact_text(content)},
        principal=actor,
    )


def _reply_payload(text: str) -> tuple[str, list[str], object]:
    try:
        result = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return redact_text(text), [], []
    if not isinstance(result, dict):
        return "I could not prepare a proposal from that response.", [], []
    # `.get(key, default)` only falls back when the key is ABSENT -- a weaker
    # model (small local Ollama models especially) routinely returns valid
    # JSON with the key present but set to "", which would otherwise render
    # as a blank reply bubble.
    reply_text = redact_text(str(result.get("text") or "I can prepare a proposal for review."))
    missing = result.get("missingFields", [])
    missing_fields = [str(item) for item in missing] if isinstance(missing, list) else []
    return reply_text, missing_fields, redact_value(result.get("operations", []))


async def _select_copilot_model(db: AsyncSession) -> m.ModelConfig | None:
    """The model flagged `used_by_copilot` wins; if nothing is flagged (e.g. an
    installation created before this column existed), fall back to the oldest
    configured model, same as before this function existed."""
    flagged: m.ModelConfig | None = await db.scalar(
        select(m.ModelConfig)
        .where(
            m.ModelConfig.provider != "",
            m.ModelConfig.model != "",
            m.ModelConfig.used_by_copilot.is_(True),
        )
        .order_by(m.ModelConfig.created_at)
        .limit(1)
    )
    if flagged is not None:
        return flagged
    fallback: m.ModelConfig | None = await db.scalar(
        select(m.ModelConfig)
        .where(m.ModelConfig.provider != "", m.ModelConfig.model != "")
        .order_by(m.ModelConfig.created_at)
        .limit(1)
    )
    return fallback


async def respond_to_copilot_message(
    db: AsyncSession,
    actor: Principal,
    message: str,
    *,
    complete: ModelCompleter | None = None,
) -> CopilotReply:
    """Create an optional proposal; this path deliberately has no apply capability."""
    safe_message = redact_text(message)
    await _persist(db, actor, "user", safe_message)
    if is_secret_request(message):
        await _persist(db, actor, "assistant", _SECRET_REFUSAL)
        return CopilotReply(_SECRET_REFUSAL, [])

    model_config = await _select_copilot_model(db)
    if model_config is None:
        await _persist(db, actor, "assistant", _MODEL_UNAVAILABLE)
        return CopilotReply(_MODEL_UNAVAILABLE, [])

    snapshot = redact_value(await configuration_snapshot(db))
    messages = [
        NeutralMessage(role="system", content=_SYSTEM),
        NeutralMessage(role="system", content=json.dumps(snapshot, sort_keys=True)),
        NeutralMessage(role="user", content=safe_message),
    ]
    params = ModelParams(temperature=0.0, max_tokens=1024)
    request_id = uuid.uuid4()
    if complete is not None:
        result = await complete(
            CompletionRequest(
                provider=model_config.provider,
                model=model_config.model,
                messages=messages,
                params=params,
                tenant_id=actor.tenant_id,
                request_id=request_id,
            )
        )
    else:
        result = await complete_with_fallback(
            db,
            get_model_router(),
            tenant_id=actor.tenant_id,
            agent_id=None,
            primary=model_config,
            no_config_provider=model_config.provider,
            no_config_model=model_config.model,
            messages=messages,
            tools=[],
            params=params,
            request_id=request_id,
            contains_restricted=False,
        )
    text, missing_fields, operations = _reply_payload(result.text)
    proposal_id: uuid.UUID | None = None
    if operations:
        try:
            proposal = await create_proposal(db, actor, operations)
        except InvalidOperation:
            missing_fields = ["valid proposal fields"]
        else:
            proposal_id = proposal.id
    await _persist(db, actor, "assistant", text)
    return CopilotReply(text, missing_fields, proposal_id)
