"""`POST /agents/{id}/guardrails/interpret-from-instruction` -- the "Copilot"
button on the guardrails table: reads the agent's own instructions
(`agent.mission`) instead of an operator-typed definition, and proposes rules
for every restricted function of one connection in a single LLM call. Same
review contract as `POST /agents/{id}/guardrails/interpret`: never writes
anything, only ever a pre-filled suggestion.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.config import get_settings
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from oc8.modelrouter.types import CompletionResult, ToolCall, Usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio

_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


def _tool_call_result(**arguments: Any) -> CompletionResult:
    return CompletionResult(
        text="",
        tool_calls=[ToolCall(id="1", name="set_guardrail_interpretations", arguments=arguments)],
        usage=Usage(tokens_in=10, tokens_out=5),
        stop_reason="tool_calls",
        provider="ollama",
        model="mistral:latest",
    )


async def _agent_on(
    db: Any,
    tenant: uuid.UUID,
    *,
    mission: str,
    plugin: str = "odoo_mcp",
    conn_name: str = "odoo",
) -> uuid.UUID:
    department = m.Department(
        tenant_id=tenant,
        name=f"D-{uuid.uuid4().hex}",
        frame={"tools": {conn_name: {"enabled": True, "read": True, "modify": True}}},
    )
    db.add(department)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Kai", mission=mission)
    conn = m.McpConnection(
        tenant_id=tenant,
        name=conn_name,
        server_url="",
        transport="stdio",
        config={"_plugin_name": plugin, "_connection_key": "primary"},
    )
    db.add_all([agent, conn])
    await db.flush()
    return agent.id


async def test_the_instruction_drives_a_multi_function_suggestion(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(
            db,
            tenant,
            mission="Bearbeite Helpdesk-Tickets. Lösche nie einen Datensatz, und "
            "hole vor jedem Löschen eine Freigabe ein.",
            conn_name="odoo-1",
        )

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(
            assignments=[
                {"function": "delete_record", "decision": "not_allowed", "conditions": []},
            ]
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret-from-instruction",
                json={"connectionName": "odoo-1"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {
                "results": [
                    {"function": "delete_record", "decision": "not_allowed", "conditions": []}
                ]
            }


async def test_a_with_limits_assignment_becomes_a_structured_condition(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(
            db,
            tenant,
            mission="Lege Aufträge bis 500€ selbstständig an, darüber Freigabe.",
            conn_name="odoo-2",
        )

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(
            assignments=[
                {
                    "function": "create_record",
                    "decision": "with_limits",
                    "conditions": [
                        {
                            "attribute": "order_value",
                            "operator": ">",
                            "value": 500,
                            "then": "require_approval",
                        }
                    ],
                },
            ]
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret-from-instruction",
                json={"connectionName": "odoo-2"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {
                "results": [
                    {
                        "function": "create_record",
                        "decision": "with_limits",
                        "conditions": [
                            {
                                "attribute": "order_value",
                                "datatype": "number",
                                "operator": ">",
                                "value": 500,
                                "then": "require_approval",
                            }
                        ],
                    }
                ]
            }


async def test_an_unknown_function_is_dropped_but_the_rest_of_the_batch_survives(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(
            db, tenant, mission="Lösche nie, und rufe niemals irgendetwas Erfundenes auf.",
            conn_name="odoo-3",
        )

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(
            assignments=[
                {"function": "not_a_real_tool", "decision": "not_allowed", "conditions": []},
                {"function": "delete_record", "decision": "not_allowed", "conditions": []},
            ]
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret-from-instruction",
                json={"connectionName": "odoo-3"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {
                "results": [
                    {"function": "delete_record", "decision": "not_allowed", "conditions": []}
                ]
            }


async def test_an_empty_instruction_fails_closed(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(db, tenant, mission="", conn_name="odoo-4")

    called = False

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        nonlocal called
        called = True
        return _tool_call_result(assignments=[])

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret-from-instruction",
                json={"connectionName": "odoo-4"},
                headers=headers,
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["error"] == "guardrail_not_understood"
    assert called is False


async def test_the_model_may_legitimately_suggest_nothing(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty `assignments` list is a real, non-error answer -- "nothing in
    this instruction implies restricting anything on this connection" -- not
    the same as the tool never being called at all."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(
            db, tenant, mission="Beantworte Kundenanfragen freundlich.", conn_name="odoo-5"
        )

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(assignments=[])

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret-from-instruction",
                json={"connectionName": "odoo-5"},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"results": []}
