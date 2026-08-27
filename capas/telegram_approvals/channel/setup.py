"""Setup-time proof that a submitted bot token actually works (§13 setup_validate).

Called once, when an admin submits the setup form -- before the token is ever
handed to `channel.build()`. A wrong token found here costs one rejected form;
found in production it costs an approval that silently never reaches anyone.
"""

from __future__ import annotations

import httpx

from .channel import API


async def validate(values: dict[str, str]) -> None:
    token = values.get("bot_token", "")
    if not token:
        raise ValueError("no bot token submitted")
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(f"{API}/bot{token}/getMe")
        except httpx.HTTPError as exc:
            raise ValueError(f"could not reach Telegram: {exc}") from exc
    body = response.json()
    if not body.get("ok"):
        reason = body.get("description") or "unknown error"
        raise ValueError(f"Telegram rejected this token: {reason}")
