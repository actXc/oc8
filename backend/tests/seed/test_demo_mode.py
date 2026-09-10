"""Demo mode: OC8_DEMO flag, bilingual ACME seed overlays, auth/config surface."""

from __future__ import annotations

import pytest

from oc8.api.v1._serializers import (
    _i18n_str,
    _i18n_str_list,
    activity_to_dto,
    agent_to_dto,
    department_to_dto,
    task_to_dto,
)
from oc8.config import Settings
from oc8.seed.demo_i18n import AGENTS_DE, DEPARTMENTS_DE, de_block


def test_settings_demo_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OC8_DEMO", raising=False)
    s = Settings(_env_file=None)
    assert s.demo is False
    assert s.is_demo is False


def test_settings_demo_flag_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OC8_DEMO", "true")
    s = Settings(_env_file=None)
    assert s.demo is True
    assert s.is_demo is True


def test_de_block_wraps_locale_map() -> None:
    assert de_block({"name": "Vertrieb"}) == {"de": {"name": "Vertrieb"}}
    assert de_block(None) == {}
    assert de_block({}) == {}


def test_i18n_helpers_extract_locale_fields() -> None:
    i18n = de_block(AGENTS_DE["vera"])
    assert _i18n_str(i18n, "role") == {"de": "Verkaufsassistentin"}
    assert _i18n_str_list(i18n, "guardrails")["de"][0].startswith("Max.")


def test_department_translations_cover_every_seeded_dept() -> None:
    from oc8.seed import DEPARTMENTS

    for row in DEPARTMENTS:
        slug = row[0]
        assert slug in DEPARTMENTS_DE, f"missing DE overlay for department {slug}"


def test_agent_translations_cover_every_seeded_agent() -> None:
    from oc8.seed import AGENTS

    for row in AGENTS:
        slug = row[0]
        assert slug in AGENTS_DE, f"missing DE overlay for agent {slug}"


def test_settings_demo_credentials_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OC8_DEMO_EMAIL", raising=False)
    monkeypatch.delenv("OC8_DEMO_PASSWORD", raising=False)
    s = Settings(_env_file=None)
    assert s.demo_email == "demo@oc8.ai"
    assert s.demo_password == ""


def test_settings_demo_credentials_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OC8_DEMO_EMAIL", "walk@example.com")
    monkeypatch.setenv("OC8_DEMO_PASSWORD", "walkthrough1")
    s = Settings(_env_file=None)
    assert s.demo_email == "walk@example.com"
    assert s.demo_password == "walkthrough1"


@pytest.mark.asyncio
async def test_auth_config_reports_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8.api.v1 import auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: Settings(env="dev", demo=True, _env_file=None),
    )
    cfg = await auth_mod.auth_config()
    assert cfg.mode == "dev"
    assert cfg.demo is True


@pytest.mark.asyncio
async def test_auth_config_community_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    from oc8.api.v1 import auth as auth_mod

    async def _has_admin() -> bool:
        return True

    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: Settings(env="prod", demo=True, _env_file=None),
    )
    monkeypatch.setattr(auth_mod, "_instance_has_an_administrator", _has_admin)
    cfg = await auth_mod.auth_config()
    assert cfg.mode == "community"
    assert cfg.demo is True
    assert cfg.initialized is True


def test_dept_frames_use_real_connection_keys() -> None:
    """Demo frames must use capa connection names, not generic Lovable labels."""
    from oc8.seed import DEPT_FRAMES

    allowed = {
        "hubspot",
        "odoo",
        "microsoft365",
        "github",
        "jira",
        "google_workspace",
        "gitea",
        "coding",
    }
    banned = {
        "crm",
        "email",
        "office",
        "erp",
        "code-host",
        "chat",
        "files",
        "hr-system",
        "demo-fs",
    }
    for dept, tools in DEPT_FRAMES.items():
        for key in tools:
            assert key in allowed, f"{dept}: unexpected tool key {key!r}"
            assert key not in banned, f"{dept}: banned legacy key {key!r}"


def test_showcase_capa_list_is_nonempty() -> None:
    from oc8.seed.demo_showcase import SHOWCASE_CAPAS

    assert "hubspot_mcp" in SHOWCASE_CAPAS
    assert "odoo_mcp" in SHOWCASE_CAPAS
    assert "microsoft365" in SHOWCASE_CAPAS


