"""Tests for agent narrowing override tracking.

`_enforce_narrowing_logins` (agents_write.py) is the ONLY writer of
`Agent.narrowing_overridden_keys` — the column the new "N of M agents deviate"
feature will read. A tool key is only "overridden" if an operator's save
actually changed its value from what was stored before, not merely because
it appears in the payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.config import get_settings
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

# tests/api/<this file> -> tests -> backend -> repo root, where capas/ lives.
_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_resaving_an_unchanged_tool_does_not_mark_it_as_overridden(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"github": {"enabled": True, "read": True, "modify": False}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(
            tenant_id=tenant,
            department_id=department.id,
            name="Probe",
            narrowing={"tools": {"github": {"enabled": True, "read": True, "modify": False}}},
        )
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            # Resave the narrowing with the exact same value as what's already stored.
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {"github": {"enabled": True, "read": True, "modify": False}}
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        refreshed = await db.get(m.Agent, agent_id)
        assert refreshed.narrowing_overridden_keys == [], (
            "resending a value identical to what was already stored must not mark it as a "
            "deliberate override"
        )


async def test_resaving_a_tool_with_a_changed_value_does_mark_it_as_overridden(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"github": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Probe")
        db.add(agent)
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {"github": {"enabled": True, "read": True, "modify": False}}
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text

    async with app_session(tenant) as db:
        refreshed = await db.get(m.Agent, agent_id)
        assert refreshed.narrowing_overridden_keys == ["github"]


async def test_setting_only_for_a_connection_with_no_value_spec_is_accepted(
    app_session: AppSessionFactory,
) -> None:
    """Regression: `only` is a plain tool-name allowlist, not a value_spec
    feature -- github_mcp/jira_mcp/microsoft365/google_workspace all ship real
    `only`-based guardrail presets (e.g. github's "Support: Issue triage, no
    code access") and none of them declare a value_spec. Gating `only` behind
    `connection_supports_value_spec` (this test's own name until this fix)
    broke every one of those presets; only `approval_eur` genuinely needs a
    value_spec, since that's where the monetary threshold gets compared
    against. Found live: applying github's own "Support" preset 422'd."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"github": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Probe")
        conn = m.McpConnection(
            tenant_id=tenant,
            name="github",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "github_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {
                            "github": {
                                "enabled": True,
                                "read": True,
                                "modify": True,
                                "only": ["some_tool"],
                            }
                        }
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text


async def test_an_empty_only_list_for_a_connection_with_no_value_spec_is_accepted(
    app_session: AppSessionFactory,
) -> None:
    """The frontend's GuardrailValue always sends `only: []` for a tool that
    never had an allowlist -- never `null` -- so this must NOT trip the same
    gate a genuinely non-empty `only` does. Regression for a live-verification
    finding: every real save from the new ToolGuardrailTable UI was 422ing on
    any connection with no value_spec, because `only` was checked with
    `is not None` instead of a real non-empty check."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"github": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Probe")
        conn = m.McpConnection(
            tenant_id=tenant,
            name="github",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "github_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {
                            "github": {
                                "enabled": True,
                                "read": True,
                                "modify": True,
                                "only": [],
                            }
                        }
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text


async def test_setting_approval_eur_for_a_connection_with_a_value_spec_is_accepted(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        department = m.Department(
            tenant_id=tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Probe")
        conn = m.McpConnection(
            tenant_id=tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()
        agent_id = agent.id

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.put(
                f"/api/v1/agents/{agent_id}/narrowing",
                json={
                    "narrowing": {
                        "tools": {
                            "odoo": {
                                "enabled": True,
                                "read": True,
                                "modify": True,
                                "approval_eur": 3000,
                            }
                        }
                    }
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
