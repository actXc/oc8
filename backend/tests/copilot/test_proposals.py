from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from oc8 import models as m
from oc8.auth import Principal
from oc8.config import get_settings

pytestmark = pytest.mark.asyncio

# tests/copilot/<this file> -> tests -> backend -> repo root, where capas/ lives.
_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"


@pytest.fixture(autouse=True)
def _plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(_PLUGINS_DIR))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _actor(tenant: uuid.UUID) -> Principal:
    return Principal(subject="operator-1", tenant_id=tenant, role="org_admin")


async def test_proposal_applies_once_and_redacts_secret_from_durable_state(
    app_session, acme_tenant
) -> None:
    """Removing the capability allowlist would persist a submitted secret."""
    from oc8.copilot.proposals import create_proposal

    secret = "SENTINEL-DO-NOT-PERSIST"
    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        with pytest.raises(ValueError) as rejected:
            await create_proposal(
                db,
                actor,
                [
                    {
                        "type": "integration.prepare",
                        "integrationId": str(uuid.uuid4()),
                        "secret": secret,
                    }
                ],
            )
        assert secret not in str(rejected.value)


async def test_mission_proposal_applies_once_and_expires_when_target_changes(
    app_session, acme_tenant
) -> None:
    """Removing the revision comparison would apply a superseded proposal."""
    from oc8.copilot.proposals import ProposalRejected, apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        agent = m.Agent(
            tenant_id=acme_tenant,
            department_id=uuid.uuid4(),
            name="Copilot target",
            mission="old",
        )
        db.add(agent)
        await db.flush()
        proposal = await create_proposal(
            db,
            actor,
            [{"type": "agent.mission.set", "agentId": str(agent.id), "mission": "new"}],
        )
        result = await apply_proposal(db, proposal.id, actor)
        assert result.status == "applied"
        assert agent.mission == "new"
        with pytest.raises(ProposalRejected):
            await apply_proposal(db, proposal.id, actor)

        stale = await create_proposal(
            db,
            actor,
            [{"type": "agent.mission.set", "agentId": str(agent.id), "mission": "stale"}],
        )
        agent.mission = "changed outside proposal"
        await db.flush()
        with pytest.raises(ProposalRejected):
            await apply_proposal(db, stale.id, actor)
        assert stale.status == "expired"


