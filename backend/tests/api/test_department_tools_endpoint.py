"""PUT /departments/{id}/tools -- validates the tool-policy payload against
the shape `authz/pdp.py`'s `ToolPolicy.from_json` actually expects, instead of
writing arbitrary JSON straight into `department.frame["tools"]`. Same
tenant-wide-only `department:manage` gate as `PATCH /departments/{id}`
(test_department_patch.py) -- no seat can ever hold it, so `org_admin` is used
for the allowed cases and `member` for the 403 case, matching that file's
convention exactly."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.config import get_settings
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


def _headers(tenant: uuid.UUID, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="u", role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def _seed_department(app_session: AppSessionFactory) -> tuple[uuid.UUID, uuid.UUID]:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        dept = m.Department(
            tenant_id=tenant,
            name="Sales",
            goal="",
            frame={},
            presentation={"icon": "building", "slug": "sales"},
        )
        db.add(dept)
        # Every existing test in this file writes `approvalEur`/`only` for the
        # "odoo" key -- since Task 5, that now needs to resolve back to a real
        # manifest connection with a `value_spec` to be accepted (odoo_mcp's
        # `primary` connection has one; see `capas/odoo_mcp/tool_pack.toml`).
        conn = m.McpConnection(
            tenant_id=tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add(conn)
        await db.flush()
        return tenant, dept.id


async def test_well_formed_payload_is_accepted_and_echoed_back(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {
        "tools": {
            "odoo": {
                "enabled": True,
                "read": True,
                "modify": False,
                "approvalEur": 3000,
                "approvalActions": ["send"],
                "only": ["search_records", "post_message"],
            }
        }
    }
    # The frame -- and therefore the read path -- stores/echoes snake_case
    # keys, matching what `ToolPolicy.from_json` expects (see
    # `DepartmentToolsDTO.tools: dict[str, dict[str, Any]]`: the inner values
    # are raw dicts, not nested CamelModels, so no camelCase aliasing applies
    # to them -- only the request body goes through camelCase translation).
    expected = {
        "tools": {
            "odoo": {
                "enabled": True,
                "read": True,
                "modify": False,
                "approval_eur": 3000,
                "approval_actions": ["send"],
                "only": ["search_records", "post_message"],
                "default_connection_id": None,
            }
        },
        "deviationCounts": {"odoo": 0},
        "agentCount": 0,
    }
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json() == expected

        get_resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json() == expected


async def test_unknown_key_is_rejected(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True, "foo": True}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text


async def test_wrong_type_for_bool_field_is_rejected(app_session: AppSessionFactory) -> None:
    # Not a string like "yes"/"true" -- Pydantic v2's lenient bool coercion
    # accepts those. A list is not bool-coercible under any mode.
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": ["not", "a", "bool"]}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text


async def test_negative_approval_eur_is_rejected(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"approvalEur": -100}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text


async def test_zero_approval_eur_is_preserved_not_dropped(
    app_session: AppSessionFactory,
) -> None:
    """`0` means "a human decides every send" (ToolPolicy's docstring in
    pdp.py, and the identical rule GuardrailPreset enforces in manifest.py) --
    the single most important assertion in this file. A normalisation bug
    here would silently delete the strictest possible setting."""
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"approvalEur": 0}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["approval_eur"] == 0

        get_resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools"]["odoo"]["approval_eur"] == 0


async def test_empty_string_approval_action_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"approvalActions": [""]}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text


async def test_non_string_approval_action_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    """Raw JSON permits a non-string entry even though `approval_actions` is
    typed `list[str]`; verify Pydantic's coercion does NOT silently accept
    it."""
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"approvalActions": [123]}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text


async def test_omitting_only_is_accepted_and_reads_back_as_null(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["only"] is None

        get_resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools"]["odoo"]["only"] is None


async def test_applying_a_guardrail_with_nonempty_only_persists_the_exact_list(
    app_session: AppSessionFactory,
) -> None:
    """There is no dedicated "apply a guardrail" endpoint (task-6 investigation
    confirmed: `guardrail-preset-picker.tsx` is entirely client-side and every
    save path -- preset, library guardrail, or hand-configured -- converges on
    this one `PUT`). This proves that ONE write path preserves `only` for a
    real Task-4 library entry with a non-empty `only`: `sales_autonomous_with_
    limit` from `plugins/odoo_mcp/guardrails/`, which deliberately excludes
    `delete_record` from its 9-tool alphabet (that file's own summary: "a euro
    threshold can never catch a deletion, which carries no amount")."""
    tenant, dept_id = await _seed_department(app_session)
    payload = {
        "tools": {
            "odoo": {
                "enabled": True,
                "read": True,
                "modify": False,
                "approvalEur": 1000,
                "approvalActions": [],
                "only": [
                    "search_records",
                    "get_record",
                    "list_models",
                    "list_resource_templates",
                    "aggregate_records",
                    "create_record",
                    "update_record",
                    "post_message",
                ],
            }
        }
    }
    expected_only = payload["tools"]["odoo"]["only"]
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["only"] == expected_only

        get_resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools"]["odoo"]["only"] == expected_only
        assert "delete_record" not in get_resp.json()["tools"]["odoo"]["only"]


async def test_applying_a_guardrail_with_empty_only_persists_explicit_empty_list(
    app_session: AppSessionFactory,
) -> None:
    """Same write path, the other real Task-4 case: `quote_approval_threshold`
    from `plugins/odoo_mcp/guardrails/` deliberately ships `only = []` --
    that file's own summary calls this out as a documented gap ("`only` is
    deliberately left empty here ... delete_record and post_message stay
    reachable ungated"). This is the "dropped at four boundaries" failure mode
    named in the guardrail-library spec: an explicit `only: []` must read back
    as `[]`, not `null`/missing -- distinct from `test_omitting_only_is_
    accepted_and_reads_back_as_null` above, which never sends the key at all."""
    tenant, dept_id = await _seed_department(app_session)
    payload = {
        "tools": {
            "odoo": {
                "enabled": True,
                "read": True,
                "modify": False,
                "approvalEur": 3000,
                "approvalActions": [],
                "only": [],
            }
        }
    }
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["only"] == []
        assert resp.json()["tools"]["odoo"]["only"] is not None

        get_resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools"]["odoo"]["only"] == []
        assert get_resp.json()["tools"]["odoo"]["only"] is not None


async def test_member_role_is_refused(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True}}}
    async with _http() as http:
        h = _headers(tenant, "member")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 403, resp.text


