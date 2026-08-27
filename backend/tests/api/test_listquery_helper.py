import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from oc8 import models as m
from oc8.api.v1._listquery import apply_group_order, apply_search, paginate
from tests.conftest import AppSessionFactory


@pytest.fixture
async def tenant_id():
    """Fixture providing a fresh test tenant ID."""
    return uuid.uuid4()


@pytest.fixture
async def db_session(app_session: AppSessionFactory, tenant_id):
    """Database session bound to the test tenant."""
    async with app_session(tenant_id) as session:
        yield session


@pytest.mark.asyncio
async def test_apply_search_matches_substring_case_insensitive(db_session, tenant_id):
    db_session.add_all(
        [
            m.Skill(tenant_id=tenant_id, name="Email Triage", description=""),
            m.Skill(tenant_id=tenant_id, name="Invoice Matching", description=""),
        ]
    )
    await db_session.flush()
    stmt = apply_search(
        select(m.Skill), model=m.Skill, columns=[m.Skill.name, m.Skill.description], search="email"
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert [r.name for r in rows] == ["Email Triage"]


@pytest.mark.asyncio
async def test_apply_group_order_orders_by_group_field_first(db_session, tenant_id):
    db_session.add_all(
        [
            m.Skill(tenant_id=tenant_id, name="B", category="ops", description=""),
            m.Skill(tenant_id=tenant_id, name="A", category="sales", description=""),
            m.Skill(tenant_id=tenant_id, name="C", category="ops", description=""),
        ]
    )
    await db_session.flush()
    stmt = apply_group_order(
        select(m.Skill),
        model=m.Skill,
        group_by="category",
        group_fields={"category": m.Skill.category},
        default_order=m.Skill.name,
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert [r.category for r in rows] == ["ops", "ops", "sales"]


def test_apply_group_order_rejects_unknown_field():
    """A 400, not a 500. `group_by` arrives straight off the query string, so an
    unknown value is a caller error; a bare `ValueError` here would leave every
    list route in the plan's scope answering an unknown `group_by` with an
    Internal Server Error (Task 22 live-API finding)."""
    with pytest.raises(HTTPException) as exc:
        apply_group_order(
            select(m.Skill),
            model=m.Skill,
            group_by="bogus",
            group_fields={"category": m.Skill.category},
            default_order=m.Skill.name,
        )
    assert exc.value.status_code == 400
    assert "not a groupable field" in str(exc.value.detail)
    # The refusal names what WOULD have worked.
    assert "category" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_paginate_returns_page_and_total_count(db_session, tenant_id):
    db_session.add_all(
        [m.Skill(tenant_id=tenant_id, name=f"S{i}", description="") for i in range(5)]
    )
    await db_session.flush()
    stmt = select(m.Skill).order_by(m.Skill.name)
    rows, total = await paginate(db_session, stmt, limit=2, offset=1)
    assert total == 5
    assert [r.name for r in rows] == ["S1", "S2"]
