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


import json
import uuid as _uuid
from collections.abc import Mapping

from oc8.channels.notice import (
    ApprovalNotice,
    ChannelDecision,
    ChannelFreeText,
    ChannelLink,
)


def render_body(notice: ApprovalNotice) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": notice.title, "weight": "Bolder", "size": "Medium", "wrap": True}
    ]
    if notice.content_withheld:
        blocks.append(
            {
                "type": "TextBlock",
                "text": (
                    "Der Inhalt dieser Anfrage darf nicht über Teams gehen. "
                    "Bitte in oc8 öffnen und dort entscheiden."
                ),
                "wrap": True,
            }
        )
        return blocks
    if notice.amount_text:
        blocks.append({"type": "TextBlock", "text": notice.amount_text, "weight": "Bolder", "wrap": True})
    if notice.detail:
        blocks.append({"type": "TextBlock", "text": notice.detail, "wrap": True})
    if notice.expires_at is not None:
        blocks.append(
            {
                "type": "TextBlock",
                "text": f"Läuft ab: {notice.expires_at:%d.%m.%Y %H:%M}",
                "wrap": True,
                "isSubtle": True,
            }
        )
    return blocks


def render_actions(notice: ApprovalNotice) -> list[dict[str, Any]]:
    """A redacted notice gets NO buttons on purpose: a button labelled "Voll
    erstatten" tells you what the request is about even with the text
    removed, which is exactly what withholding it was for."""
    if notice.content_withheld:
        return []
    actions: list[dict[str, Any]] = []
    for option in notice.options:
        actions.append(
            {
                "type": "Action.Submit",
                "title": option.label,
                "data": {"payload": f"{notice.approval_id}:approve:{option.key}"},
            }
        )
    if not actions:
        actions.append(
            {
                "type": "Action.Submit",
                "title": "✅ Freigeben",
                "data": {"payload": f"{notice.approval_id}:approve"},
            }
        )
    actions.append(
        {
            "type": "Action.Submit",
            "title": "✖️ Ablehnen",
            "data": {"payload": f"{notice.approval_id}:reject"},
        }
    )
    return actions


def render_card(notice: ApprovalNotice) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": render_body(notice),
    }
    actions = render_actions(notice)
    if actions:
        card["actions"] = actions
    return card


def render_outcome_card(notice: ApprovalNotice, *, outcome: str) -> dict[str, Any]:
    said = {"approved": "freigegeben", "rejected": "abgelehnt"}.get(outcome, outcome)
    body = render_body(notice)
    body.append({"type": "TextBlock", "text": f"Entschieden: {said}", "wrap": True, "isSubtle": True})
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }


def parse_payload(data: str) -> tuple[str, str, str | None] | None:
    """`<approval-id>:<verdict>[:<option>]` -> its parts, or None if it is
    not ours. Same encoding, and the same local-parser-per-plugin shape, as
    Telegram's own `parse_callback`."""
    parts = data.split(":")
    if len(parts) < 2 or parts[1] not in ("approve", "reject"):
        return None
    return parts[0], parts[1], parts[2] if len(parts) > 2 and parts[2] else None


def _conversation_reference(activity: Mapping[str, Any]) -> dict[str, Any]:
    """A flat, deterministic subset of the inbound Activity's own fields --
    everything `deliver`/`withdraw`/`say` (Task 11) need to reconstruct a
    Connector API call later, when there is no inbound Activity to read
    them from again. Flat and ID-only (not the raw `conversation`/`from`/
    `recipient` sub-objects) so the JSON is stable across activities that
    otherwise vary in which extra keys Teams includes."""
    conversation = activity.get("conversation") or {}
    bot = activity.get("recipient") or {}
    user = activity.get("from") or {}
    return {
        "serviceUrl": activity.get("serviceUrl"),
        "channelId": activity.get("channelId"),
        "conversationId": conversation.get("id"),
        "botId": bot.get("id"),
        "botName": bot.get("name"),
        "userId": user.get("id"),
        "userName": user.get("name"),
    }


def _external_id_for(activity: Mapping[str, Any]) -> str:
    return json.dumps(_conversation_reference(activity), sort_keys=True)


def _sender_id(activity: Mapping[str, Any]) -> str | None:
    sender = activity.get("from")
    sender_id = sender.get("id") if isinstance(sender, dict) else None
    return str(sender_id) if sender_id else None


def parse_activity(
    activity: Mapping[str, Any],
) -> ChannelDecision | ChannelLink | ChannelFreeText | None:
    if activity.get("type") != "message":
        return None
    sender_id = _sender_id(activity)
    if sender_id is None:
        return None
    value = activity.get("value")
    if isinstance(value, dict):
        parsed = parse_payload(str(value.get("payload") or ""))
        if parsed is not None:
            approval_id, verdict, option = parsed
            try:
                approval_uuid = _uuid.UUID(approval_id)
            except ValueError:
                return None
            return ChannelDecision(
                approval_id=approval_uuid,
                verdict=verdict,
                option_key=option,
                external_id=_external_id_for(activity),
            )
    text = str(activity.get("text") or "").strip()
    if not text:
        return None
    if " " not in text:
        return ChannelLink(code=text, external_id=_external_id_for(activity))
    return ChannelFreeText(text=text, external_id=_external_id_for(activity))
