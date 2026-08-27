"""Closed capability registry for secret-blind Copilot operations.

No capability accepts arbitrary dictionaries.  In particular this module never
imports the secret store or an HTTP/shell client: a proposal may only reference
existing resources and invoke their established server-side service.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oc8 import models as m
from oc8.automation.catalogue import list_installed_automation_events
from oc8.capas.lifecycle import enable_plugin
from oc8.modelrouter.subscription_guard import SubscriptionModelNotManualOnly
from oc8.triggers.service import create_trigger


class _Operation(BaseModel):
    # JSON UUID references arrive as strings. Strict primitive validation still
    # comes from each field's declared type and the closed extra-key policy.
    model_config = ConfigDict(extra="forbid")


class MissionSet(_Operation):
    type: Literal["agent.mission.set"]
    agentId: uuid.UUID
    mission: str = Field(min_length=1, max_length=10_000)


class TriggerCreate(_Operation):
    type: Literal["trigger.create"]
    agentId: uuid.UUID
    kind: Literal["cron", "event"]
    taskText: str = Field(min_length=1, max_length=10_000)
    cronExpression: str | None = Field(default=None, max_length=256)
    eventSource: str | None = Field(default=None, max_length=128)
    eventType: str | None = Field(default=None, max_length=128)


class PluginEnable(_Operation):
    type: Literal["plugin.enable"]
    pluginId: uuid.UUID
    grantedPermissions: list[str] = Field(default_factory=list, max_length=64)


class IntegrationPrepare(_Operation):
    type: Literal["integration.prepare"]
    integrationId: uuid.UUID
    # A reference is intentionally all this operation can carry. Credential
    # material belongs to the integration's normal setup flow, outside Copilot.
    configurationRef: uuid.UUID | None = None


Operation = MissionSet | TriggerCreate | PluginEnable | IntegrationPrepare
_OPERATIONS = TypeAdapter(list[Operation])


class InvalidOperation(ValueError):
    """Safe, value-free validation failure exposed at every boundary."""

    def __init__(self) -> None:
        super().__init__("invalid copilot operation")


def parse_operations(raw: object) -> list[Operation]:
    try:
        operations = _OPERATIONS.validate_python(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise InvalidOperation() from exc
    if not operations:
        raise InvalidOperation()
    return operations


def operation_data(operation: Operation) -> dict[str, Any]:
    """The sole conversion into persisted data, after closed-schema validation."""
    return operation.model_dump(mode="json")


def operation_label(operation_type: str) -> str:
    return operation_type


def operation_references(data: dict[str, Any]) -> dict[str, str]:
    """Audit/response safe references only; never return textual configuration."""
    refs: dict[str, str] = {}
    for key in ("agentId", "pluginId", "integrationId", "configurationRef"):
        value = data.get(key)
        if value is not None:
            refs[key] = str(value)
    return refs


async def target_revision(
    db: AsyncSession, operation: Operation, *, lock_for_apply: bool = False
) -> str | None:
    """Read a target's trigger-maintained revision.

    Application takes a row lock before comparing it, so an update cannot land
    between the stale check and the capability applier. Proposal creation only
    snapshots and therefore never takes a lock.
    """
    if isinstance(operation, (MissionSet, TriggerCreate)):
        statement = select(m.Agent.config_revision).where(
            m.Agent.id == operation.agentId, m.Agent.deleted_at.is_(None)
        )
        changed = await db.scalar(statement.with_for_update() if lock_for_apply else statement)
        if changed is None:
            raise InvalidOperation()
        return str(changed)
    if isinstance(operation, PluginEnable):
        statement = select(m.Capa.config_revision).where(m.Capa.id == operation.pluginId)
        changed = await db.scalar(statement.with_for_update() if lock_for_apply else statement)
        if changed is None:
            raise InvalidOperation()
        return str(changed)
    statement = select(m.Integration.config_revision).where(
        m.Integration.id == operation.integrationId
    )
    changed = await db.scalar(statement.with_for_update() if lock_for_apply else statement)
    if changed is None:
        raise InvalidOperation()
    return str(changed)


async def apply_operation(db: AsyncSession, *, tenant_id: uuid.UUID, data: dict[str, Any]) -> None:
    """Apply exactly one validated operation through existing service boundaries."""
    operation = parse_operations([data])[0]
    if isinstance(operation, MissionSet):
        agent = await db.get(m.Agent, operation.agentId)
        if agent is None or agent.deleted_at is not None:
            raise InvalidOperation()
        agent.mission = operation.mission
        await db.flush()
        return
    if isinstance(operation, TriggerCreate):
        events = await list_installed_automation_events(db)
        try:
            await create_trigger(
                db,
                tenant_id=tenant_id,
                agent_id=operation.agentId,
                kind=operation.kind,
                task_text=operation.taskText,
                cron_expression=operation.cronExpression,
                event_source=operation.eventSource,
                event_type=operation.eventType,
                declared_events={(event.source, event.type) for event in events},
            )
        except SubscriptionModelNotManualOnly as exc:
            # A ChatGPT-subscription-backed agent may not be given an
            # unattended trigger (`oc8.modelrouter.subscription_guard`), and
            # the Copilot is no exception -- `operation.agentId` is arbitrary
            # and `kind` may be "cron". Reported through this module's one
            # rejection convention (`InvalidOperation`, which `apply_proposal`
            # turns into a rejected proposal and a 409) rather than a new
            # error surface, and value-free like every other InvalidOperation.
            raise InvalidOperation() from exc
        return
    if isinstance(operation, PluginEnable):
        await enable_plugin(
            db,
            tenant_id=tenant_id,
            capa_id=operation.pluginId,
            granted_permissions=operation.grantedPermissions,
        )
        return
    # Integration prepare validates the opaque reference still identifies a
    # tenant-visible integration. It deliberately does not configure credentials.
    if await db.get(m.Integration, operation.integrationId) is None:
        raise InvalidOperation()
