from __future__ import annotations

import uuid

import pytest
from asgi_lifespan import LifespanManager
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient

from oc8.api.deps import deny_totp_pending_principals
from oc8.auth import get_identity_provider
from oc8.main import create_app

pytestmark = pytest.mark.asyncio

_MEMBERS = "/api/v1/members"
_ENROLL = "/api/v1/auth/totp/enroll"
_CONFIRM = "/api/v1/auth/totp/confirm"
_VERIFY = "/api/v1/auth/totp/verify"


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _request_for(path: str, token: str) -> Request:
    """A bare `Request` carrying just enough ASGI scope for
    `deny_totp_pending_principals` to read `request.url.path` and the
    Authorization header -- the only two things it looks at.

    Used instead of a real HTTP round-trip for the `/enroll`/`/confirm`/
    `/verify` paths specifically: those routes don't exist yet (Task 5), so a
    real request to them 404s in Starlette's routing BEFORE any router-level
    `dependencies=[...]` ever runs -- confirmed empirically (see fix-round
    commit history): the guard never gets a chance to refuse or admit
    anything, which would make an HTTP-level test of the enroll/challenge
    partition vacuously pass no matter what the allowlists say. Calling the
    dependency directly exercises the exact same code the real mount will run
    once those routes exist, without depending on their existing yet.
    """
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    return Request(scope)


def _totp_token(scope: str) -> str:
    return get_identity_provider().mint(
        tenant_id=uuid.uuid4(),
        subject="pending@example.com",
        role="org_admin",
        scopes=[scope],
    )


def _agent_token() -> str:
    return get_identity_provider().mint(
        tenant_id=uuid.uuid4(),
        subject=f"agent:{uuid.uuid4()}",
        role="agent_default",
        kind="agent",
        scopes=[f"run:{uuid.uuid4()}"],
    )


def _assert_not_blocked_by_either_router_guard(status_code: int, body: dict[str, object]) -> None:
    """Neither `deny_totp_pending_principals` nor `deny_agent_principals` short-
    circuited this request. Not asserting 200 -- the tenant/member in these
    tests doesn't really exist, so downstream lookup/permission logic may
    still refuse the request for unrelated reasons. What this proves is that
    a 403, if one comes back, is NOT either guard's own refusal message.
    """
    if status_code == 403:
        detail = str(body.get("detail", "")).lower()
        assert "totp" not in detail
        assert "agent token" not in detail
        assert "challenge" not in detail


