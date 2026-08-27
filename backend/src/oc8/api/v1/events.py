"""Inbound webhook ingress. Authenticated by per-source signature, not bearer."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from oc8.api.deps import unguarded
from oc8.config import get_settings
from oc8.events.dispatcher import get_dispatcher
from oc8.events.github import parse_github_event, verify_github_signature

router = APIRouter()


@router.post(
    "/events/{source}",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(
            unguarded("authenticated by the sender's webhook signature, not by an operator role")
        )
    ],
)
async def ingest(source: str, request: Request) -> dict[str, int]:
    body = await request.body()
    if source != "github":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown event source")
    settings = get_settings()
    sig = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(settings.github_webhook_secret, body, sig):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")

    payload = json.loads(body) if body else {}
    event = parse_github_event(request.headers, payload)
    dispatched = await get_dispatcher().dispatch(event)
    return {"dispatched": dispatched}
