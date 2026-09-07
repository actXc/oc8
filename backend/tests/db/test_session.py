from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from oc8.db.session import owner_session

pytestmark = pytest.mark.asyncio


async def test_owner_session_commits_on_success() -> None:
    slug = f"owner-session-{uuid.uuid4().hex[:10]}"
    tid = uuid.uuid4()
    async with owner_session() as db:
        db.add(m.Organization(id=tid, slug=slug, name="X", tier="standard", region="eu", settings={}))

    async with owner_session() as db:
        row = (await db.execute(select(m.Organization).where(m.Organization.id == tid))).scalar_one_or_none()
        assert row is not None
        assert row.slug == slug


async def test_owner_session_rolls_back_on_exception() -> None:
    slug = f"owner-session-{uuid.uuid4().hex[:10]}"
    tid = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with owner_session() as db:
            db.add(m.Organization(id=tid, slug=slug, name="X", tier="standard", region="eu", settings={}))
            await db.flush()
            raise RuntimeError("boom")

    async with owner_session() as db:
        row = (await db.execute(select(m.Organization).where(m.Organization.id == tid))).scalar_one_or_none()
        assert row is None
