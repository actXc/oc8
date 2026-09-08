"""`_verify_activity_jwt` -- Bot Framework's own inbound auth scheme: a
bearer JWT signed by a key published at Microsoft's JWKS endpoint. Every
case here must fail closed: a malformed token, an unknown key id, a wrong
audience, an expired signature, a signature from the wrong key, or a
`serviceUrl` the token does not itself claim must all come back None/False,
the same discipline Telegram's/WhatsApp's own verify_inbound already follow."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _evict() -> None:
    """Drop any cached `channel` module before/after each test -- `channel/`
    is a shared folder name across plugins (telegram_approvals and
    whatsapp_approvals both ship one too), and sys.modules is keyed by NAME,
    not path."""
    for _stale in [n for n in sys.modules if n == "channel" or n.startswith("channel.")]:
        del sys.modules[_stale]


@pytest.fixture(autouse=True)
def _plugin_path() -> Iterator[None]:
    _evict()
    sys.path.insert(0, str(PLUGIN_ROOT))
    yield
    sys.path.remove(str(PLUGIN_ROOT))
    _evict()


@pytest.fixture(autouse=True)
def _reset_jwks_cache() -> Iterator[None]:
    """`_jwks_cache`/`_jwks_cache_at`/`_jwks_forced_refresh_at` are
    module-level mutable state; without resetting them, one test's cached keys
    -- or its spent forced-refresh budget -- would leak into the next."""
    import channel.channel as channel_module

    def _reset() -> None:
        channel_module._jwks_cache = {}
        channel_module._jwks_cache_at = 0.0
        channel_module._jwks_forced_refresh_at = 0.0

    _reset()
    yield
    _reset()


def _keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk_for(private_key: rsa.RSAPrivateKey, *, kid: str) -> dict[str, Any]:
    public_jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = kid
    public_jwk["use"] = "sig"
    public_jwk["alg"] = "RS256"
    return public_jwk


#: The `serviceUrl` Bot Framework stamps into both the token and the Activity
#: body. Every outbound call this channel later makes goes to this host.
_SERVICE_URL = "https://smba.trafficmanager.net/teams/"


def _token_for(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    audience: str,
    issuer: str = "https://api.botframework.com",
    exp_delta: int = 3600,
    service_url: str | None = _SERVICE_URL,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + exp_delta,
    }
    if service_url is not None:
        payload["serviceUrl"] = service_url
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def _body(service_url: str | None = _SERVICE_URL) -> bytes:
    activity: dict[str, Any] = {"type": "message"}
    if service_url is not None:
        activity["serviceUrl"] = service_url
    return json.dumps(activity).encode()


def _install_jwks(monkeypatch: pytest.MonkeyPatch, jwks: dict[str, Any]) -> None:
    import channel.channel as channel_module

    async def fake_fetch() -> dict[str, Any]:
        return jwks

    monkeypatch.setattr(channel_module, "_fetch_jwks", fake_fetch)


@pytest.mark.asyncio
async def test_a_validly_signed_token_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    _install_jwks(monkeypatch, jwks)
    token = _token_for(key, kid="k1", audience="app-1")

    claims = await _verify_activity_jwt(token, app_id="app-1")
    assert claims is not None
    # The CLAIMS come back, not a bool: verify_inbound has to compare the
    # token's own serviceUrl against the (unsigned) Activity body's.
    assert claims["serviceUrl"] == _SERVICE_URL


@pytest.mark.asyncio
async def test_an_expired_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    _install_jwks(monkeypatch, jwks)
    token = _token_for(key, kid="k1", audience="app-1", exp_delta=-3600)

    assert await _verify_activity_jwt(token, app_id="app-1") is None


@pytest.mark.asyncio
async def test_a_token_for_the_wrong_audience_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    _install_jwks(monkeypatch, jwks)
    token = _token_for(key, kid="k1", audience="some-other-app")

    assert await _verify_activity_jwt(token, app_id="app-1") is None


@pytest.mark.asyncio
async def test_a_token_signed_by_the_wrong_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The header claims a `kid` that IS in the JWKS, but the signature was
    made with a different private key entirely -- the shape of a forged or
    tampered token, not just an unknown key id."""
    from channel.channel import _verify_activity_jwt

    published_key = _keypair()
    forged_key = _keypair()
    jwks = {"keys": [_jwk_for(published_key, kid="k1")]}
    _install_jwks(monkeypatch, jwks)
    token = _token_for(forged_key, kid="k1", audience="app-1")

    assert await _verify_activity_jwt(token, app_id="app-1") is None


