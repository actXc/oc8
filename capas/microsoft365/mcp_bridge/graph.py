"""Thin async Microsoft Graph client for the MCP bridge process.

Deliberately NOT reusing `oc8.oauth`'s `AuthContext`: this module runs in a
SEPARATE process (the stdio bridge, launched by `oc8.agent.mcp_client` — see
plugin.toml's `command`/`args`), with no database session and no access to
oc8's secret store. The core process is what holds an `OAuthConnection` and
calls `get_access_token`; it resolves a fresh token BEFORE launching this
bridge and passes it via the `GRAPH_ACCESS_TOKEN` env var (see plugin.toml's
`[plugin.tool_pack.connections.config.secret_env]` in Task 13, which is the
existing mechanism odoo_mcp already uses for its own password -- nothing new
here). A token bound this way is valid for the lifetime of one bridge process
(started fresh per tool-calling session), which matches an access token's own
~1 hour lifetime closely enough that no in-bridge refresh logic is needed.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

API = "https://graph.microsoft.com/v1.0"

#: `GET /drives/{id}/items/{id}/content` does NOT return the bytes. It answers
#: 302 with a `Location` pointing at a short-lived, already-pre-authenticated
#: download URL on a different host (`*.sharepoint.com`, or a blob-storage
#: host). Without following it, `resp.content` is empty and `resp.status_code`
#: is neither 200 nor >= 400 -- so every download silently returned nothing.
#:
#: httpx strips the `Authorization` header itself when a redirect leaves the
#: origin (verified against the installed 0.28.1: `_redirect_headers` pops it
#: for any non-same-origin hop that is not a plain http->https upgrade), which
#: is what must happen here -- the redirect target carries its own credential
#: in the URL and has no business seeing a live Graph bearer token.
_FOLLOW_DOWNLOAD_REDIRECT = True

#: Same limit the knowledge-base half already applies to a downloaded file
#: (`microsoft365/connector/connector.py:_MAX_BYTES`) -- one number for "a file this
#: plugin will pull into memory", so an operator does not have to learn two.
#: Without it a multi-megabyte SharePoint document goes straight into a run's
#: context (the connector skips such a file; here we say so out loud instead,
#: because a tool call asked for this specific item by id).
_MAX_BYTES = 5 * 1024 * 1024


def _headers() -> dict[str, str]:
    token = os.environ.get("GRAPH_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("GRAPH_ACCESS_TOKEN is not set")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{API}{path}", headers=_headers(), params=params)
    return _unwrap(resp)


async def get_bytes(path: str) -> bytes:
    """A file's raw bytes. Only ever called for `/content`, hence the redirect."""
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=_FOLLOW_DOWNLOAD_REDIRECT
    ) as client:
        resp = await client.get(f"{API}{path}", headers=_headers())
    _raise_for_status(resp, f"GET {path}")
    _raise_if_too_large(resp, f"GET {path}")
    return resp.content


async def get_text(path: str) -> str:
    """A file's content decoded as text.

    Separate from `get` deliberately: `/content` answers with the file itself,
    not JSON, so putting it through `_unwrap`'s `resp.json()` would raise on
    every plain-text file the moment the redirect above is actually followed.
    """
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=_FOLLOW_DOWNLOAD_REDIRECT
    ) as client:
        resp = await client.get(f"{API}{path}", headers=_headers())
    _raise_for_status(resp, f"GET {path}")
    _raise_if_too_large(resp, f"GET {path}")
    return resp.text


async def post(path: str, json: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{API}{path}", headers=_headers(), json=json)
    return _unwrap(resp)


async def patch(path: str, json: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.patch(f"{API}{path}", headers=_headers(), json=json)
    return _unwrap(resp)


async def put_bytes(path: str, content: bytes) -> dict[str, Any]:
    headers = _headers()
    headers["Content-Type"] = "application/octet-stream"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(f"{API}{path}", headers=headers, content=content)
    return _unwrap(resp)


async def delete(path: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(f"{API}{path}", headers=_headers())
    _raise_for_status(resp, f"DELETE {path}")


def _raise_for_status(resp: httpx.Response, what: str) -> None:
    """One wording for a Graph failure, matching the connector's own
    `_api_error` (microsoft365/connector/connector.py) -- 401/403/404 are the same
    failures on both halves of this plugin and an operator should not have to
    learn two vocabularies for them.

    Raised, not returned: `__main__._on_call_tool` turns any exception here
    into a real `is_error=True` tool result, so the text below is what the
    model and the run transcript actually see.
    """
    status = resp.status_code
    if status < 400:
        return
    if status in (401, 403):
        raise RuntimeError(
            f"Microsoft Graph rejected the credentials (HTTP {status}) — check the app "
            f"registration's Graph permissions and admin consent [{what}]"
        )
    if status == 404:
        raise RuntimeError(
            f"Microsoft Graph item not found (HTTP 404) — check the configured IDs [{what}]"
        )
    raise RuntimeError(f"Microsoft Graph API error (HTTP {status}): {resp.text[:200]} [{what}]")


def _raise_if_too_large(resp: httpx.Response, what: str) -> None:
    """Refuse a download bigger than `_MAX_BYTES`, loudly.

    Only the `/content` helpers call this: a JSON Graph response is paged by
    the API itself, a file is not. Raised rather than truncated -- half a
    `.docx` is not a smaller `.docx`, it is an unparseable one, and half a
    text file read as though it were whole is worse than an error.
    """
    size = len(resp.content)
    if size > _MAX_BYTES:
        raise RuntimeError(
            f"File is too large to read ({size} bytes, limit {_MAX_BYTES}) [{what}]"
        )


def _unwrap(resp: httpx.Response) -> dict[str, Any]:
    _raise_for_status(resp, "call")
    if not resp.content:
        return {}
    body: dict[str, Any] = resp.json()
    return body
