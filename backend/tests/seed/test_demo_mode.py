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
