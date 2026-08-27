"""Task 9 of the Design System Consistency plan: `GET /members` widened to
the uniform search/filter/group/pagination contract (spec §1.1) --
`Page[MemberDTO]` instead of a bare list.

Per the plan's Global Constraint, Members has NO archive/restore -- `Member`
carries no `SoftDeleteMixin` and archive was never requested for this
screen -- so this task is list-query only: `search`, `groupBy`, `limit`,
`offset`. No `includeArchived`.

Mirrors `tests/api/test_departments_archive_and_list_query.py` (Task 7) in
shape and convention: no shared `client`/`tenant_headers` fixtures exist in
this suite, so each test builds its own `AsyncClient` against a fresh
`create_app()` and an `org_admin` bearer token (tenant-wide, so `member:view`
is never the thing under test here).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject="boss", role="org_admin")
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _subject_uuid(subject: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


def _member(tenant: uuid.UUID, subject: str, display_name: str) -> m.OrgMember:
    return m.OrgMember(
        tenant_id=tenant,
        subject=subject,
        subject_uuid=_subject_uuid(subject),
        display_name=display_name,
    )


async def test_list_members_returns_paged_envelope(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add_all(
            [
                _member(tenant, "anna", "Anna Admin"),
                _member(tenant, "ben", "Ben Approver"),
                _member(tenant, "cara", "Cara Contributor"),
            ]
        )
        await db.flush()

    async with _http() as http:
        resp = await http.get("/api/v1/members?limit=1", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "totalCount" in body
        assert isinstance(body["items"], list)
        assert body["totalCount"] == 3
        assert len(body["items"]) <= 1


async def test_list_members_search_matches_display_name(app_session: AppSessionFactory) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add_all(
            [
                _member(tenant, "anna", "Anna Admin"),
                _member(tenant, "ben", "Ben Approver"),
            ]
        )
        await db.flush()

    async with _http() as http:
        resp = await http.get("/api/v1/members?search=Anna", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        names = [mem["displayName"] for mem in body["items"]]
        assert "Anna Admin" in names
        assert "Ben Approver" not in names


async def test_list_members_default_page_shape_has_total_count(
    app_session: AppSessionFactory,
) -> None:
    """The default (unfiltered) list is now `Page[MemberDTO]`, not a bare
    list -- pinned separately from the search/pagination test above so a
    regression to the old bare-list shape fails even when nobody filters or
    paginates."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_member(tenant, "solo", "Solo Member"))
        await db.flush()

    async with _http() as http:
        resp = await http.get("/api/v1/members", headers=_headers(tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["totalCount"] == 1
        assert body["items"][0]["displayName"] == "Solo Member"


async def test_an_unknown_group_by_is_a_refusal_not_a_crash(
    app_session: AppSessionFactory,
) -> None:
    """`groupBy` arrives straight off the query string, so a value the route
    cannot group by is the caller's mistake, not the server's fault.

    Route-level companion to `test_apply_group_order_rejects_unknown_field`:
    that one pins the helper, this one pins what a client actually sees, which
    was a bare 500 until Task 22's live-API sweep found it. Members stands in
    for every list route in the plan's scope -- they all reach the same helper.
    """
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(_member(tenant, "solo", "Solo Member"))
        await db.flush()

    async with _http() as http:
        resp = await http.get("/api/v1/members?groupBy=bogus", headers=_headers(tenant))
        assert resp.status_code == 400, resp.text
        assert "not a groupable field" in resp.json()["detail"]
