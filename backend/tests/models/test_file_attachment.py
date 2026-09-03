import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


async def test_file_attachment_round_trips(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = m.FileAttachment(
            tenant_id=tenant,
            owner_type="chat_message",
            owner_id=uuid.uuid4(),
            bucket_key=f"{tenant}/chat_message/{uuid.uuid4()}-report.pdf",
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=1234,
            extracted_text="hello",
            is_image=False,
        )
        db.add(row)
        await db.flush()

    async with app_session(tenant) as db:
        found = (
            await db.execute(select(m.FileAttachment).where(m.FileAttachment.tenant_id == tenant))
        ).scalar_one()
        assert found.filename == "report.pdf"
        assert found.extracted_text == "hello"


async def test_file_attachment_rejects_unknown_owner_type(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.FileAttachment(
                tenant_id=tenant,
                owner_type="not_a_real_type",
                owner_id=uuid.uuid4(),
                bucket_key=f"{tenant}/x/{uuid.uuid4()}",
                filename="x",
                content_type="text/plain",
                size_bytes=1,
                is_image=False,
            )
        )
        with pytest.raises(Exception):  # IntegrityError from the CHECK constraint
            await db.flush()
        await db.rollback()