async def test_bogus_department_id_404s(app_session: AppSessionFactory) -> None:
    tenant, _ = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{uuid.uuid4()}/tools", json=payload, headers=h)
        assert resp.status_code == 404, resp.text


# ---------------------------- default_connection_id (department default login)


async def test_default_connection_id_round_trips(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    conn_id = str(uuid.uuid4())
    payload = {"tools": {"odoo": {"enabled": True, "defaultConnectionId": conn_id}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["default_connection_id"] == conn_id

        get_resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["tools"]["odoo"]["default_connection_id"] == conn_id


async def test_omitting_default_connection_id_is_accepted_and_reads_back_as_null(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["default_connection_id"] is None


async def test_non_uuid_default_connection_id_is_rejected(app_session: AppSessionFactory) -> None:
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True, "defaultConnectionId": "not-a-uuid"}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text


async def test_empty_string_default_connection_id_is_normalised_to_null(
    app_session: AppSessionFactory,
) -> None:
    """The frontend clears a picker by sending "" -- treated the same as
    omitting the field entirely, not rejected."""
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True, "defaultConnectionId": ""}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["default_connection_id"] is None


# ---------------------------- only/approval_eur value-spec gate (Task 5)


async def test_setting_approval_eur_for_a_connection_with_no_value_spec_is_rejected(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with app_session(tenant) as db:
        conn = m.McpConnection(
            tenant_id=tenant,
            name="github",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "github_mcp", "_connection_key": "primary"},
        )
        db.add(conn)
        await db.flush()

    payload = {"tools": {"github": {"enabled": True, "read": True, "approvalEur": 3000}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 422, resp.text
        body = resp.json()["detail"]
        assert body["error"] == "value_spec_not_supported"
        assert body["violations"] == [{"connection": "github", "field": "approval_eur"}]


async def test_setting_only_for_a_connection_with_a_value_spec_is_accepted(
    app_session: AppSessionFactory,
) -> None:
    # `_seed_department` already seeds an "odoo" McpConnection stamped to
    # `odoo_mcp`'s `primary` connection, which HAS a `value_spec` block.
    tenant, dept_id = await _seed_department(app_session)
    payload = {"tools": {"odoo": {"enabled": True, "read": True, "only": ["search_records"]}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        assert resp.json()["tools"]["odoo"]["only"] == ["search_records"]


# ---------------------------- deviation_counts and agent_count (Task 6)


async def test_get_department_tools_reports_deviation_counts(
    app_session: AppSessionFactory,
) -> None:
    tenant, dept_id = await _seed_department(app_session)
    async with app_session(tenant) as db:
        dept = await db.get(m.Department, dept_id)
        dept.frame = {"tools": {"github": {"enabled": True, "read": True, "modify": True}}}
        agent_a = m.Agent(
            tenant_id=tenant, department_id=dept_id, name="A",
            narrowing_overridden_keys=["github"],
        )
        agent_b = m.Agent(
            tenant_id=tenant, department_id=dept_id, name="B",
            narrowing_overridden_keys=[],
        )
        agent_c = m.Agent(
            tenant_id=tenant, department_id=dept_id, name="C",
            narrowing_overridden_keys=["github"],
        )
        db.add_all([agent_a, agent_b, agent_c])
        await db.flush()

    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.get(f"/api/v1/departments/{dept_id}/tools", headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deviationCounts"]["github"] == 2
        assert body["agentCount"] == 3


async def test_put_department_tools_also_reports_deviation_counts(
    app_session: AppSessionFactory,
) -> None:
    """The write endpoint returns the same DTO shape as the read endpoint --
    a caller acting on the PUT response directly must not see stale zeros
    for a department that already has agents with real overrides."""
    tenant, dept_id = await _seed_department(app_session)
    async with app_session(tenant) as db:
        agent = m.Agent(
            tenant_id=tenant, department_id=dept_id, name="A",
            narrowing_overridden_keys=["odoo"],
        )
        db.add(agent)
        await db.flush()

    payload = {"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}}
    async with _http() as http:
        h = _headers(tenant, "org_admin")
        resp = await http.put(f"/api/v1/departments/{dept_id}/tools", json=payload, headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deviationCounts"]["odoo"] == 1
        assert body["agentCount"] == 1
