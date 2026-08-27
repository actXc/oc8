"""Tests for the OpenAI device-code start/poll/PKCE-exchange client.

This codebase mocks provider HTTP calls with `httpx.MockTransport` installed
via `oauth_http.set_transport_override` (see `test_tokens.py`'s `_Recorder`),
not `respx` -- `respx` is not a dependency here, so these tests match that
existing convention rather than the plan's respx sketch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from oc8.oauth import device_flow
from oc8.oauth import http as oauth_http
from oc8.oauth.errors import OAuthExchangeFailed
from oc8.oauth.openai_chatgpt_params import (
    DEVICE_AUTHORIZATION_URL,
    DEVICE_POLL_URL,
    TOKEN_URL,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def _transport() -> Iterator[None]:
    yield
    oauth_http.set_transport_override(None)


def _install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
    oauth_http.set_transport_override(httpx.MockTransport(handler))


async def test_start_device_login_parses_json_response(_transport: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DEVICE_AUTHORIZATION_URL
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(
            200, json={"device_auth_id": "da1", "user_code": "ABCD-1234", "interval": "5"}
        )

    _install(handler)
    result = await device_flow.start_device_login()
    assert result.device_auth_id == "da1"
    assert result.user_code == "ABCD-1234"
    assert result.interval == 5  # coerced from the wire string
    assert result.verification_uri == device_flow.VERIFICATION_URI
    assert result.expires_in == device_flow.DEVICE_CODE_EXPIRES_IN_SECONDS


async def test_poll_device_login_pending_on_403(_transport: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DEVICE_POLL_URL
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(403)

    _install(handler)
    result = await device_flow.poll_device_login("da1", "ABCD-1234")
    assert result.status == "pending"
    assert result.tokens is None


async def test_poll_device_login_pending_on_404(_transport: None) -> None:
    _install(lambda request: httpx.Response(404))
    result = await device_flow.poll_device_login("da1", "ABCD-1234")
    assert result.status == "pending"


async def test_poll_device_login_completes_via_pkce_exchange(_transport: None) -> None:
    exchange_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEVICE_POLL_URL:
            return httpx.Response(
                200,
                json={
                    "authorization_code": "authcode1",
                    "code_challenge": "challenge1",
                    "code_verifier": "verifier1",
                },
            )
        if str(request.url) == TOKEN_URL:
            exchange_calls.append(request)
            return httpx.Response(
                200,
                json={
                    "access_token": "at1",
                    "refresh_token": "rt1",
                    "expires_in": 3600,
                    "id_token": "eyJhbGciOiJub25lIn0.e30.",
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")

    _install(handler)
    result = await device_flow.poll_device_login("da1", "ABCD-1234")

    assert result.status == "complete"
    assert result.tokens is not None
    assert result.tokens.access_token == "at1"
    assert len(exchange_calls) == 1
    sent = exchange_calls[0]
    assert sent.headers["content-type"].startswith("application/x-www-form-urlencoded")
    body = dict(httpx.QueryParams(sent.content.decode()))
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "authcode1"
    assert body["code_verifier"] == "verifier1"


async def test_poll_device_login_returns_error_when_pkce_exchange_fails(
    _transport: None,
) -> None:
    """A successful poll whose follow-up PKCE exchange then fails (a
    transient 4xx/5xx from TOKEN_URL, here) must surface as
    DevicePollResult(status="error", ...), not an escaping OAuthExchangeFailed
    -- the whole point of poll_device_login owning both legs is that its
    caller never has to handle the intermediate exchange call failing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DEVICE_POLL_URL:
            return httpx.Response(
                200,
                json={
                    "authorization_code": "authcode1",
                    "code_challenge": "challenge1",
                    "code_verifier": "verifier1",
                },
            )
        if str(request.url) == TOKEN_URL:
            return httpx.Response(500)
        raise AssertionError(f"unexpected request to {request.url}")

    _install(handler)
    result = await device_flow.poll_device_login("da1", "ABCD-1234")

    assert result.status == "error"
    assert result.tokens is None
    assert result.error is not None


async def test_poll_device_login_other_error_is_error_status(_transport: None) -> None:
    _install(lambda request: httpx.Response(410))
    result = await device_flow.poll_device_login("da1", "ABCD-1234")
    assert result.status == "error"


async def test_start_device_login_raises_on_disabled_account_404(_transport: None) -> None:
    _install(lambda request: httpx.Response(404))
    with pytest.raises(OAuthExchangeFailed, match="enable"):
        await device_flow.start_device_login()
