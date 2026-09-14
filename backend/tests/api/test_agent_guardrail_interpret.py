"""`POST /agents/{id}/guardrails/interpret` -- a single-shot, non-conversational
translation of a free-text guardrail definition into the generic 4-state
decision (`self_sufficient`/`with_limits`/`approval_required`/`not_allowed`)
plus structured `Condition`s for `with_limits`. Never writes anything: only
ever a pre-filled suggestion the operator still confirms through the existing
`PUT /agents/{id}/narrowing`.
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
        tool_calls=[ToolCall(id="1", name="set_guardrail_interpretation", arguments=arguments)],
        usage=Usage(tokens_in=10, tokens_out=5),
        stop_reason="tool_calls",
        provider="ollama",
        model="mistral:latest",
    )


async def _agent_on(
    db: Any, tenant: uuid.UUID, *, plugin: str = "odoo_mcp", conn_name: str = "odoo"
) -> uuid.UUID:
    """`conn_name` must be unique per test -- `ACME_TENANT_ID` is a shared
    fixture tenant, and connection lookup picks the earliest-created row for
    a given (tenant, name), so reusing a name across tests would silently
    resolve to a *different* test's connection/plugin."""
    department = m.Department(
        tenant_id=tenant,
        name=f"D-{uuid.uuid4().hex}",
        frame={"tools": {conn_name: {"enabled": True, "read": True, "modify": True}}},
    )
    db.add(department)
    await db.flush()
    agent = m.Agent(tenant_id=tenant, department_id=department.id, name="Sina")
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


async def test_a_with_limits_definition_becomes_a_structured_condition(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's own worked example ("Nur bis 100 Stück selbstständig,
    darüber Freigabe" -> quantity <= 100 -> allow, else -> approval) maps
    onto `order_value`, the one numeric attribute odoo_mcp actually declares
    for `create_record` -- the model reports the structured rule, this
    endpoint never touches free text again after that."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(db, tenant, conn_name="odoo-1")

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(
            decision="with_limits",
            conditions=[
                {
                    "attribute": "order_value",
                    "operator": ">",
                    "value": 500,
                    "then": "require_approval",
                }
            ],
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret",
                json={
                    "connectionName": "odoo-1",
                    "function": "create_record",
                    "definition": "Freigabe ab 500€",
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {
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


async def test_a_not_allowed_definition_is_understood(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(db, tenant, conn_name="odoo-2")

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(decision="not_allowed", conditions=[])

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret",
                json={
                    "connectionName": "odoo-2",
                    "function": "delete_record",
                    "definition": "darf nie automatisch löschen",
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"decision": "not_allowed", "conditions": []}


async def test_a_non_numeric_condition_is_understood(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the interpreter is genuinely generic, not a euro special case:
    odoo_mcp's `model` attribute (a string) works the same way `order_value`
    does."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(db, tenant, conn_name="odoo-3")

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(
            decision="with_limits",
            conditions=[
                {
                    "attribute": "model",
                    "operator": "==",
                    "value": "helpdesk.ticket",
                    "then": "deny",
                }
            ],
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret",
                json={
                    "connectionName": "odoo-3",
                    "function": "create_record",
                    "definition": "helpdesk.ticket nie automatisch anlegen",
                },
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json() == {
                "decision": "with_limits",
                "conditions": [
                    {
                        "attribute": "model",
                        "datatype": "string",
                        "operator": "==",
                        "value": "helpdesk.ticket",
                        "then": "deny",
                    }
                ],
            }


async def test_with_limits_is_rejected_when_the_connection_has_no_attributes(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed, not fail-safe here: a connection with no declared
    `GuardrailAttribute`s has nothing a Condition could reference, so even if
    the model reaches for `with_limits` anyway (it's told not to, but nothing
    stops a bad response) it must never reach the operator as a real
    suggestion."""
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(db, tenant, plugin="github_mcp", conn_name="github-1")

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return _tool_call_result(
            decision="with_limits",
            conditions=[
                {"attribute": "order_value", "operator": ">", "value": 500, "then": "deny"}
            ],
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret",
                json={
                    "connectionName": "github-1",
                    "function": "merge_pull_request",
                    "definition": "Freigabe ab 500€",
                },
                headers=headers,
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["error"] == "guardrail_not_understood"


async def test_a_definition_the_model_cannot_map_fails_closed(
    app_session: AppSessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    async with app_session(tenant) as db:
        agent_id = await _agent_on(db, tenant, conn_name="odoo-4")

    async def fake_complete(*a: object, **kw: object) -> CompletionResult:
        return CompletionResult(
            text="I'm not sure what you mean.",
            tool_calls=[],
            usage=Usage(tokens_in=10, tokens_out=5),
            stop_reason="stop",
            provider="ollama",
            model="mistral:latest",
        )

    monkeypatch.setattr("oc8.copilot.guardrail_interpret.complete_with_fallback", fake_complete)

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.post(
                f"/api/v1/agents/{agent_id}/guardrails/interpret",
                json={
                    "connectionName": "odoo-4",
                    "function": "post_message",
                    "definition": "nie am Wochenende senden",
                },
                headers=headers,
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["error"] == "guardrail_not_understood"
            assert "nie am Wochenende" not in r.text
