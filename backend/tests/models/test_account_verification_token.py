from __future__ import annotations

import datetime as dt
import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_a_token_row_round_trips(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        token = m.AccountVerificationToken(
            tenant_id=tenant,
            member_id=uuid.uuid4(),
            purpose="password_reset",
            token_hash="a" * 64,
            expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(hours=1),
        )
        db.add(token)
        await db.flush()
        assert token.used_at is None
        assert token.new_email is None
