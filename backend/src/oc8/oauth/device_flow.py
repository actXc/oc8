"""OpenAI "Sign in with ChatGPT" device-code HTTP client -- start, poll, and
the PKCE code exchange the poll's success hands off to. See
oc8.oauth.openai_chatgpt_params's module docstring for the full list of ways
this differs from RFC 8628 (it is NOT that flow); this module implements
against those quirks directly rather than any generic device-flow library.

Deliberately does not go through oc8.oauth.providers/oc8.oauth.client --
those are built for the browser-redirect authorization_code shape (a
registered client_secret, a redirect_uri). This flow uses a public
client_id with no secret and no redirect_uri of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from oc8.oauth.errors import OAuthExchangeFailed
from oc8.oauth.http import get_client
from oc8.oauth.openai_chatgpt_params import (
    CLIENT_ID,
    DEVICE_AUTHORIZATION_URL,
    DEVICE_POLL_URL,
    TOKEN_URL,
)
from oc8.oauth.tokens import TokenResponse, _parse

#: Client-side constants -- OpenAI's start response supplies neither (quirk 3
#: of openai_chatgpt_params.py's docstring). The verification page has no
#: query-param-embedded-code variant (no verification_uri_complete exists on
#: this flow), so the UI (Task 12) shows the code for the user to type in.
VERIFICATION_URI = "https://auth.openai.com/codex/device"
DEVICE_CODE_EXPIRES_IN_SECONDS = 15 * 60

#: The device-code-callback redirect_uri the PKCE exchange must send -- not a
#: real callback route (this is a device flow, nothing ever redirects there),
#: but OpenAI's token endpoint validates it's present and matches exactly.
_PKCE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"


@dataclass(frozen=True)
class DeviceLoginStart:
    device_auth_id: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class DevicePollResult:
    status: Literal["pending", "complete", "expired", "error"]
    tokens: TokenResponse | None
    error: str | None


async def start_device_login() -> DeviceLoginStart:
    async with get_client() as client:
        resp = await client.post(DEVICE_AUTHORIZATION_URL, json={"client_id": CLIENT_ID})
    if resp.status_code == 404:
        raise OAuthExchangeFailed(
            "device-code sign-in is not enabled on this ChatGPT account -- enable it under "
            "ChatGPT Settings > Security (or, for a Workspace account, in workspace "
            "permissions) and try again"
        )
    if resp.status_code >= 400:
        raise OAuthExchangeFailed(f"device authorization request failed ({resp.status_code})")
    try:
        body: dict[str, object] = resp.json()
    except ValueError:
        raise OAuthExchangeFailed("device authorization endpoint returned non-JSON") from None
    device_auth_id = body.get("device_auth_id")
    user_code = body.get("user_code")
    interval = body.get("interval")
    if not isinstance(device_auth_id, str) or not isinstance(user_code, str):
        raise OAuthExchangeFailed(
            "device authorization response is missing device_auth_id or user_code"
        )
    if not isinstance(interval, int | float | str):
        raise OAuthExchangeFailed("device authorization response has a non-numeric interval")
    return DeviceLoginStart(
        device_auth_id=device_auth_id,
        user_code=user_code,
        verification_uri=VERIFICATION_URI,
        expires_in=DEVICE_CODE_EXPIRES_IN_SECONDS,
        interval=int(interval),  # wire value is a JSON string, not a number
    )


async def _exchange_pkce_code(*, authorization_code: str, code_verifier: str) -> TokenResponse:
    """The form-encoded leg the poll's success hands off to -- see this
    module's own docstring and openai_chatgpt_params.py's quirk 5."""
    async with get_client() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": _PKCE_REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": code_verifier,
            },
        )
    if resp.status_code >= 400:
        raise OAuthExchangeFailed(f"code exchange failed ({resp.status_code})")
    try:
        body: dict[str, object] = resp.json()
    except ValueError:
        raise OAuthExchangeFailed("token endpoint returned non-JSON on code exchange") from None
    return _parse(body)


async def poll_device_login(device_auth_id: str, user_code: str) -> DevicePollResult:
    """One poll attempt, chaining straight into the PKCE code exchange on
    success so the caller never sees the intermediate authorization_code/
    code_verifier handoff. The caller (Task 5's endpoint) owns the interval
    between calls -- this function never sleeps or loops itself."""
    async with get_client() as client:
        resp = await client.post(
            DEVICE_POLL_URL, json={"device_auth_id": device_auth_id, "user_code": user_code}
        )
    if resp.status_code in (403, 404):
        return DevicePollResult(status="pending", tokens=None, error=None)
    if resp.status_code >= 400:
        return DevicePollResult(status="error", tokens=None, error=f"http {resp.status_code}")
    try:
        body: dict[str, object] = resp.json()
    except ValueError:
        return DevicePollResult(status="error", tokens=None, error="non-JSON poll response")
    authorization_code = body.get("authorization_code")
    code_verifier = body.get("code_verifier")
    if not isinstance(authorization_code, str) or not isinstance(code_verifier, str):
        return DevicePollResult(
            status="error", tokens=None, error="poll response missing PKCE fields"
        )
    try:
        tokens = await _exchange_pkce_code(
            authorization_code=authorization_code, code_verifier=code_verifier
        )
    except OAuthExchangeFailed as exc:
        # The poll itself succeeded -- the caller's contract is
        # pending/complete/expired/error, never an exception escaping this
        # function, so a failure on the follow-up exchange leg (transient
        # 4xx/5xx from TOKEN_URL, a malformed body, a missing access_token)
        # is reported the same way any other poll-time failure is.
        return DevicePollResult(status="error", tokens=None, error=str(exc))
    return DevicePollResult(status="complete", tokens=tokens, error=None)
