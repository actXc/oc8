from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from oc8.constants import ACME_TENANT_ID
from oc8.triggers.service import (
    InvalidTriggerConfig,
    compute_next_run,
    create_trigger,
    delete_trigger,
    list_triggers_for_agent,
    update_trigger,
)
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def test_compute_next_run_respects_cron_expression() -> None:
    after = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    next_run = compute_next_run("0 9 * * *", after=after, jitter_seconds=0)
    assert next_run.hour == 9 and next_run.minute == 0
    assert next_run.date() == after.date()


def test_compute_next_run_applies_jitter_within_bounds() -> None:
    after = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    base = compute_next_run("0 9 * * *", after=after, jitter_seconds=0)
    jittered = compute_next_run("0 9 * * *", after=after, jitter_seconds=60)
    assert base <= jittered <= base + timedelta(seconds=60)


def test_compute_next_run_rejects_invalid_cron() -> None:
    with pytest.raises(InvalidTriggerConfig):
        compute_next_run("not a cron", after=datetime.now(tz=UTC))


async def test_create_trigger_cron_computes_next_run(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        trigger = await create_trigger(
            db,
            tenant_id=tenant,
            agent_id=uuid.uuid4(),
            kind="cron",
            task_text="daily report",
            cron_expression="0 9 * * *",
        )
        assert trigger.next_run_at is not None
        assert trigger.kind == "cron"


async def test_create_trigger_cron_without_expression_raises(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        with pytest.raises(InvalidTriggerConfig):
            await create_trigger(
                db, tenant_id=tenant, agent_id=uuid.uuid4(), kind="cron", task_text="x"
            )


async def test_create_trigger_event_kind_no_next_run(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        trigger = await create_trigger(
            db,
            tenant_id=tenant,
            agent_id=uuid.uuid4(),
            kind="event",
            task_text="respond to issue",
            event_source="github",
            # Namespaced, not the literal "github.issues.opened": this row is
            # never fired/deleted, and handle_inbound_event (Task 3) matches
            # ACROSS ALL TENANTS by (source, type) -- a real webhook-event
            # test dispatching that literal type later in the same run would
            # otherwise match and fire this leftover trigger unexpectedly.
            event_type="github.issues.opened.test-service-create",
        )
        assert trigger.next_run_at is None
        assert trigger.event_source == "github"


async def test_create_trigger_event_kind_without_fields_raises(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        with pytest.raises(InvalidTriggerConfig):
            await create_trigger(
                db, tenant_id=tenant, agent_id=uuid.uuid4(), kind="event", task_text="x"
            )


async def test_update_trigger_recomputes_next_run_on_cron_change(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        trigger = await create_trigger(
            db,
            tenant_id=tenant,
            agent_id=uuid.uuid4(),
            kind="cron",
            task_text="report",
            cron_expression="0 9 * * *",
        )
        original_next_run = trigger.next_run_at
        updated = await update_trigger(db, trigger, cron_expression="0 18 * * *")
        assert updated.cron_expression == "0 18 * * *"
        assert updated.next_run_at != original_next_run


async def test_list_and_delete_trigger(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    agent_id = uuid.uuid4()
    async with app_session(tenant) as db:
        trigger = await create_trigger(
            db,
            tenant_id=tenant,
            agent_id=agent_id,
            kind="cron",
            task_text="x",
            cron_expression="0 9 * * *",
        )
        listed = await list_triggers_for_agent(db, agent_id=agent_id)
        assert len(listed) == 1 and listed[0].id == trigger.id

        await delete_trigger(db, trigger)
        listed_after = await list_triggers_for_agent(db, agent_id=agent_id)
        assert listed_after == []
