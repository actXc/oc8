"""Setup-time proof that a submitted access token and phone number actually work
together (§13 setup_validate). Called once, when an admin submits the setup
form -- before either value is ever handed to `channel.build()`.
"""

from __future__ import annotations

import httpx

from .channel import API


async def validate(values: dict[str, str]) -> None:
    token = values.get("access_token", "")
    phone_number_id = values.get("phone_number_id", "")
    if not token:
        raise ValueError("no access token submitted")
    if not phone_number_id:
        raise ValueError("no phone number id submitted")
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(
                f"{API}/{phone_number_id}",
                params={"fields": "verified_name"},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise ValueError(f"could not reach WhatsApp: {exc}") from exc
    if response.status_code != 200:
        body = response.json() if response.content else {}
        reason = body.get("error", {}).get("message") or f"HTTP {response.status_code}"
        raise ValueError(f"WhatsApp rejected this token/number: {reason}")
