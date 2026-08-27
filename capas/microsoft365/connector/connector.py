"""Microsoft 365 knowledge connector: SharePoint sites and OneDrive for
Business drives, shipped as a plugin.

Scope, stated plainly (mirrors gdrive_source's own docstring discipline):

* **Text is extracted, binaries are skipped.** Plain text/Markdown/CSV/JSON/
  HTML download and ingest as-is (Task 5). Word/Excel/PowerPoint are parsed
  with pure-Python libraries after download, since Graph -- unlike Google
  Drive -- has no server-side "export this as text" endpoint (Task 6).
  Everything else is skipped and counted, never silently dropped.
* **Read-only.** Only GETs; the app registration is scoped by whatever Graph
  application permissions the tenant admin granted (see the design doc §4.1).
* **Re-scan, not delta sync.** Same reasoning as gdrive_source: the ingestion
  framework's content-hash cursor is what prevents re-ingesting unchanged files.
* **It attests what it holds.** `attest_listing` enumerates every configured
  drive to the end of its pagination and swears the result is complete -- see
  gdrive_source's own `attest_listing` docstring for why that's what lets core
  conclude a document missing from it was deleted upstream. The `maxFiles`
  config key bounds what is *ingested* (Task 5), never this listing.

The connector never sees a token store: it asks the `AuthContext` for a token
and gets a fresh one, refresh (or, for this connection's client_credentials
grant, re-mint) included -- `AuthContext` doesn't care which.
"""

from __future__ import annotations

import hashlib
import io
import logging
from collections.abc import AsyncIterator
from typing import Any

from oc8.knowledge.connectors.base import (
    Attestation,
    AuthContext,
    ConnectorError,
    RawDocument,
    SourceItemMeta,
    SourceListing,
    ValidationResult,
)
from oc8.oauth.http import get_client

# Pinned to an explicit name, NOT `__name__`: after the package restructure this
# module's `__name__` is the generic `connector.connector`, which every other
# connector plugin (google_workspace, gdrive_source, ...) shares exactly. A
# shared logger name makes per-plugin log filtering and `caplog`-by-logger
# impossible and attributes one plugin's warnings to another.
logger = logging.getLogger("oc8.plugin.microsoft365.connector")

API = "https://graph.microsoft.com/v1.0"
_MAX_BYTES = 5 * 1024 * 1024

# Types we can ingest byte-for-byte as text -- same set gdrive_source downloads
# as-is, since these need no export/parsing step on either platform.
DOWNLOAD_AS_TEXT = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/json",
}

# Office-native formats: Graph's /content returns raw OOXML bytes, unlike
# Google's server-side export -- these three pure-Python libraries do the
# parsing after download. One entry per format; the value is which parser
# function handles it (defined below, after the imports that need them).
_OFFICE_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _extract_docx_text(raw: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(raw))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


def _extract_xlsx_text(raw: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(v) for v in row if v is not None]
            if cells:
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _extract_pptx_text(raw: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(raw))
    lines: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text:
                lines.append(shape.text_frame.text)
    return "\n".join(lines)


_OFFICE_EXTRACTORS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _extract_docx_text,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _extract_xlsx_text,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": _extract_pptx_text,
}


def _api_error(status: int, body: str) -> ConnectorError:
    if status in (401, 403):
        return ConnectorError(
            f"Microsoft Graph rejected the credentials (HTTP {status}) — check the app "
            "registration's Graph permissions and admin consent"
        )
    if status == 404:
        return ConnectorError(
            "Microsoft Graph drive/item not found — check the configured IDs"
        )
    return ConnectorError(f"Microsoft Graph API error (HTTP {status}): {body[:200]}")


async def _auth_header(auth: AuthContext | None) -> dict[str, str]:
    if auth is None:
        raise ConnectorError(
            "microsoft365 requires an Azure AD app registration connection"
        )
    return {"Authorization": f"Bearer {await auth.token()}"}


def _file_uri(drive_id: str, item_id: str) -> str:
    """One function, listing and fetch must agree byte-for-byte -- same
    discipline as gdrive_source's own `_file_uri` (its docstring explains why
    a mismatch here silently wipes a source)."""
    return f"{API}/drives/{drive_id}/items/{item_id}"


async def _list_drive_children(
    headers: dict[str, str], drive_id: str
) -> list[dict[str, Any]]:
    """Every non-folder item under a drive's root -- however many pages. Not
    bounded by any max-files config, for the same reason gdrive_source's own
    `_list_every_file` isn't: a listing that stopped early is indistinguishable
    from "the rest were deleted."""
    items: list[dict[str, Any]] = []
    url = f"{API}/drives/{drive_id}/root/children"
    async with get_client() as client:
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise _api_error(resp.status_code, resp.text)
            payload = resp.json()
            items.extend(
                item for item in payload.get("value", []) if "folder" not in item
            )
            url = payload.get("@odata.nextLink", "")
    return items