@pytest.mark.asyncio
async def test_an_unknown_key_id_refreshes_once_then_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `kid` the cached JWKS has never seen retries once (the key may have
    rotated since the cache was built) before giving up."""
    import channel.channel as channel_module
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    fetch_calls = 0

    async def counting_fetch() -> dict[str, Any]:
        nonlocal fetch_calls
        fetch_calls += 1
        return jwks

    monkeypatch.setattr(channel_module, "_fetch_jwks", counting_fetch)
    token = _token_for(key, kid="never-published", audience="app-1")

    assert await _verify_activity_jwt(token, app_id="app-1") is None
    assert fetch_calls == 2, "one fetch to populate the cache, one forced refresh on the miss"


@pytest.mark.asyncio
async def test_repeated_unknown_key_ids_cannot_drive_a_fetch_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`jwt.get_unverified_header` verifies nothing, so an unauthenticated
    caller can put any `kid` they like in a header and -- before the rate
    limit -- make oc8 issue one outbound HTTPS GET to login.botframework.com
    for every request they send, TTL or no TTL. The second call here must
    reuse what the first already fetched."""
    import channel.channel as channel_module
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    fetch_calls = 0

    async def counting_fetch() -> dict[str, Any]:
        nonlocal fetch_calls
        fetch_calls += 1
        return jwks

    monkeypatch.setattr(channel_module, "_fetch_jwks", counting_fetch)
    token = _token_for(key, kid="never-published", audience="app-1")

    assert await _verify_activity_jwt(token, app_id="app-1") is None
    assert await _verify_activity_jwt(token, app_id="app-1") is None

    assert fetch_calls == 2, "the second request's forced refresh must be inside the rate limit"


@pytest.mark.asyncio
async def test_a_forced_refresh_is_allowed_again_once_the_interval_has_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The floor must not turn into a permanent block: a key really rotating
    half an hour after someone probed us has to still be picked up."""
    import channel.channel as channel_module
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    fetch_calls = 0

    async def counting_fetch() -> dict[str, Any]:
        nonlocal fetch_calls
        fetch_calls += 1
        return jwks

    class _Clock:
        """Stands in for the `time` MODULE inside channel.channel only --
        patching the real `time.monotonic` would move the clock for asyncio
        and pytest too."""

        now = 1000.0

        def monotonic(self) -> float:
            return self.now

    clock = _Clock()
    monkeypatch.setattr(channel_module, "_fetch_jwks", counting_fetch)
    monkeypatch.setattr(channel_module, "time", clock)
    token = _token_for(key, kid="never-published", audience="app-1")

    assert await _verify_activity_jwt(token, app_id="app-1") is None
    assert fetch_calls == 2

    clock.now += channel_module._JWKS_FORCED_REFRESH_MIN_INTERVAL_SECONDS + 1
    assert await _verify_activity_jwt(token, app_id="app-1") is None
    assert fetch_calls == 3, "past the interval, a genuine rotation must still be fetched"


@pytest.mark.asyncio
async def test_an_unreachable_jwks_endpoint_fails_closed_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ApprovalChannel.verify_inbound` promises a bool, and the webhook route
    (api/v1/channels.py) wraps the call in no try/except at all -- so an httpx
    error escaping from here turned a transient login.botframework.com outage
    into a 500 with a traceback instead of the documented "not verified"."""
    import channel.channel as channel_module
    from channel.channel import TeamsChannel, _verify_activity_jwt

    key = _keypair()

    async def failing_fetch() -> dict[str, Any]:
        raise httpx.ConnectError("login.botframework.com is unreachable")

    monkeypatch.setattr(channel_module, "_fetch_jwks", failing_fetch)
    token = _token_for(key, kid="k1", audience="app-1")

    assert await _verify_activity_jwt(token, app_id="app-1") is None

    channel = TeamsChannel(app_id="app-1", app_password="pw")
    verified = await channel.verify_inbound(
        headers={"Authorization": f"Bearer {token}"}, body=_body()
    )
    assert verified is False


