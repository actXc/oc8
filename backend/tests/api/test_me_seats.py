"""`GET /me` has to be able to say "you hold no seat".

Today it returns the `Principal` -- five claims and nothing else -- so the two
empty states the workspace screen must distinguish look identical to the
frontend: "nothing is waiting for you" and "nobody has put you in a department
yet". One of those is the system working and the other is a person locked out of
their own job, and a blank screen says neither.

It stays wire-compatible with `CurrentUser` (`src/lib/hooks.ts:196-199`), which
reads `subject` / `role` / `kind` today -- this adds fields, it does not rename
any.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from oc8 import models as m
from oc8.auth import get_identity_provider
from oc8.authz.permissions import SEAT_APPROVER, SEAT_VIEWER
from oc8.main import create_app
from tests.conftest import AppSessionFactory

pytestmark = pytest.mark.asyncio


def _headers(tenant: uuid.UUID, subject: str, role: str) -> dict[str, str]:
    token = get_identity_provider().mint(tenant_id=tenant, subject=subject, role=role)
    return {"Authorization": f"Bearer {token}"}


@asynccontextmanager
async def _http() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c


def _subject_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(subject)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"oc8:subject:{subject}")


async def test_me_names_my_departments_and_whether_i_see_everything(
    app_session: AppSessionFactory,
) -> None:
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        sales = m.Department(tenant_id=tenant, name="Vertrieb")
        marketing = m.Department(tenant_id=tenant, name="Marketing")
        engineering = m.Department(tenant_id=tenant, name="Entwicklung")
        db.add_all([sales, marketing, engineering])
        await db.flush()
        member = m.OrgMember(
            tenant_id=tenant,
            subject="hos",
            subject_uuid=_subject_uuid("hos"),
            display_name="Lena Krüger",
        )
        db.add(member)
        await db.flush()
        db.add_all(
            [
                m.OrgMemberDepartment(
                    tenant_id=tenant,
                    member_id=member.id,
                    department_id=sales.id,
                    seat_role=SEAT_APPROVER,
                ),
                m.OrgMemberDepartment(
                    tenant_id=tenant,
                    member_id=member.id,
                    department_id=marketing.id,
                    seat_role=SEAT_VIEWER,
                ),
            ]
        )
        await db.flush()
        member_id, sales_id, marketing_id = member.id, sales.id, marketing.id

    async with _http() as http:
        got = await http.get("/api/v1/me", headers=_headers(tenant, "hos", "member"))
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()

    # Wire-compatible with what the frontend already reads.
    assert body["subject"] == "hos"
    assert body["role"] == "member"
    assert body["kind"] == "operator"

    assert body["memberId"] == str(member_id)
    assert body["displayName"] == "Lena Krüger", (
        "the header chip hardcodes 'Lena Krüger' today; this is where the real "
        "name has to come from"
    )
    assert body["viewsAllDepartments"] is False

    seats = {s["departmentId"]: s for s in body["seats"]}
    assert set(seats) == {str(sales_id), str(marketing_id)}
    assert seats[str(sales_id)]["seatRole"] == SEAT_APPROVER
    assert seats[str(sales_id)]["departmentName"] == "Vertrieb"
    assert seats[str(marketing_id)]["seatRole"] == SEAT_VIEWER
    assert seats[str(marketing_id)]["departmentName"] == "Marketing", (
        "a seat the screen can only render as a uuid is a subtitle nobody can read"
    )


async def test_me_tells_a_seatless_employee_apart_from_a_quiet_queue(
    app_session: AppSessionFactory,
) -> None:
    """The distinction the whole screen turns on. "Nichts wartet auf dich" and
    "Du bist keiner Abteilung zugeordnet" must not look alike, and the only place
    the frontend can learn which it is showing is here."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.Department(tenant_id=tenant, name="Vertrieb"))
        await db.flush()

    async with _http() as http:
        got = await http.get("/api/v1/me", headers=_headers(tenant, "newjoiner", "member"))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["seats"] == []
    assert body["viewsAllDepartments"] is False
    assert body["memberId"] is not None, (
        "the row is upserted on the first workspace request, which is also what "
        "makes `decided_by` never NULL and lets POST /members pick from subjects "
        "the system has actually seen"
    )


async def test_an_org_admin_says_he_sees_everything_without_listing_seats(
    app_session: AppSessionFactory,
) -> None:
    """He holds no seat and never will. `viewsAllDepartments` is what the screen
    reads to show him the department picker and the Company-wide bucket."""
    tenant = uuid.uuid4()
    async with app_session(tenant) as db:
        db.add(m.Department(tenant_id=tenant, name="Vertrieb"))
        await db.flush()

    async with _http() as http:
        got = await http.get("/api/v1/me", headers=_headers(tenant, "boss", "org_admin"))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["viewsAllDepartments"] is True
    assert body["seats"] == []
