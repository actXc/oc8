"""Skill soft delete: deleted_at column on Skill, SkillVersion, SkillAssignment."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from oc8 import models as m
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def tenant_id() -> uuid.UUID:
    """Fixture providing a fresh test tenant ID."""
    return uuid.uuid4()


@pytest.fixture
async def db_session(app_session: AppSessionFactory, tenant_id: uuid.UUID):
    """Database session bound to the test tenant."""
    async with app_session(tenant_id) as session:
        yield session


async def test_skill_has_deleted_at_column(db_session, tenant_id):
    skill = m.Skill(tenant_id=tenant_id, name="Test", description="")
    db_session.add(skill)
    await db_session.flush()
    assert skill.deleted_at is None
    skill.deleted_at = dt.datetime.now(tz=dt.UTC)
    await db_session.flush()
    assert skill.deleted_at is not None


async def test_skill_version_has_deleted_at_column(db_session, tenant_id):
    skill_version = m.SkillVersion(
        tenant_id=tenant_id, skill_id=uuid.uuid4(), semver="1.0.0", definition={}, artifact_hash=b""
    )
    db_session.add(skill_version)
    await db_session.flush()
    assert skill_version.deleted_at is None
    skill_version.deleted_at = dt.datetime.now(tz=dt.UTC)
    await db_session.flush()
    assert skill_version.deleted_at is not None


async def test_skill_assignment_has_deleted_at_column(db_session, tenant_id):
    skill_assignment = m.SkillAssignment(
        tenant_id=tenant_id, skill_version_id=uuid.uuid4(), enabled=True
    )
    db_session.add(skill_assignment)
    await db_session.flush()
    assert skill_assignment.deleted_at is None
    skill_assignment.deleted_at = dt.datetime.now(tz=dt.UTC)
    await db_session.flush()
    assert skill_assignment.deleted_at is not None
