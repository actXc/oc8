"""Setup-time proof that a submitted app id and app password actually work
together (§13 setup_validate). Called once, when an admin submits the
credential form -- before either value is ever handed to `channel.build()`.
A wrong registration found here costs one rejected form; found in
production it costs an approval that silently never reaches anyone.
"""

from __future__ import annotations

import httpx

from .channel import mint_access_token


async def validate(values: dict[str, str]) -> None:
    app_id = values.get("app_id", "")
    app_password = values.get("app_password", "")
    if not app_id:
        raise ValueError("no app id submitted")
    if not app_password:
        raise ValueError("no app password submitted")
    try:
        await mint_access_token(
            app_id=app_id, app_password=app_password, tenant=values.get("tenant_id", "")
        )
    except httpx.HTTPStatusError as exc:
        body = exc.response.json() if exc.response.content else {}
        reason = body.get("error_description") or body.get("error") or f"HTTP {exc.response.status_code}"
        raise ValueError(f"Microsoft rejected this app registration: {reason}") from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"could not reach Microsoft: {exc}") from exc
