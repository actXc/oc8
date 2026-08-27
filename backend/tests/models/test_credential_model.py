# backend/tests/models/test_credential_model.py
from __future__ import annotations

import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_credential_round_trips_its_fields(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        cred = m.Credential(
            tenant_id=tenant,
            name="Production S3",
            credential_type="s3_api",
            field_values={"region": "eu-central-1"},
            secret_refs={"access_key": "credential/x/access_key"},
        )
        db.add(cred)
        await db.flush()
        cred_id = cred.id

    async with app_session(tenant) as db:
        reloaded = await db.get(m.Credential, cred_id)
        assert reloaded is not None
        assert reloaded.name == "Production S3"
        assert reloaded.credential_type == "s3_api"
        assert reloaded.field_values == {"region": "eu-central-1"}
        assert reloaded.secret_refs == {"access_key": "credential/x/access_key"}
        assert reloaded.last_tested_at is None
        assert reloaded.last_test_ok is None
