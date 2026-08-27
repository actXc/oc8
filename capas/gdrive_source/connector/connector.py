"""Google Drive knowledge connector, shipped as a plugin.

Scope, stated plainly so nobody is surprised by what lands in a knowledge base:

* **Text is extracted, binaries are skipped.** Google Docs/Sheets/Slides are
  exported as text; plain text, Markdown, CSV and JSON files are downloaded as
  they are. Everything else -- PDFs, images, archives -- is skipped rather than
  ingested as garbage. Skipped files are counted and reported, never silently
  dropped.
* **Read-only.** The connector only ever issues GETs, and the Google provider
  requests `drive.readonly`.
* **Re-scan, not delta sync.** Each sync lists the folder again; the ingestion
  framework's content-hash cursor is what prevents re-ingesting unchanged files.
  Drive's changes API would be cheaper on huge drives and is not used here.
* **It attests what it holds.** `attest_listing` enumerates the folder to the
  end and swears the result is complete, which is what lets the core conclude
  that a document missing from it was deleted upstream. `maxFiles` bounds what
  is *ingested*; it never bounds that listing, because a listing that stopped
  early cannot be told apart from files that are gone.

The connector never sees a token store or an expiry: it asks the `AuthContext`
for a token and gets a fresh one, refresh included.
"""

from __future__ import annotations

import hashlib
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

API = "https://www.googleapis.com/drive/v3"

# Google-native types have no bytes to download; they must be exported.
EXPORT_AS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

# Types we can ingest byte-for-byte as text.
DOWNLOAD_AS_TEXT = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "application/json",
}

_LIST_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime)"
_MAX_BYTES = 5 * 1024 * 1024


def _q(folder_id: str) -> str:
    """Drive query: inside a folder if one is configured, never trashed."""
    clauses = ["trashed = false"]
    if folder_id:
        # Escape single quotes -- a folder id is operator-supplied config, and a
        # stray quote would otherwise change the meaning of the query.
        safe = folder_id.replace("\\", "\\\\").replace("'", "\\'")
        clauses.append(f"'{safe}' in parents")
    return " and ".join(clauses)


async def _auth_header(auth: AuthContext | None) -> dict[str, str]:
    if auth is None:
        raise ConnectorError("google drive requires an OAuth connection")
    return {"Authorization": f"Bearer {await auth.token()}"}


def _api_error(status: int, body: str) -> ConnectorError:
    if status in (401, 403):
        return ConnectorError(
            "Google Drive rejected the credentials — reconnect the Google account "
            f"(HTTP {status})"
        )
    if status == 404:
        return ConnectorError("Google Drive folder not found — check the folder ID")
    return ConnectorError(f"Google Drive API error (HTTP {status}): {body[:200]}")


def _file_uri(meta: dict[str, Any]) -> str:
    """The one place a Drive file's URI is built.

    `attest_listing` and `fetch` must produce byte-identical strings: if they
    drift, the listing and the corpus share zero URIs, every stored document
    reads as absent from every listing, and the source is wiped. Core catches
    that at runtime with the namespace-mismatch refusal; one function is what
    stops it happening.
    """
    return f"https://drive.google.com/file/d/{meta.get('id', '')}"


