from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app
from oc8.metering.usage import record_usage
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _token(tenant: uuid.UUID) -> str:
    return get_identity_provider().mint(tenant_id=tenant, subject="u", role="org_admin")


async def test_department_budget_upsert_and_list() -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept_id = str(uuid.uuid4())
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            created = await client.put(
                "/api/v1/budgets",
                json={"departmentId": dept_id, "softLimitTokens": 1000, "hardLimitTokens": 2000},
                headers=headers,
            )
            assert created.status_code == 200
            budget_id = created.json()["id"]

            updated = await client.put(
                "/api/v1/budgets",
                json={"departmentId": dept_id, "softLimitTokens": 1500, "hardLimitTokens": 3000},
                headers=headers,
            )
            assert updated.status_code == 200
            assert updated.json()["id"] == budget_id  # upsert, not a new row
            assert updated.json()["hardLimitTokens"] == 3000

            listed = await client.get("/api/v1/budgets", headers=headers)
            assert listed.status_code == 200
            assert any(b["id"] == budget_id for b in listed.json())


async def test_department_budget_status_reflects_usage(app_session: AppSessionFactory) -> None:
    tenant = uuid.UUID(str(ACME_TENANT_ID))
    dept_id = uuid.uuid4()
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}

    async with app_session(tenant) as db:
        await record_usage(
            db, tenant_id=tenant, request_id=uuid.uuid4(), model="m", provider="p",
            tokens_in=40, tokens_out=10, department_id=dept_id,
        )

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await client.put(
                "/api/v1/budgets",
                json={
                    "departmentId": str(dept_id),
                    "softLimitTokens": 50,
                    "hardLimitTokens": 1000,
                },
                headers=headers,
            )
            status = await client.get(
                "/api/v1/budgets/status",
                params={"department_id": str(dept_id)},
                headers=headers,
            )
            assert status.status_code == 200
            body = status.json()
            assert body["scope"] == "department"
            assert body["currentTokens"] == 50
            assert body["softExceeded"] is True
            assert body["hardExceeded"] is False


async def test_tenant_wide_budget_upsert_and_status() -> None:
    # Fresh tenant -- see the plan's Global Constraints test-collision hazard note.
    tenant = uuid.uuid4()
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            created = await client.put(
                "/api/v1/budgets",
                json={"departmentId": None, "softLimitTokens": None, "hardLimitTokens": 500},
                headers=headers,
            )
            assert created.status_code == 200
            assert created.json()["departmentId"] is None

            status = await client.get("/api/v1/budgets/status", headers=headers)
            assert status.status_code == 200
            assert status.json()["scope"] == "tenant"
            assert status.json()["hardLimitTokens"] == 500


async def test_dollar_budget_converts_and_stores_reference() -> None:
    tenant = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            headers = {"Authorization": f"Bearer {_token(tenant)}"}
            r = await client.put("/api/v1/budgets", json={"dollarBudgetUsd": 90.0}, headers=headers)
            # No reference model exists for a brand-new tenant -- expected reject.
            assert r.status_code == 422, r.text


async def test_switching_from_dollar_to_token_mode_clears_dollar_fields(
    app_session: AppSessionFactory,
) -> None:
    """Regression for the bug where a $ budget could never be switched back
    to token mode: the 3 dollar_* fields must be cleared, not left stale,
    once the caller explicitly saves token limits instead."""
    from oc8 import models as m

    tenant = uuid.uuid4()
    app = create_app()
    headers = {"Authorization": f"Bearer {_token(tenant)}"}

    # Seed a Copilot-flagged model so $ -> token conversion has a reference.
    async with app_session(tenant) as db:
        db.add(
            m.ModelPrice(
                provider="anthropic",
                model_pattern="claudesonnet4",
                price_in_usd_per_1m=3.0,
                price_out_usd_per_1m=15.0,
            )
        )
        db.add(
            m.ModelConfig(
                tenant_id=tenant,
                provider="anthropic",
                model="claude-sonnet-4",
                locality="cloud",
                used_by_copilot=True,
            )
        )
        await db.flush()  # avoid mid-block commit's GUC reset (see other tests' note)

    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            dollar_mode = await client.put(
                "/api/v1/budgets", json={"dollarBudgetUsd": 90.0}, headers=headers
            )
            assert dollar_mode.status_code == 200, dollar_mode.text
            body = dollar_mode.json()
            assert body["dollarBudgetUsd"] == 90.0
            assert body["dollarReferenceProvider"] == "anthropic"
            assert body["dollarReferenceModel"] == "claude-sonnet-4"

            token_mode = await client.put(
                "/api/v1/budgets",
                json={"softLimitTokens": 1000, "hardLimitTokens": 2000},
                headers=headers,
            )
            assert token_mode.status_code == 200, token_mode.text
            token_body = token_mode.json()
            assert token_body["hardLimitTokens"] == 2000
            assert token_body["dollarBudgetUsd"] is None
            assert token_body["dollarReferenceProvider"] is None
            assert token_body["dollarReferenceModel"] is None

            listed = await client.get("/api/v1/budgets", headers=headers)
            assert listed.status_code == 200
            row = next(b for b in listed.json() if b["id"] == token_body["id"])
            assert row["dollarBudgetUsd"] is None
            assert row["dollarReferenceProvider"] is None
            assert row["dollarReferenceModel"] is None