class Microsoft365FilesConnector:
    type_id = "microsoft365_files"
    label = "Microsoft 365 (SharePoint/OneDrive)"
    description = (
        "SharePoint sites and OneDrive for Business drives via one Azure AD app"
    )
    requires_oauth: str | None = "microsoft"
    config_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "siteIds": {
                "type": "array",
                "items": {"type": "string"},
                "title": "SharePoint site IDs",
                "description": "The specific site IDs to index. Leave empty to index none.",
            },
            "driveIds": {
                "type": "array",
                "items": {"type": "string"},
                "title": "OneDrive drive IDs",
                "description": "The specific OneDrive-for-Business drive IDs to index.",
            },
            "maxFiles": {
                "type": "integer",
                "default": 100,
                "minimum": 1,
                "maximum": 1000,
            },
        },
    }

    async def validate(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> ValidationResult:
        site_ids = config.get("siteIds") or []
        drive_ids = config.get("driveIds") or []
        if not site_ids and not drive_ids:
            return ValidationResult(
                ok=False, error="configure at least one site or drive ID"
            )
        if auth is None:
            return ValidationResult(
                ok=False, error="connect a Microsoft 365 app registration first"
            )
        try:
            headers = await _auth_header(auth)
            async with get_client() as client:
                resp = await client.get(f"{API}/organization", headers=headers)
            if resp.status_code != 200:
                raise _api_error(resp.status_code, resp.text)
        except ConnectorError as exc:
            return ValidationResult(ok=False, error=str(exc))
        return ValidationResult(ok=True)

    async def _resolve_drive_ids(
        self, headers: dict[str, str], config: dict[str, Any]
    ) -> list[str]:
        """Site IDs are convenience config -- Graph addresses content by drive
        ID, so a site's own document library drive is resolved once per call
        rather than asking every caller to know Graph's site->drive mapping."""
        drive_ids = list(config.get("driveIds") or [])
        async with get_client() as client:
            for site_id in config.get("siteIds") or []:
                resp = await client.get(f"{API}/sites/{site_id}/drive", headers=headers)
                if resp.status_code != 200:
                    raise _api_error(resp.status_code, resp.text)
                drive_ids.append(resp.json()["id"])
        return drive_ids

    async def discover(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> list[SourceItemMeta]:
        headers = await _auth_header(auth)
        drive_ids = await self._resolve_drive_ids(headers, config)
        out: list[SourceItemMeta] = []
        for drive_id in drive_ids:
            items = await _list_drive_children(headers, drive_id)
            out.extend(
                SourceItemMeta(
                    uri=_file_uri(drive_id, i["id"]), title=str(i.get("name", ""))
                )
                for i in items
            )
        return out

    async def attest_listing(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> SourceListing:
        headers = await _auth_header(auth)
        drive_ids = await self._resolve_drive_ids(headers, config)
        present: set[str] = set()
        for drive_id in drive_ids:
            items = await _list_drive_children(headers, drive_id)
            present.update(_file_uri(drive_id, i["id"]) for i in items)
        return SourceListing(
            attestation=Attestation.AUTHORITATIVE, present_uris=frozenset(present)
        )

    async def fetch(
        self,
        config: dict[str, Any],
        cursor: dict[str, Any] | None,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[RawDocument]:
        headers = await _auth_header(auth)
        seen = set((cursor or {}).get("hashes", []))
        drive_ids = await self._resolve_drive_ids(headers, config)
        max_files = int(config.get("maxFiles", 100))
        yielded = 0
        for drive_id in drive_ids:
            if yielded >= max_files:
                break
            for meta in await _list_drive_children(headers, drive_id):
                if yielded >= max_files:
                    break
                result = await self._read_text(headers, drive_id, meta)
                if result is None:
                    continue
                text, content_type = result
                if not text.strip():
                    continue
                digest = hashlib.sha256(text.encode()).hexdigest()
                if digest in seen:
                    continue
                yielded += 1
                yield RawDocument(
                    source_uri=_file_uri(drive_id, meta["id"]),
                    title=str(meta.get("name", "")),
                    content=text,
                    content_type=content_type,
                    content_hash=digest,
                    metadata={
                        "driveId": drive_id,
                        "itemId": str(meta.get("id", "")),
                        "mimeType": str(meta.get("file", {}).get("mimeType", "")),
                    },
                )

    async def _read_text(
        self, headers: dict[str, str], drive_id: str, meta: dict[str, Any]
    ) -> tuple[str, str] | None:
        """(text, content_type), or None if this file isn't ingestible.
        Extended in Task 6 to also handle Word/Excel/PowerPoint."""
        mime = str(meta.get("file", {}).get("mimeType", ""))
        if mime not in DOWNLOAD_AS_TEXT and mime not in _OFFICE_MIME_TYPES:
            return None
        item_id = str(meta["id"])
        # follow_redirects: /content does not return the bytes, it returns 302
        # + Location pointing at a short-lived pre-authenticated download URL on
        # a *.sharepoint.com (or blob-storage) host. Without this the very first
        # ingestible file answered 302, `_api_error` turned that into "HTTP 302"
        # and the whole sync aborted -- this connector never ingested one
        # document against the real API. httpx drops the Authorization header on
        # the cross-host hop, which is what we want: the target URL is already
        # authenticated and has no business holding a Graph token.
        async with get_client(follow_redirects=True) as client:
            resp = await client.get(
                f"{API}/drives/{drive_id}/items/{item_id}/content", headers=headers
            )
        if resp.status_code != 200:
            raise _api_error(resp.status_code, resp.text)
        if len(resp.content) > _MAX_BYTES:
            return None
        if mime in _OFFICE_MIME_TYPES:
            # Skip, never abort: a corrupt or password-protected .docx/.xlsx/
            # .pptx raises out of the parser, and letting that escape took the
            # ENTIRE sync down over one bad file -- the same all-or-nothing
            # shape as the redirect bug above. This file's own docstring states
            # the policy: "everything else is skipped ... never silently
            # dropped", so an unreadable Office file joins the binaries --
            # skipped, and said out loud in the log. NOT counted on `self`: the
            # connector is registered once per process and shared by every
            # tenant, so an instance attribute would mix all of them.
            try:
                return _OFFICE_EXTRACTORS[mime](resp.content), mime
            except Exception:
                logger.warning(
                    "microsoft365: skipping unreadable %s in drive %s (item %s)",
                    mime,
                    drive_id,
                    item_id,
                    exc_info=True,
                )
                return None
        return resp.text, mime


def register(contrib: Any) -> None:
    contrib.add_connector(Microsoft365FilesConnector())
