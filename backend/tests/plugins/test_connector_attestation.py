"""The two connectors willing to swear their listing is complete.

Amendment A1: there is no silent cap. The design originally made truncation
honest -- `Attestation.NONE` when a connector hit its own `maxFiles` -- and left
the cap in place, which meant a Drive folder of 101 files never attested and
deletion propagation was silently off for the ORDINARY case. So enumeration is
unbounded: `attest_listing` follows `nextPageToken` / `NextContinuationToken` to
the end and never consults `maxFiles`. It costs one API page per 1000 objects
and carries no content, which is what makes it affordable where an unbounded
FETCH would not be. `maxFiles` still bounds `fetch()`, because ingestion embeds
and stores and that cost is real -- what changes is that a bounded fetch can no
longer poison anything.

`Attestation.NONE` survives for what it is actually for: an API error, a
timeout, a connector that cannot enumerate.

The contract test in each half is the one that matters most in practice. If
`attest_listing` builds a URI even slightly differently from `fetch` -- bare S3
keys against `s3://bucket/key` -- the listing and the corpus share zero strings
and the source is wiped. The namespace-mismatch refusal catches that at runtime;
this catches it at build time.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from oc8.capas import contributions, loader
from oc8.capas.discovery import find_plugin
from oc8.config import get_settings
from oc8.knowledge.connectors.base import Attestation, ConnectorError
from oc8.oauth import http as oauth_http

pytestmark = pytest.mark.asyncio

PLUGINS_DIR = Path(__file__).resolve().parents[3] / "capas"

DOC = "application/vnd.google-apps.document"

S3_CFG = {
    "bucket": "my-bucket",
    "credential": "test-credential-id",
}


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


def _install(handler: Any) -> None:
    oauth_http.set_transport_override(httpx.MockTransport(handler))


async def _collect(connector: Any, config: dict[str, Any], auth: Any) -> list[Any]:
    return [d async for d in connector.fetch(config, None, auth)]


# ------------------------------------------------------------------ google drive


class _FakeAuth:
    async def token(self) -> str:
        return "tok-123"

    async def secret(self, ref: str) -> str:
        raise ConnectorError("drive uses OAuth, not stored secrets")


class _PagedDrive:
    """A Drive that hands out `page_size` files at a time, like the real one.

    The page size is the fake's own, not the caller's: what is under test is
    that the listing follows every `nextPageToken` rather than stopping when it
    has enough.
    """

    def __init__(self, files: list[dict[str, Any]], *, page_size: int = 2) -> None:
        self.files = files
        self.page_size = page_size
        self.list_calls = 0
        self.status_override: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.status_override is not None:
            return httpx.Response(self.status_override, text="denied")
        path = request.url.path
        if path.endswith("/about"):
            return httpx.Response(200, json={"user": {"emailAddress": "a@b.c"}})
        if path.endswith("/files"):
            self.list_calls += 1
            start = int(request.url.params.get("pageToken", "0") or 0)
            page = self.files[start : start + self.page_size]
            payload: dict[str, Any] = {"files": page}
            if start + self.page_size < len(self.files):
                payload["nextPageToken"] = str(start + self.page_size)
            return httpx.Response(200, json=payload)
        file_id = path.split("/files/")[1].split("/")[0]
        return httpx.Response(200, text=f"the body of {file_id}")


def _drive_connector() -> Any:
    found = find_plugin("gdrive_source")
    assert found is not None, "gdrive_source plugin not discovered"
    assert loader.load_plugin(found) is True
    return contributions.connectors_for("gdrive_source")["gdrive"]


def _drive_files(n: int) -> list[dict[str, Any]]:
    return [{"id": f"f{i}", "name": f"note-{i}", "mimeType": DOC} for i in range(n)]


async def test_gdrive_attests_a_complete_listing(app_session: Any) -> None:
    """A listing it enumerated to the end is AUTHORITATIVE. An API error is not
    a listing at all, and must raise rather than attest an empty set -- a
    revoked grant that returned `AUTHORITATIVE` with no URIs is the wire shape
    of "the customer deleted everything"."""
    drive = _PagedDrive(_drive_files(3))
    _install(drive.handler)
    connector = _drive_connector()

    listing = await connector.attest_listing({"folderId": "F1"}, _FakeAuth())

    assert listing.attestation is Attestation.AUTHORITATIVE
    assert listing.present_uris == frozenset(
        f"https://drive.google.com/file/d/f{i}" for i in range(3)
    )

    denied = _PagedDrive([])
    denied.status_override = 401
    _install(denied.handler)
    with pytest.raises(ConnectorError):
        await _drive_connector().attest_listing({}, _FakeAuth())


async def test_gdrive_attests_every_page_of_a_folder_larger_than_max_files(
    app_session: Any,
) -> None:
    """Amendment A1. Seven files upstream, a `maxFiles` of three.

    The listing must still be AUTHORITATIVE and must contain all seven, or the
    four the fetch never reached would look exactly like four deletions.
    """
    drive = _PagedDrive(_drive_files(7), page_size=2)
    _install(drive.handler)

    listing = await _drive_connector().attest_listing({"maxFiles": 3}, _FakeAuth())

    assert listing.attestation is Attestation.AUTHORITATIVE
    assert len(listing.present_uris) == 7
    assert listing.present_uris == frozenset(
        f"https://drive.google.com/file/d/f{i}" for i in range(7)
    )
    assert drive.list_calls > 1, "it has to have followed a nextPageToken to get there"


async def test_gdrive_attested_uris_match_the_uris_it_fetches(app_session: Any) -> None:
    """The contract: present_uris is a SUPERSET of what fetch stamps.

    A bounded fetch is fine. A differently-built URI is not -- the two sets
    would share no strings and every document in the corpus would read as
    absent from every listing.
    """
    drive = _PagedDrive(_drive_files(7), page_size=2)
    _install(drive.handler)
    connector = _drive_connector()

    listing = await connector.attest_listing({"maxFiles": 3}, _FakeAuth())
    fetched = {d.source_uri for d in await _collect(connector, {"maxFiles": 3}, _FakeAuth())}

    assert fetched, "the fetch must actually have produced documents"
    assert fetched <= listing.present_uris


# ------------------------------------------------------------------ s3


class _Secrets:
    async def token(self) -> str:
        raise ConnectorError("this source has no connected account")

    async def secret(self, ref: str) -> str:
        return {"s3/access": "AKIAEXAMPLE", "s3/secret": "shhh"}[ref]

    async def credential(self, credential_id: str, field_key: str) -> str:
        return {
            ("test-credential-id", "access_key"): "AKIAEXAMPLE",
            ("test-credential-id", "secret_key"): "shhh",
            ("test-credential-id", "region"): "eu-central-1",
            ("test-credential-id", "endpoint"): "",
        }[(credential_id, field_key)]


class _PagedS3:
    """ListObjectsV2 with real continuation tokens."""

    def __init__(self, keys: list[tuple[str, int]], *, page_size: int = 2) -> None:
        self.keys = keys
        self.page_size = page_size
        self.list_calls = 0

    def _xml(self, page: list[tuple[str, int]], next_token: str | None) -> str:
        items = "".join(f"<Contents><Key>{k}</Key><Size>{s}</Size></Contents>" for k, s in page)
        tail = (
            "<IsTruncated>true</IsTruncated>"
            f"<NextContinuationToken>{next_token}</NextContinuationToken>"
            if next_token
            else "<IsTruncated>false</IsTruncated>"
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"{items}{tail}</ListBucketResult>"
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("list-type") == "2":
            self.list_calls += 1
            start = int(request.url.params.get("continuation-token", "0") or 0)
            page = self.keys[start : start + self.page_size]
            nxt = start + self.page_size
            return httpx.Response(
                200, text=self._xml(page, str(nxt) if nxt < len(self.keys) else None)
            )
        key = request.url.path.lstrip("/")
        if key.startswith("my-bucket/"):
            key = key[len("my-bucket/") :]
        return httpx.Response(200, text=f"the body of {key}")


def _s3_connector() -> Any:
    found = find_plugin("s3_source")
    assert found is not None, "s3_source plugin not discovered"
    assert loader.load_plugin(found) is True
    return contributions.connectors_for("s3_source")["s3"]


def _s3_keys(n: int) -> list[tuple[str, int]]:
    return [(f"docs/note-{i}.txt", 11) for i in range(n)]


async def test_s3_attests_every_continuation_page_of_a_bucket_larger_than_max_files(
    app_session: Any,
) -> None:
    """Amendment A1 again, and the reason s3 is in scope at all.

    It was deferred because attesting over a cap-truncated `ListObjectsV2`
    needs honest continuation-token handling -- which is exactly what A1
    requires, so it stopped being a reason to defer.
    """
    s3 = _PagedS3(_s3_keys(7), page_size=2)
    _install(s3.handler)

    listing = await _s3_connector().attest_listing({**S3_CFG, "maxFiles": 3}, _Secrets())

    assert listing.attestation is Attestation.AUTHORITATIVE
    assert listing.present_uris == frozenset(f"s3://my-bucket/docs/note-{i}.txt" for i in range(7))
    assert s3.list_calls > 1, "it has to have followed a NextContinuationToken"


async def test_s3_attested_uris_match_the_uris_it_fetches(app_session: Any) -> None:
    """The bare-key-versus-`s3://bucket/key` trap, pinned."""
    s3 = _PagedS3(_s3_keys(7), page_size=2)
    _install(s3.handler)
    connector = _s3_connector()

    listing = await connector.attest_listing({**S3_CFG, "maxFiles": 3}, _Secrets())
    fetched = {
        d.source_uri for d in await _collect(connector, {**S3_CFG, "maxFiles": 3}, _Secrets())
    }

    assert fetched, "the fetch must actually have produced documents"
    assert fetched <= listing.present_uris