@pytest.mark.asyncio
async def test_a_jwks_endpoint_answering_5xx_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_fetch_jwks`'s own `raise_for_status()`, from inside the fetch."""
    import channel.channel as channel_module
    from channel.channel import _verify_activity_jwt

    key = _keypair()

    async def failing_fetch() -> dict[str, Any]:
        request = httpx.Request("GET", channel_module._JWKS_URL)
        raise httpx.HTTPStatusError(
            "503", request=request, response=httpx.Response(503, request=request)
        )

    monkeypatch.setattr(channel_module, "_fetch_jwks", failing_fetch)
    token = _token_for(key, kid="k1", audience="app-1")

    assert await _verify_activity_jwt(token, app_id="app-1") is None


@pytest.mark.asyncio
async def test_a_malformed_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    _install_jwks(monkeypatch, {"keys": []})

    assert await _verify_activity_jwt("not-a-jwt-at-all", app_id="app-1") is None


@pytest.mark.asyncio
async def test_verify_inbound_requires_a_bearer_authorization_header() -> None:
    from channel.channel import TeamsChannel

    channel = TeamsChannel(app_id="app-1", app_password="pw")
    assert await channel.verify_inbound(headers={}, body=b"{}") is False


@pytest.mark.asyncio
async def test_verify_inbound_rejects_a_non_bearer_scheme() -> None:
    from channel.channel import TeamsChannel

    channel = TeamsChannel(app_id="app-1", app_password="pw")
    assert await channel.verify_inbound(headers={"Authorization": "Basic xyz"}, body=b"{}") is False


@pytest.mark.asyncio
async def test_verify_inbound_delegates_the_bearer_token_to_jwt_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import channel.channel as channel_module
    from channel.channel import TeamsChannel

    seen: dict[str, str] = {}

    async def fake_verify(token: str, *, app_id: str) -> dict[str, Any]:
        seen["token"] = token
        seen["app_id"] = app_id
        return {"serviceUrl": _SERVICE_URL}

    monkeypatch.setattr(channel_module, "_verify_activity_jwt", fake_verify)
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    assert (
        await channel.verify_inbound(headers={"Authorization": "Bearer tok123"}, body=_body())
        is True
    )
    assert seen == {"token": "tok123", "app_id": "app-1"}


@pytest.mark.asyncio
async def test_verify_inbound_accepts_a_token_whose_service_url_matches_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import TeamsChannel

    key = _keypair()
    _install_jwks(monkeypatch, {"keys": [_jwk_for(key, kid="k1")]})
    token = _token_for(key, kid="k1", audience="app-1")
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    verified = await channel.verify_inbound(
        headers={"Authorization": f"Bearer {token}"}, body=_body()
    )
    assert verified is True


@pytest.mark.asyncio
async def test_verify_inbound_rejects_a_service_url_the_token_does_not_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JWT signs nothing about the request body, so a VALID token replayed
    against a body naming the attacker's own `serviceUrl` would otherwise be
    accepted -- and that host is exactly what `deliver`/`withdraw`/`say` hand
    the bot's AAD access token to afterwards. Bot Framework's own auth
    contract requires this comparison."""
    from channel.channel import TeamsChannel

    key = _keypair()
    _install_jwks(monkeypatch, {"keys": [_jwk_for(key, kid="k1")]})
    token = _token_for(key, kid="k1", audience="app-1")
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    verified = await channel.verify_inbound(
        headers={"Authorization": f"Bearer {token}"},
        body=_body("https://evil.example.com/teams/"),
    )
    assert verified is False


@pytest.mark.asyncio
async def test_verify_inbound_rejects_a_body_with_no_service_url_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import TeamsChannel

    key = _keypair()
    _install_jwks(monkeypatch, {"keys": [_jwk_for(key, kid="k1")]})
    token = _token_for(key, kid="k1", audience="app-1")
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    assert (
        await channel.verify_inbound(headers={"Authorization": f"Bearer {token}"}, body=_body(None))
        is False
    )


@pytest.mark.asyncio
async def test_verify_inbound_rejects_a_body_that_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from channel.channel import TeamsChannel

    key = _keypair()
    _install_jwks(monkeypatch, {"keys": [_jwk_for(key, kid="k1")]})
    token = _token_for(key, kid="k1", audience="app-1")
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    assert (
        await channel.verify_inbound(
            headers={"Authorization": f"Bearer {token}"}, body=b"not json at all"
        )
        is False
    )


def test_only_microsofts_own_connector_hosts_may_receive_the_bots_token() -> None:
    """The allowlist is the second half of the serviceUrl defence: even a
    serviceUrl that matched its token must still be a Bot Framework host
    before `_post`/`_put` attach a live AAD access token to it."""
    from channel.channel import _is_allowed_service_url

    assert _is_allowed_service_url("https://smba.trafficmanager.net/teams/") is True
    assert _is_allowed_service_url("https://smba.trafficmanager.net/emea/") is True
    assert _is_allowed_service_url("https://api.botframework.com/") is True

    assert _is_allowed_service_url("https://evil.example.com/") is False
    # http, not https -- the token would go over the wire in clear.
    assert _is_allowed_service_url("http://smba.trafficmanager.net/teams/") is False
    # A suffix match on the STRING, not the host, would let both of these
    # through.
    assert _is_allowed_service_url("https://evil.com/?x=.botframework.com") is False
    assert _is_allowed_service_url("https://notbotframework.com/") is False
    assert _is_allowed_service_url("") is False


@pytest.mark.asyncio
async def test_post_and_put_refuse_a_service_url_off_the_allowlist() -> None:
    """No HTTP is mocked here on purpose: if the guard fails, the call would
    have to actually reach out, and this test would fail loudly rather than
    quietly passing against a mock."""
    from channel.channel import TeamsChannel

    channel = TeamsChannel(app_id="app-1", app_password="pw")

    with pytest.raises(ValueError, match="refusing to send"):
        await channel._post("https://evil.example.com", "conv-1", {"type": "message"})
    with pytest.raises(ValueError, match="refusing to send"):
        await channel._put("https://evil.example.com", "conv-1", "act-1", {"type": "message"})
