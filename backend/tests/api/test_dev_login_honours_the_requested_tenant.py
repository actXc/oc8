"""The dev login mints for the tenant that was asked for.

`DevLoginRequest` is a plain `BaseModel` with a snake_case `tenant_id`
(`api/v1/auth.py:29`), while the frontend -- like every other body in this API --
sends camelCase. Pydantic ignores the unknown key and falls back to the default,
so **every dev token is minted for ACME** whatever was clicked in the tenant
picker.

That is not a cosmetic bug for this slice: it makes the whole feature
un-QA-able through the browser. A seat granted in another tenant's Vertrieb is
invisible to a token minted for ACME, and the screen under test shows an empty
queue that looks exactly like the failure it is meant to distinguish.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8.auth import get_identity_provider
from oc8.constants import ACME_TENANT_ID
from oc8.main import create_app

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


async def test_posting_camel_tenant_id_mints_for_that_tenant() -> None:
    wanted = uuid.uuid4()
    assert wanted != uuid.UUID(str(ACME_TENANT_ID))

    async with _http() as http:
        got = await http.post(
            "/api/v1/auth/dev-login",
            json={"tenantId": str(wanted), "role": "member", "subject": "hos"},
        )
    assert got.status_code == 200, got.text
    body = got.json()

    # The TOKEN, because that is what every later request is judged on -- the
    # echoed principal is a convenience and could agree with the request while
    # the token disagreed.
    verified = get_identity_provider().verify(body["token"])
    assert verified.tenant_id == wanted, (
        f"asked for {wanted}, was minted for {verified.tenant_id} -- the "
        f"camelCase key was dropped and the default won"
    )
    assert verified.role == "member"
    assert verified.subject == "hos"

    # `TokenResponse` is a plain BaseModel and this slice does not change it, so
    # read the echoed principal under either spelling rather than pinning a
    # serialization this test has no opinion about.
    echoed = body["principal"]
    assert (echoed.get("tenantId") or echoed.get("tenant_id")) == str(wanted)
    assert echoed["role"] == "member"


async def test_the_defaults_are_unchanged_for_a_body_that_says_nothing() -> None:
    """A CamelModel with `populate_by_name` must not become a breaking change for
    the callers already posting nothing at all."""
    async with _http() as http:
        got = await http.post("/api/v1/auth/dev-login", json={})
    assert got.status_code == 200, got.text
    verified = get_identity_provider().verify(got.json()["token"])
    assert verified.tenant_id == uuid.UUID(str(ACME_TENANT_ID))
    assert verified.role == "org_admin"
