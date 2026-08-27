"""The shipped Google Drive plugin, exercised against a mock Drive API.

No test here reaches Google: `oc8.oauth.http` has a transport seam, and every
request is served by an in-process handler that records what was asked for.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.config import get_settings
from oc8.knowledge.connectors.base import ConnectorError
from oc8.oauth import http as oauth_http

pytestmark = pytest.mark.asyncio

PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"


class _FakeAuth:
    def __init__(self, token: str = "tok-123") -> None:
        self._token = token
        self.calls = 0

    async def token(self) -> str:
        self.calls += 1
        return self._token


class _Drive:
    """A tiny fake Drive API. Records every request it serves."""

    def __init__(self, files: list[dict[str, Any]], bodies: dict[str, str] | None = None) -> None:
        self.files = files
        self.bodies = bodies or {}
        self.requests: list[httpx.Request] = []
        self.status_override: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status_override is not None:
            return httpx.Response(self.status_override, text="denied")
        path = request.url.path
        if path.endswith("/about"):
            return httpx.Response(200, json={"user": {"emailAddress": "a@b.c"}})
        if path.endswith("/files"):
            return httpx.Response(
                200, json={"files": self.files}, headers={"content-type": "application/json"}
            )
        # /files/{id}/export or /files/{id}?alt=media
        file_id = path.split("/files/")[1].split("/")[0]
        return httpx.Response(200, text=self.bodies.get(file_id, ""))


@pytest.fixture(autouse=True)
def _plugins_path(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OC8_CAPAS_PATH", str(PLUGINS_DIR))
    get_settings.cache_clear()
    loader.reset_for_tests()
    contributions.reset_for_tests()
    yield
    oauth_http.set_transport_override(None)
    loader.reset_for_tests()
    contributions.reset_for_tests()
    get_settings.cache_clear()


def _connector() -> Any:
    found = find_plugin("gdrive_source")
    assert found is not None, "gdrive_source plugin not discovered"
    assert found.valid, found.error
    assert loader.load_plugin(found) is True
    conns = contributions.connectors_for("gdrive_source")
    assert "gdrive" in conns
    return conns["gdrive"]


def _install(drive: _Drive) -> None:
    oauth_http.set_transport_override(httpx.MockTransport(drive.handler))


# ---------------------------------------------------------------- shape

async def test_the_plugin_manifest_declares_a_connector_entry_point() -> None:
    found = find_plugin("gdrive_source")
    assert found is not None and found.manifest is not None
    assert found.type == "connector"
    assert found.trust == "first_party"
    assert found.manifest["entry_points"]["connectors"] == "connector.connector:register"


async def test_it_declares_that_it_needs_google_oauth() -> None:
    c = _connector()
    assert c.type_id == "gdrive"
    assert c.requires_oauth == "google"


# ---------------------------------------------------------------- validate

async def test_validate_without_a_google_connection_fails_clearly() -> None:
    res = await _connector().validate({}, None)
    assert res.ok is False
    assert "google" in (res.error or "").lower()


async def test_validate_calls_drive_with_the_bearer_token() -> None:
    drive = _Drive([])
    _install(drive)
    auth = _FakeAuth()
    res = await _connector().validate({}, auth)
    assert res.ok is True, res.error
    assert auth.calls == 1
    assert drive.requests[0].headers["Authorization"] == "Bearer tok-123"


async def test_validate_reports_a_revoked_grant_as_reconnect_not_a_stack_trace() -> None:
    drive = _Drive([])
    drive.status_override = 401
    _install(drive)
    res = await _connector().validate({}, _FakeAuth())
    assert res.ok is False
    assert "reconnect" in (res.error or "").lower()


async def test_validate_rejects_an_out_of_range_max_files() -> None:
    res = await _connector().validate({"maxFiles": 99999}, _FakeAuth())
    assert res.ok is False
    assert "maxFiles" in (res.error or "")


# ---------------------------------------------------------------- discover

async def test_discover_lists_files_in_the_configured_folder() -> None:
    drive = _Drive([{"id": "f1", "name": "Notes", "mimeType": DOC}])
    _install(drive)
    items = await _connector().discover({"folderId": "FOLDER"}, _FakeAuth())
    assert [i.title for i in items] == ["Notes"]
    q = drive.requests[0].url.params["q"]
    assert "'FOLDER' in parents" in q
    assert "trashed = false" in q


async def test_an_empty_folder_id_syncs_everything_readable() -> None:
    drive = _Drive([])
    _install(drive)
    await _connector().discover({}, _FakeAuth())
    assert "in parents" not in drive.requests[0].url.params["q"]


async def test_a_quote_in_the_folder_id_cannot_break_out_of_the_query() -> None:
    """folderId is operator config, but a stray quote would still change the
    meaning of the Drive query, so it is escaped rather than interpolated raw."""
    drive = _Drive([])
    _install(drive)
    await _connector().discover({"folderId": "a' or name = 'x"}, _FakeAuth())
    q = str(drive.requests[0].url.params["q"])
    assert "\\'" in q
    assert "' or name = '" not in q.replace("\\'", "")


# ---------------------------------------------------------------- fetch

async def _collect(connector: Any, config: dict[str, Any], cursor: Any, auth: Any) -> list[Any]:
    return [d async for d in connector.fetch(config, cursor, auth)]


async def test_fetch_exports_a_google_doc_as_text() -> None:
    drive = _Drive(
        [{"id": "f1", "name": "Notes", "mimeType": DOC, "modifiedTime": "2026-01-01T00:00:00Z"}],
        {"f1": "the body of the doc"},
    )
    _install(drive)
    docs = await _collect(_connector(), {}, None, _FakeAuth())
    assert len(docs) == 1
    assert docs[0].content == "the body of the doc"
    assert docs[0].title == "Notes"
    assert docs[0].content_type == "text/plain"
    assert docs[0].metadata["driveFileId"] == "f1"
    export = next(r for r in drive.requests if "export" in r.url.path)
    assert export.url.params["mimeType"] == "text/plain"


async def test_fetch_exports_a_spreadsheet_as_csv() -> None:
    drive = _Drive([{"id": "s1", "name": "Numbers", "mimeType": SHEET}], {"s1": "a,b\n1,2"})
    _install(drive)
    docs = await _collect(_connector(), {}, None, _FakeAuth())
    assert docs[0].content_type == "text/csv"


async def test_fetch_downloads_a_plain_text_file_directly() -> None:
    drive = _Drive([{"id": "t1", "name": "readme.md", "mimeType": "text/markdown"}], {"t1": "# Hi"})
    _install(drive)
    docs = await _collect(_connector(), {}, None, _FakeAuth())
    assert docs[0].content == "# Hi"
    download = [r for r in drive.requests if r.url.params.get("alt") == "media"]
    assert download, "a text file should be downloaded, not exported"


async def test_binary_files_are_skipped_not_ingested_as_garbage() -> None:
    # The binaries MUST have non-empty bodies, otherwise the empty-content guard
    # would exclude them and this test would pass without the mime check ever
    # running -- which is exactly how it passed a mutation that ingested them.
    drive = _Drive(
        [
            {"id": "p1", "name": "scan.pdf", "mimeType": "application/pdf"},
            {"id": "i1", "name": "photo.jpg", "mimeType": "image/jpeg"},
            {"id": "t1", "name": "ok.txt", "mimeType": "text/plain"},
        ],
        {"p1": "%PDF-1.7 binary junk", "i1": "\xff\xd8\xff binary junk", "t1": "real content"},
    )
    _install(drive)
    docs = await _collect(_connector(), {}, None, _FakeAuth())
    assert [d.title for d in docs] == ["ok.txt"]
    # And it must not even have requested the bytes of a type it cannot use.
    assert not [r for r in drive.requests if "/files/p1" in r.url.path]


async def test_empty_files_are_skipped() -> None:
    drive = _Drive([{"id": "e1", "name": "blank.txt", "mimeType": "text/plain"}], {"e1": "   \n"})
    _install(drive)
    assert await _collect(_connector(), {}, None, _FakeAuth()) == []


async def test_already_seen_content_is_not_re_emitted() -> None:
    """The framework's cursor is content-hash based; a second sync of unchanged
    files must produce nothing."""
    drive = _Drive([{"id": "f1", "name": "Notes", "mimeType": DOC}], {"f1": "same body"})
    _install(drive)
    connector = _connector()
    first = await _collect(connector, {}, None, _FakeAuth())
    assert len(first) == 1
    cursor = {"hashes": [first[0].content_hash]}
    assert await _collect(connector, {}, cursor, _FakeAuth()) == []


async def test_fetch_without_auth_raises_rather_than_silently_returning_nothing() -> None:
    with pytest.raises(ConnectorError):
        await _collect(_connector(), {}, None, None)


async def test_a_drive_outage_surfaces_as_a_connector_error() -> None:
    drive = _Drive([])
    drive.status_override = 500
    _install(drive)
    with pytest.raises(ConnectorError):
        await _collect(_connector(), {}, None, _FakeAuth())


async def test_max_files_is_passed_to_the_api_and_honoured() -> None:
    files = [{"id": f"f{i}", "name": f"n{i}", "mimeType": "text/plain"} for i in range(10)]
    drive = _Drive(files, {f"f{i}": json.dumps({"i": i}) for i in range(10)})
    _install(drive)
    docs = await _collect(_connector(), {"maxFiles": 3}, None, _FakeAuth())
    assert len(docs) == 3
    assert int(drive.requests[0].url.params["pageSize"]) == 3


async def test_only_read_requests_are_ever_issued() -> None:
    """The Google grant is drive.readonly; a write would fail anyway, but the
    connector must not attempt one."""
    drive = _Drive([{"id": "f1", "name": "Notes", "mimeType": DOC}], {"f1": "body"})
    _install(drive)
    c = _connector()
    await c.validate({}, _FakeAuth())
    await c.discover({}, _FakeAuth())
    await _collect(c, {}, None, _FakeAuth())
    assert {r.method for r in drive.requests} == {"GET"}


async def test_the_tenant_gate_still_applies_to_this_plugin(
    app_session: Any,
) -> None:
    """Being shipped in the repo grants no privilege: an unentitled tenant still
    cannot resolve it."""
    from oc8.knowledge.connectors.registry import resolve_connector

    _connector()  # load it into this process
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        with pytest.raises(ConnectorError):
            await resolve_connector(db, tenant_id=tenant, type_id="gdrive")


async def test_it_satisfies_the_connector_protocol() -> None:
    """A plugin that merely looks like a connector is not enough -- the
    framework calls it through the Protocol, so it must actually match."""
    from oc8.knowledge.connectors.base import Connector

    assert isinstance(_connector(), Connector)


async def test_a_drive_source_can_actually_be_created_through_the_api(
    app_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: create_source used to call validate() with no AuthContext, so
    a connector needing credentials rejected EVERY source at creation time --
    the Drive plugin was unusable through the API while its unit tests passed."""
    import base64

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    from oc8 import config
    from oc8 import models as m
    from oc8.auth import get_identity_provider
    from oc8.main import create_app

    drive = _Drive([])
    _install(drive)

    tenant = uuid.uuid4()
    tok = get_identity_provider().mint(tenant_id=tenant, subject="op", role="org_admin")
    headers = {"Authorization": f"Bearer {tok}"}

    # A Google connection for the source to hang off.
    conn_id = uuid.uuid4()
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # Install + enable the plugin for this tenant.
            inst = await c.post(
                "/api/v1/capas/install-from-disk",
                json={"pluginId": "gdrive_source"},
                headers=headers,
            )
            assert inst.status_code == 201, inst.text
            en = await c.post(
                f"/api/v1/capas/{inst.json()['pluginId']}/enable",
                json={"grantedPermissions": ["knowledge:write"]},
                headers=headers,
            )
            assert en.status_code in (200, 201), en.text

            # Seed the OAuth connection + a fake token directly.
            async with app_session(tenant) as db:
                db.add(
                    m.OAuthConnection(
                        id=conn_id,
                        tenant_id=tenant,
                        provider="google",
                        account_label="a@example.com",
                        scopes=["https://www.googleapis.com/auth/drive.readonly"],
                        access_secret_ref="oauth/x/access",
                        status="active",
                        client_source="platform",
                    )
                )

            config.get_settings().secret_kek = base64.b64encode(bytes(range(32))).decode()

            async def _tok(*_a: object, **_k: object) -> str:
                return "tok-123"

            monkeypatch.setattr(
                "oc8.knowledge.connectors.context.get_access_token", _tok
            )
            res = await c.post(
                "/api/v1/knowledge/sources",
                json={
                    "connectorType": "gdrive",
                    "name": "Drive",
                    "config": {"folderId": "F1"},
                    "kbId": str(uuid.uuid4()),
                    "oauthConnectionId": str(conn_id),
                },
                headers=headers,
            )

    assert res.status_code == 201, res.text
    assert res.json()["connectorType"] == "gdrive"
    # validate() really did reach Drive with the token.
    assert any(r.headers.get("Authorization") == "Bearer tok-123" for r in drive.requests)
