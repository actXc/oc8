"""Verify a Bot Framework Activity, and (later tasks) render one as an
Adaptive Card and post one back through the Connector API.

Everything Teams-shaped lives here, same as telegram_approvals'/
whatsapp_approvals' own channel.py. Bot Framework's inbound auth is a bearer
JWT signed by a key published at a well-known JWKS endpoint -- there is no
SDK dependency here, only pyjwt + cryptography, both already used elsewhere
in this codebase.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

# Module logger, never __name__: `channel/` is a shared folder name across
# plugins.
logger = logging.getLogger("oc8.plugin.teams_approvals.channel")

CHANNEL_ID = "teams"

_JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
_JWKS_ISSUER = "https://api.botframework.com"
#: How long a fetched JWKS is trusted before a routine refresh. Independent
#: of the unknown-kid forced refresh below, which fires immediately
#: regardless of this TTL.
_JWKS_CACHE_TTL_SECONDS = 3600

_jwks_cache: dict[str, Any] = {}
_jwks_cache_at: float = 0.0


async def _fetch_jwks() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(_JWKS_URL)
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


async def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    global _jwks_cache, _jwks_cache_at
    now = time.monotonic()
    if not force_refresh and _jwks_cache and (now - _jwks_cache_at) < _JWKS_CACHE_TTL_SECONDS:
        return _jwks_cache
    _jwks_cache = await _fetch_jwks()
    _jwks_cache_at = now
    return _jwks_cache


async def _verify_activity_jwt(token: str, *, app_id: str) -> bool:
    """Fails closed on anything unexpected -- a malformed token, an unknown
    key id, a wrong audience or issuer, an expired signature -- because an
    unauthenticated webhook that reaches the decision path is an open door
    to approving anything, same discipline as Telegram's/WhatsApp's own
    verify_inbound.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return False
    kid = header.get("kid")
    if not kid:
        return False
    jwks = await _get_jwks()
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key_data is None:
        # The key may have rotated since our cache was built -- refresh once
        # and retry before giving up.
        jwks = await _get_jwks(force_refresh=True)
        key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key_data is None:
            return False
    try:
        import json

        public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
        jwt.decode(token, key=public_key, algorithms=["RS256"], audience=app_id, issuer=_JWKS_ISSUER)  # type: ignore[arg-type]
    except jwt.PyJWTError:
        return False
    return True