async def test_an_enroll_scoped_token_is_refused_on_a_real_endpoint() -> None:
    token = _totp_token("totp:enroll")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.get(_MEMBERS, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403, r.text


async def test_a_challenge_scoped_token_is_refused_on_a_real_endpoint() -> None:
    token = _totp_token("totp:challenge")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.get(_MEMBERS, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403, r.text


async def test_an_ordinary_token_with_no_totp_scope_is_unaffected() -> None:
    tenant = uuid.uuid4()
    token = get_identity_provider().mint(
        tenant_id=tenant,
        subject="ordinary@example.com",
        role="org_admin",
    )
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.get(_MEMBERS, headers={"Authorization": f"Bearer {token}"})
            _assert_not_blocked_by_either_router_guard(r.status_code, r.json())


# --- Fix round 1, finding 1 (CRITICAL): header-whitespace bypass ------------
#
# `get_authorization_scheme_param` (FastAPI's own parser, used downstream by
# `HTTPBearer`/`get_principal`) strips whitespace around the token; a
# hand-rolled `header.partition(" ")` does not. `Authorization: Bearer  <t>`
# (TWO spaces) used to hand both router guards a leading-space-corrupted
# token, which failed `verify()` and made each guard's `except InvalidToken:
# return` FAIL OPEN -- while the identical request, resolved correctly by
# `HTTPBearer` a few lines later, verified fine and authenticated normally.
# These prove the two-space form is refused exactly like the one-space form,
# for every principal kind either guard is responsible for.


async def test_two_space_header_still_refuses_an_agent_token() -> None:
    token = _agent_token()
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.get(_MEMBERS, headers={"Authorization": f"Bearer  {token}"})
            assert r.status_code == 403, r.text
            # Asserted on the exact detail, not just the status code: with the
            # guard bypassed (partition(" ") corrupting the two-space token),
            # this same request still gets refused 403 by the unrelated
            # downstream permission layer (agent_default lacks member:view),
            # so a bare status-code check can't tell "guard fired" from
            # "guard bypassed, something else refused it anyway".
            assert r.json()["detail"] == "an agent token may not use the operator API"


async def test_two_space_header_still_refuses_an_enroll_scoped_token() -> None:
    token = _totp_token("totp:enroll")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.get(_MEMBERS, headers={"Authorization": f"Bearer  {token}"})
            assert r.status_code == 403, r.text


async def test_two_space_header_still_refuses_a_challenge_scoped_token() -> None:
    token = _totp_token("totp:challenge")
    app = create_app()
    async with LifespanManager(app):
        async with _client(app) as c:
            r = await c.get(_MEMBERS, headers={"Authorization": f"Bearer  {token}"})
            assert r.status_code == 403, r.text


# --- Fix round 1, finding 4 (MEDIUM): enroll/challenge must not share a door -
#
# A `totp:challenge` token proves a password only -- no second factor yet --
# so it must be confined to `/verify` alone; it must NOT reach `/enroll` or
# `/confirm` and register a brand new authenticator. Symmetrically, an
# `totp:enroll` token must not be able to reach `/verify`. Exercised via
# `_request_for` / direct dependency invocation -- see its docstring for why
# an HTTP round-trip to these not-yet-existing routes can't test this.


async def test_a_challenge_scoped_token_is_refused_on_enroll() -> None:
    token = _totp_token("totp:challenge")
    with pytest.raises(HTTPException) as exc_info:
        await deny_totp_pending_principals(_request_for(_ENROLL, token))
    assert exc_info.value.status_code == 403
    assert "challenge" in str(exc_info.value.detail).lower()


async def test_a_challenge_scoped_token_is_refused_on_confirm() -> None:
    token = _totp_token("totp:challenge")
    with pytest.raises(HTTPException) as exc_info:
        await deny_totp_pending_principals(_request_for(_CONFIRM, token))
    assert exc_info.value.status_code == 403
    assert "challenge" in str(exc_info.value.detail).lower()


async def test_an_enroll_scoped_token_is_refused_on_verify() -> None:
    token = _totp_token("totp:enroll")
    with pytest.raises(HTTPException) as exc_info:
        await deny_totp_pending_principals(_request_for(_VERIFY, token))
    assert exc_info.value.status_code == 403
    assert "enrollment" in str(exc_info.value.detail).lower()


# --- Fix round 1, finding 5 (LOW): the allowlist must be positively checked -
#
# The original test only proved the guard doesn't crash; it never confirmed a
# correctly-scoped token can actually reach its own allowed path(s). An
# allowlist typo (e.g. a stray trailing slash) would have been invisible
# until Task 5's endpoints exist. These call the dependency directly (same
# reason as the partition tests above) and assert it returns normally --
# i.e. does NOT raise -- for every scope/path pairing the allowlists are
# supposed to admit.


async def test_an_enroll_scoped_token_can_reach_enroll_and_confirm() -> None:
    token = _totp_token("totp:enroll")
    for path in (_ENROLL, _CONFIRM):
        # Returns None / raises nothing -- that IS the assertion.
        await deny_totp_pending_principals(_request_for(path, token))


async def test_a_challenge_scoped_token_can_reach_verify() -> None:
    token = _totp_token("totp:challenge")
    await deny_totp_pending_principals(_request_for(_VERIFY, token))