@pytest.mark.asyncio
async def test_demo_usage_and_runs_seed(app_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Costs + Statistics surfaces get rows under OC8_DEMO."""
    import uuid

    from sqlalchemy import func, select

    from oc8 import models as m
    from oc8.config import Settings
    from oc8.seed import _seed_acme
    from oc8.seed.demo_showcase import seed_demo_usage, seed_demo_runs, seed_demo_collab

    monkeypatch.setattr(
        "oc8.seed.get_settings",
        lambda: Settings(demo=True, demo_password="walkthrough1", _env_file=None),
    )
    tid = uuid.uuid4()
    monkeypatch.setattr("oc8.seed.ACME_TENANT_ID", tid)

    async with app_session(tid) as session:
        await _seed_acme(session)
        # Showcase is gated on is_demo inside _seed_acme; usage/runs/collab
        # should already be there. Re-call helpers to assert idempotency.
        await seed_demo_usage(session, tenant_id=tid)
        await seed_demo_runs(session, tenant_id=tid)
        await seed_demo_collab(session, tenant_id=tid)

        usage_n = (
            await session.execute(
                select(func.count()).select_from(m.TokenUsageRecord).where(
                    m.TokenUsageRecord.tenant_id == tid
                )
            )
        ).scalar_one()
        run_n = (
            await session.execute(
                select(func.count()).select_from(m.AgentRun).where(m.AgentRun.tenant_id == tid)
            )
        ).scalar_one()
        flow_n = (
            await session.execute(
                select(func.count()).select_from(m.Flow).where(m.Flow.tenant_id == tid)
            )
        ).scalar_one()
        handoff_n = (
            await session.execute(
                select(func.count()).select_from(m.Handoff).where(m.Handoff.tenant_id == tid)
            )
        ).scalar_one()

    assert usage_n >= 20
    assert run_n >= 10
    assert flow_n >= 2
    assert handoff_n >= 2


def test_serializers_surface_demo_i18n() -> None:
    from oc8 import models as m
    from oc8.constants import ACME_TENANT_ID
    from oc8.seed import det

    tid = ACME_TENANT_ID
    agent = m.Agent(
        id=det(tid, "agent", "vera"),
        tenant_id=tid,
        name="Vera",
        role_title="Sales Assistant",
        mission="Sales Assistant for the vertrieb department.",
        status="waiting_for_approval",
        is_team_lead=True,
        presentation={
            "last_action": "Prepared quote",
            "last_run": "2 min ago",
            "tasks_today": 1,
            "guardrails": ["Max quote"],
            "schedule": "Mon-Fri",
            "avatar_color": "x",
            "i18n": de_block(AGENTS_DE["vera"]),
        },
    )
    dto = agent_to_dto(agent)
    assert dto.role_translations["de"] == "Verkaufsassistentin"
    assert dto.last_action_translations["de"].startswith("Angebot")

    dept = m.Department(
        id=det(tid, "dept", "vertrieb"),
        tenant_id=tid,
        name="Sales",
        goal="Fill the pipeline",
        prompt_caching_enabled=True,
        presentation={
            "icon": "sales",
            "okr": "x",
            "kpi_label": "Leads today",
            "kpi_value": "18",
            "activity": 1,
            "accent": "x",
            "i18n": de_block(DEPARTMENTS_DE["vertrieb"]),
        },
    )
    d_dto = department_to_dto(dept)
    assert d_dto.name_translations["de"] == "Vertrieb"

    task_i18n = {
        "title": "Angebot Bauer GmbH (7.400 EUR)",
        "meta": "Freigabe > 5.000 EUR",
    }
    task = m.Task(
        id=det(tid, "task", "t-v1"),
        tenant_id=tid,
        department_id=dept.id,
        title="Quote Bauer GmbH (EUR 7,400)",
        state="waiting_for_approval",
        meta_label="Approval > EUR 5,000",
        payload={"i18n": de_block(task_i18n)},
    )
    t_dto = task_to_dto(task)
    assert t_dto.title_translations["de"].startswith("Angebot")

    import datetime as dt

    activity = m.ActivityEvent(
        id=det(tid, "activity", "a1"),
        tenant_id=tid,
        status="success",
        message="Sent revenue report W27",
        i18n=de_block({"message": "Umsatzreport W27 gesendet"}),
        ts=dt.datetime(2026, 7, 9, 14, 42, tzinfo=dt.UTC),
        cache_hit=False,
    )
    a_dto = activity_to_dto(activity)
    assert a_dto.message_translations["de"] == "Umsatzreport W27 gesendet"
