from __future__ import annotations

import uuid

from oc8 import models as m
from oc8.api.v1._serializers import model_to_dto, skill_to_dto, task_to_dto


def test_model_to_dto_canonicalizes_display_alias_provider() -> None:
    # A seeded config may store a display alias ("Ollama", "GPT") rather than the
    # registry's canonical provider. The DTO must expose the canonical form so the
    # UI's provider dropdown (canonical options) pre-selects correctly instead of
    # silently falling back to its first option and corrupting the provider on save.
    cfg = m.ModelConfig(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        provider="Ollama",
        model="mistral:latest",
        locality="local",
        display_name="Llama 3.1 8B (local)",
    )

    dto = model_to_dto(cfg, assigned_to=[])

    assert dto.provider == "ollama"  # "Ollama" alias -> canonical
    assert dto.model == "mistral:latest"


def test_task_to_dto_maps_budget_exceeded_to_waiting_column() -> None:
    task = m.Task(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        title="Do the thing",
        state="budget_exceeded",
    )

    dto = task_to_dto(task)

    # A budget-blocked task needs an operator to unblock it, just like a
    # waiting_for_approval task -- it must not silently fall back to the
    # "backlog" column, which would make it look unstarted rather than
    # blocked.
    assert dto.column == "waiting"


def test_skill_to_dto_exposes_current_version_id() -> None:
    # The UI's assign-skill call (POST /agents/{id}/skills) requires a
    # SkillVersion UUID, not the Skill's own id. Without currentVersionId on
    # the DTO the frontend has nothing correct to send and the assign call
    # 400/404s. See Skill.current_version_id (models/skills.py).
    tenant_id = uuid.uuid4()
    version_id = uuid.uuid4()
    skill = m.Skill(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="do-thing",
        category="general",
        description="",
        author="",
        origin="local",
        current_version_id=version_id,
    )
    version = m.SkillVersion(
        id=version_id,
        tenant_id=tenant_id,
        skill_id=skill.id,
        semver="1.0.0",
        definition={},
        artifact_hash=b"test",
    )

    dto = skill_to_dto(skill, version)

    assert dto.current_version_id == str(version_id)


def test_skill_to_dto_current_version_id_none_when_unset() -> None:
    skill = m.Skill(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="do-thing",
        category="general",
        description="",
        author="",
        origin="local",
    )

    dto = skill_to_dto(skill, None)

    assert dto.current_version_id is None