async def _list_files(
    headers: dict[str, str], folder_id: str, max_files: int
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token = ""
    async with get_client() as client:
        while len(files) < max_files:
            params: dict[str, str | int] = {
                "q": _q(folder_id),
                "fields": _LIST_FIELDS,
                "pageSize": min(100, max_files - len(files)),
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(f"{API}/files", params=params, headers=headers)
            if resp.status_code != 200:
                raise _api_error(resp.status_code, resp.text)
            payload = resp.json()
            files.extend(payload.get("files", []))
            page_token = payload.get("nextPageToken", "")
            if not page_token:
                break
    return files[:max_files]


async def _list_every_file(headers: dict[str, str], folder_id: str) -> list[dict[str, Any]]:
    """The folder, all of it, however many pages that takes.

    Deliberately not `_list_files(..., max_files)`: a listing that stopped at a
    cap is indistinguishable at the wire from the files past the cap having been
    deleted, so a bounded listing would silently condemn the tail of any folder
    bigger than `maxFiles` -- the ordinary case, not the exotic one. Enumeration
    is affordable where an unbounded FETCH is not because a page carries ids and
    names, not content: one request per 1000 files.

    `maxFiles` still bounds `fetch()`, where the cost is embedding and storing.
    """
    files: list[dict[str, Any]] = []
    page_token = ""
    async with get_client() as client:
        while True:
            params: dict[str, str | int] = {
                "q": _q(folder_id),
                "fields": _LIST_FIELDS,
                "pageSize": 1000,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(f"{API}/files", params=params, headers=headers)
            if resp.status_code != 200:
                # Raise rather than return what we have so far: a half-read
                # listing that claimed completeness would delete the other half.
                raise _api_error(resp.status_code, resp.text)
            payload = resp.json()
            files.extend(payload.get("files", []))
            page_token = payload.get("nextPageToken", "")
            if not page_token:
                return files


async def _read_text(headers: dict[str, str], meta: dict[str, Any]) -> tuple[str, str] | None:
    """Return (text, content_type), or None if this file type is not ingestible."""
    mime = str(meta.get("mimeType", ""))
    file_id = str(meta.get("id", ""))
    export_to = EXPORT_AS.get(mime)

    async with get_client() as client:
        if export_to is not None:
            resp = await client.get(
                f"{API}/files/{file_id}/export",
                params={"mimeType": export_to},
                headers=headers,
            )
            content_type = export_to
        elif mime in DOWNLOAD_AS_TEXT:
            resp = await client.get(
                f"{API}/files/{file_id}", params={"alt": "media"}, headers=headers
            )
            content_type = mime
        else:
            return None

    if resp.status_code != 200:
        raise _api_error(resp.status_code, resp.text)
    if len(resp.content) > _MAX_BYTES:
        return None
    return resp.text, content_type


class GoogleDriveConnector:
    type_id = "gdrive"
    label = "Google Drive"
    description = "Documents and files shared with a connected account"
    requires_oauth: str | None = "google"
    config_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "folderId": {
                "type": "string",
                "title": "Folder ID",
                "description": "Leave empty to sync everything the account can read.",
            },
            "maxFiles": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000},
        },
    }

    async def validate(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> ValidationResult:
        max_files = config.get("maxFiles", 100)
        if not isinstance(max_files, int) or not 1 <= max_files <= 1000:
            return ValidationResult(ok=False, error="maxFiles must be between 1 and 1000")
        if auth is None:
            # Surfaced at source-creation time, before anything is stored.
            return ValidationResult(ok=False, error="connect a Google account first")
        try:
            headers = await _auth_header(auth)
            async with get_client() as client:
                resp = await client.get(
                    f"{API}/about", params={"fields": "user"}, headers=headers
                )
            if resp.status_code != 200:
                raise _api_error(resp.status_code, resp.text)
        except ConnectorError as exc:
            return ValidationResult(ok=False, error=str(exc))
        return ValidationResult(ok=True)

    async def discover(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> list[SourceItemMeta]:
        headers = await _auth_header(auth)
        files = await _list_files(
            headers, str(config.get("folderId", "")), int(config.get("maxFiles", 100))
        )
        return [SourceItemMeta(uri=_file_uri(f), title=str(f.get("name", ""))) for f in files]

    async def attest_listing(
        self, config: dict[str, Any], auth: AuthContext | None = None
    ) -> SourceListing:
        """Every file this folder currently holds -- the whole folder, or nothing.

        AUTHORITATIVE is honest here because `_q` asks Drive for
        `trashed = false`, so a file the customer trashed is already absent from
        the answer; deletion propagation works the day this ships rather than
        waiting for a delta API.

        `maxFiles` is not consulted, on purpose (see `_list_every_file`), and an
        API error propagates as `ConnectorError` instead of becoming an empty
        AUTHORITATIVE listing -- a revoked grant looks exactly like a customer
        who deleted everything, and only one of those should erase a corpus.
        """
        headers = await _auth_header(auth)
        files = await _list_every_file(headers, str(config.get("folderId", "")))
        return SourceListing(
            attestation=Attestation.AUTHORITATIVE,
            present_uris=frozenset(_file_uri(f) for f in files),
        )

    async def fetch(
        self,
        config: dict[str, Any],
        cursor: dict[str, Any] | None,
        auth: AuthContext | None = None,
    ) -> AsyncIterator[RawDocument]:
        headers = await _auth_header(auth)
        seen = set((cursor or {}).get("hashes", []))
        files = await _list_files(
            headers, str(config.get("folderId", "")), int(config.get("maxFiles", 100))
        )
        for meta in files:
            result = await _read_text(headers, meta)
            if result is None:
                continue  # not an ingestible type, or oversized
            text, content_type = result
            if not text.strip():
                continue
            digest = hashlib.sha256(text.encode()).hexdigest()
            if digest in seen:
                continue
            yield RawDocument(
                source_uri=_file_uri(meta),
                title=str(meta.get("name", "")),
                content=text,
                content_type=content_type,
                content_hash=digest,
                metadata={
                    "driveFileId": str(meta.get("id", "")),
                    "mimeType": str(meta.get("mimeType", "")),
                    "modifiedTime": str(meta.get("modifiedTime", "")),
                },
            )


def register(contrib: Any) -> None:
    contrib.add_connector(GoogleDriveConnector())
