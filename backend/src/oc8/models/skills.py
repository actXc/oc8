"""Skills: reusable capabilities and their assignments (tech-spec §6.5)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, LargeBinary, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from oc8.db.base import Base, TimestampMixin
from oc8.models._mixins import PkMixin, SoftDeleteMixin, TenantMixin


class Skill(Base, PkMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "skill"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    author: Mapped[str] = mapped_column(Text, nullable=False, default="")
    origin: Mapped[str] = mapped_column(Text, nullable=False, default="local")
    trust_level: Mapped[str] = mapped_column(Text, nullable=False, default="first_party")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (
        CheckConstraint(
            "trust_level IN ('first_party','verified','community')",
            name="ck_skill_trust",
        ),
        CheckConstraint("origin IN ('local','store')", name="ck_skill_origin"),
    )


class SkillVersion(Base, PkMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "skill_version"

    skill_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    semver: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    artifact_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    __table_args__ = (UniqueConstraint("skill_id", "semver", name="uq_skill_version"),)


class SkillAssignment(Base, PkMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """Who may load a skill. The scope is which column is filled:

        agent_id set        -> this agent
        department_id set   -> every agent in that department
        neither             -> every agent of the tenant

    A skill could once be given to a single agent only, so a standard set for a
    company meant one assignment per skill per agent, redone by hand whenever
    somebody was hired.
    """

    __tablename__ = "skill_assignment"

    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    skill_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # A row naming BOTH would read as "this agent" and "everyone here" at
        # once. Uniqueness is three partial indexes (migration 0040), because a
        # single UNIQUE over nullable columns accepts the same tenant-wide row
        # any number of times -- NULLs are distinct in Postgres.
        CheckConstraint(
            "agent_id IS NULL OR department_id IS NULL", name="ck_skill_assignment_scope"
        ),
    )
