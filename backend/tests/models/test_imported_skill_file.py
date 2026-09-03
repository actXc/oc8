from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from oc8 import models as m
from tests.conftest import AppSessionFactory


@pytest.mark.asyncio
async def test_a_row_round_trips_its_content(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        row = m.ImportedSkillFile(
            tenant_id=tenant,
            skill_version_id=uuid.uuid4(),
            rel_path="references/checklist.md",
            content=b"1. Check VAT ID.\n",
        )
        db.add(row)
        await db.flush()

        found = (
            await db.execute(
                select(m.ImportedSkillFile).where(m.ImportedSkillFile.id == row.id)
            )
        ).scalar_one()
        assert found.content == b"1. Check VAT ID.\n"
        assert found.rel_path == "references/checklist.md"


@pytest.mark.asyncio
async def test_the_same_path_twice_for_one_version_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    version_id = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(
            m.ImportedSkillFile(
                tenant_id=tenant,
                skill_version_id=version_id,
                rel_path="references/x.md",
                content=b"one",
            )
        )
        await db.flush()
        db.add(
            m.ImportedSkillFile(
                tenant_id=tenant,
                skill_version_id=version_id,
                rel_path="references/x.md",
                content=b"two",
            )
        )
        with pytest.raises(Exception):  # noqa: B017 -- IntegrityError, driver-specific
            await db.flush()
        await db.rollback()
