"""`_verify_activity_jwt` -- Bot Framework's own inbound auth scheme: a
bearer JWT signed by a key published at Microsoft's JWKS endpoint. Every
case here must fail closed: a malformed token, an unknown key id, a wrong
audience, an expired signature, or a signature from the wrong key must all
come back False, the same discipline Telegram's/WhatsApp's own
verify_inbound already follow."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
    """`_jwks_cache`/`_jwks_cache_at` are module-level mutable state; without
    resetting them, one test's cached keys would leak into the next."""
    import channel.channel as channel_module

    channel_module._jwks_cache = {}
    channel_module._jwks_cache_at = 0.0
    yield
    channel_module._jwks_cache = {}
    channel_module._jwks_cache_at = 0.0


def _keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk_for(private_key: rsa.RSAPrivateKey, *, kid: str) -> dict[str, Any]:
    public_jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = kid
    public_jwk["use"] = "sig"
    public_jwk["alg"] = "RS256"
    return public_jwk


def _token_for(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    audience: str,
    issuer: str = "https://api.botframework.com",
    exp_delta: int = 3600,
) -> str:
    now = int(time.time())
    payload = {"aud": audience, "iss": issuer, "iat": now, "exp": now + exp_delta}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


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

    assert await _verify_activity_jwt(token, app_id="app-1") is True


@pytest.mark.asyncio
async def test_an_expired_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    _install_jwks(monkeypatch, jwks)
    token = _token_for(key, kid="k1", audience="app-1", exp_delta=-3600)

    assert await _verify_activity_jwt(token, app_id="app-1") is False


@pytest.mark.asyncio
async def test_a_token_for_the_wrong_audience_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    key = _keypair()
    jwks = {"keys": [_jwk_for(key, kid="k1")]}
    _install_jwks(monkeypatch, jwks)
    token = _token_for(key, kid="k1", audience="some-other-app")

    assert await _verify_activity_jwt(token, app_id="app-1") is False


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

    assert await _verify_activity_jwt(token, app_id="app-1") is False


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

    assert await _verify_activity_jwt(token, app_id="app-1") is False
    assert fetch_calls == 2, "one fetch to populate the cache, one forced refresh on the miss"


@pytest.mark.asyncio
async def test_a_malformed_token_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from channel.channel import _verify_activity_jwt

    _install_jwks(monkeypatch, {"keys": []})

    assert await _verify_activity_jwt("not-a-jwt-at-all", app_id="app-1") is False


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

    async def fake_verify(token: str, *, app_id: str) -> bool:
        seen["token"] = token
        seen["app_id"] = app_id
        return True

    monkeypatch.setattr(channel_module, "_verify_activity_jwt", fake_verify)
    channel = TeamsChannel(app_id="app-1", app_password="pw")

    assert (
        await channel.verify_inbound(headers={"Authorization": "Bearer tok123"}, body=b"{}") is True
    )
    assert seen == {"token": "tok123", "app_id": "app-1"}
