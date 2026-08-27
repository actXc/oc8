from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_secret_and_dek_rows_roundtrip(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as s:
        dek = m.TenantDek(tenant_id=tenant, wrapped_dek=b"wrapped", key_version="env:v1")
        s.add(dek)
        sec = m.Secret(
            tenant_id=tenant,
            name="gh-token",
            ciphertext=b"ct",
            nonce=b"012345678901",
            key_version="env:v1",
            kind="api_key",
        )
        s.add(sec)
        await s.flush()
        got = (await s.execute(select(m.Secret).where(m.Secret.name == "gh-token"))).scalar_one()
        assert got.ciphertext == b"ct" and got.kind == "api_key"