async def test_guardrail_set_requires_approval_for_the_one_real_case(
    app_session, acme_tenant
) -> None:
    """The Assignment: a support agent's `post_message` (an outward, customer-
    facing reply -- the one real "no external message without my approval"
    case) can be switched to require approval through the Copilot proposal
    pipeline, without touching any other odoo function."""
    from oc8.authz import pdp
    from oc8.copilot.proposals import apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        department = m.Department(
            tenant_id=acme_tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=acme_tenant, department_id=department.id, name="Sina")
        conn = m.McpConnection(
            tenant_id=acme_tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()

        proposal = await create_proposal(
            db,
            actor,
            [
                {
                    "type": "agent.guardrail.set",
                    "agentId": str(agent.id),
                    "connectionName": "odoo",
                    "function": "post_message",
                    "decision": "approval_required",
                }
            ],
        )
        result = await apply_proposal(db, proposal.id, actor)
        assert result.status == "applied"

        tools = agent.narrowing["tools"]
        assert tools["odoo"]["approval_actions"] == ["post_message"]
        assert tools["odoo"]["only"] is None or "post_message" in tools["odoo"]["only"]

        policies = pdp.effective_tool_policies(department.frame, agent.narrowing)
        decision = pdp.authorize_tool_call(
            policies=policies,
            connection_key="odoo",
            right="modify",
            value=None,
            tool="post_message",
        )
        assert decision.effect is pdp.Effect.REQUIRE_APPROVAL

        # Every other odoo function is untouched.
        decision = pdp.authorize_tool_call(
            policies=policies,
            connection_key="odoo",
            right="modify",
            value=None,
            tool="create_record",
        )
        assert decision.effect is pdp.Effect.ALLOW


async def test_guardrail_set_rejects_an_unknown_function(app_session, acme_tenant) -> None:
    """Fail-closed: a made-up function name must never silently produce a
    broken or no-op guardrail."""
    from oc8.copilot.proposals import ProposalRejected, apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        department = m.Department(
            tenant_id=acme_tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=acme_tenant, department_id=department.id, name="Sina")
        conn = m.McpConnection(
            tenant_id=acme_tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()

        proposal = await create_proposal(
            db,
            actor,
            [
                {
                    "type": "agent.guardrail.set",
                    "agentId": str(agent.id),
                    "connectionName": "odoo",
                    "function": "not_a_real_tool",
                    "decision": "approval_required",
                }
            ],
        )
        with pytest.raises(ProposalRejected):
            await apply_proposal(db, proposal.id, actor)


async def test_guardrail_set_applies_a_with_limits_condition_using_the_declared_attribute(
    app_session, acme_tenant
) -> None:
    """The 4-state model's third state: `create_record` stays self-serve up
    to a threshold on the CAPA-declared `order_value` attribute, above which
    it needs approval -- proving `GuardrailSet` writes structured
    `Condition`s through the exact same `evaluate_conditions` path the
    manual editor and the free-text interpreter both feed."""
    from oc8.authz import pdp
    from oc8.copilot.proposals import apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        department = m.Department(
            tenant_id=acme_tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=acme_tenant, department_id=department.id, name="Sina")
        conn = m.McpConnection(
            tenant_id=acme_tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()

        proposal = await create_proposal(
            db,
            actor,
            [
                {
                    "type": "agent.guardrail.set",
                    "agentId": str(agent.id),
                    "connectionName": "odoo",
                    "function": "create_record",
                    "decision": "with_limits",
                    "conditions": [
                        {
                            "attribute": "order_value",
                            "datatype": "number",
                            "operator": ">",
                            "value": 5000,
                            "then": "require_approval",
                        }
                    ],
                }
            ],
        )
        result = await apply_proposal(db, proposal.id, actor)
        assert result.status == "applied"

        policies = pdp.effective_tool_policies(department.frame, agent.narrowing)
        under = pdp.authorize_tool_call(
            policies=policies,
            connection_key="odoo",
            right="modify",
            value=None,
            tool="create_record",
            attributes={"order_value": 1000},
        )
        assert under.effect is pdp.Effect.ALLOW
        over = pdp.authorize_tool_call(
            policies=policies,
            connection_key="odoo",
            right="modify",
            value=None,
            tool="create_record",
            attributes={"order_value": 12480},
        )
        assert over.effect is pdp.Effect.REQUIRE_APPROVAL


async def test_guardrail_set_rejects_an_attribute_the_capa_does_not_declare(
    app_session, acme_tenant
) -> None:
    """Fail-closed, same as an unknown function: the Copilot must never be
    able to write an unenforceable condition on a made-up attribute name."""
    from oc8.copilot.proposals import ProposalRejected, apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        department = m.Department(
            tenant_id=acme_tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=acme_tenant, department_id=department.id, name="Sina")
        conn = m.McpConnection(
            tenant_id=acme_tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()

        proposal = await create_proposal(
            db,
            actor,
            [
                {
                    "type": "agent.guardrail.set",
                    "agentId": str(agent.id),
                    "connectionName": "odoo",
                    "function": "create_record",
                    "decision": "with_limits",
                    "conditions": [
                        {
                            "attribute": "not_a_real_attribute",
                            "datatype": "number",
                            "operator": ">",
                            "value": 5000,
                            "then": "require_approval",
                        }
                    ],
                }
            ],
        )
        with pytest.raises(ProposalRejected):
            await apply_proposal(db, proposal.id, actor)


async def test_guardrail_set_preserves_an_existing_conditions_rule_on_another_function(
    app_session, acme_tenant
) -> None:
    """The bug this migration fixes: applying a guardrail to `post_message`
    must never silently drop `create_record`'s already-saved `with_limits`
    condition -- `_apply_guardrail_set` used to construct a fresh
    `ToolPolicy` without threading `current.conditions` through at all."""
    from oc8.authz import pdp
    from oc8.copilot.proposals import apply_proposal, create_proposal

    actor = _actor(acme_tenant)
    async with app_session(acme_tenant) as db:
        department = m.Department(
            tenant_id=acme_tenant,
            name=f"D-{uuid.uuid4().hex}",
            frame={"tools": {"odoo": {"enabled": True, "read": True, "modify": True}}},
        )
        db.add(department)
        await db.flush()
        agent = m.Agent(tenant_id=acme_tenant, department_id=department.id, name="Sina")
        conn = m.McpConnection(
            tenant_id=acme_tenant,
            name="odoo",
            server_url="",
            transport="stdio",
            config={"_plugin_name": "odoo_mcp", "_connection_key": "primary"},
        )
        db.add_all([agent, conn])
        await db.flush()

        first = await create_proposal(
            db,
            actor,
            [
                {
                    "type": "agent.guardrail.set",
                    "agentId": str(agent.id),
                    "connectionName": "odoo",
                    "function": "create_record",
                    "decision": "with_limits",
                    "conditions": [
                        {
                            "attribute": "order_value",
                            "datatype": "number",
                            "operator": ">",
                            "value": 5000,
                            "then": "require_approval",
                        }
                    ],
                }
            ],
        )
        assert (await apply_proposal(db, first.id, actor)).status == "applied"

        second = await create_proposal(
            db,
            actor,
            [
                {
                    "type": "agent.guardrail.set",
                    "agentId": str(agent.id),
                    "connectionName": "odoo",
                    "function": "post_message",
                    "decision": "approval_required",
                }
            ],
        )
        assert (await apply_proposal(db, second.id, actor)).status == "applied"

        policies = pdp.effective_tool_policies(department.frame, agent.narrowing)
        over = pdp.authorize_tool_call(
            policies=policies,
            connection_key="odoo",
            right="modify",
            value=None,
            tool="create_record",
            attributes={"order_value": 12480},
        )
        assert over.effect is pdp.Effect.REQUIRE_APPROVAL
        assert (
            pdp.authorize_tool_call(
                policies=policies,
                connection_key="odoo",
                right="modify",
                value=None,
                tool="post_message",
            ).effect
            is pdp.Effect.REQUIRE_APPROVAL
        )
